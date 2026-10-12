"""Мастер «Запланировать» и раздел «Запланированные»."""
from __future__ import annotations

import html
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.schedule_keyboard import (
    get_schedule_confirm_keyboard,
    get_schedule_day_keyboard,
    get_schedule_time_keyboard,
    get_scheduled_list_keyboard,
    get_scheduled_post_actions_keyboard,
)
from app.bot.utils.post_success_ui import post_media_counts
from app.utils.time_msk import (
    format_schedule_msk,
    msk_now,
    parse_msk_date,
    parse_msk_time,
    parse_schedule_msk_to_naive_utc,
)

logger = logging.getLogger(__name__)
router = Router()


class SchedulePost(StatesGroup):
    waiting_custom_date = State()
    waiting_custom_time = State()


def _orchestrator(bot):
    from app.bot.handlers.scheduler import get_orchestrator

    return get_orchestrator(bot)


async def _answer_quietly(
    callback: CallbackQuery, text: str = "", *, show_alert: bool = False
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.debug("callback.answer failed: %s", e)


async def _safe_edit(message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    from app.bot.handlers.scheduler import safe_edit_message

    return await safe_edit_message(
        message, text, reply_markup=reply_markup, parse_mode=parse_mode
    )


def _is_reschedule(data: str) -> bool:
    return data.startswith("sched_rs_")


def _strip_schedule_prefix(data: str) -> str:
    if data.startswith("sched_rs_"):
        return data[len("sched_rs_") :]
    if data.startswith("sched_"):
        return data[len("sched_") :]
    return data


def _parse_post_suffix(rest: str, marker: str) -> Optional[str]:
    """rest вида '{marker}_{post_id}' → post_id."""
    prefix = marker + "_"
    if not rest.startswith(prefix):
        return None
    post_id = rest[len(prefix) :]
    return post_id or None


async def _load_post(post_id: str) -> Optional[dict]:
    from app.bot.handlers.post_management import get_post_api

    return await get_post_api(post_id)


def _day_prompt(post_name: str, *, reschedule: bool) -> str:
    title = "Перенести публикацию" if reschedule else "Запланировать публикацию"
    name = html.escape(post_name or "Без названия")
    return (
        f"📅 <b>{title}</b>\n\n"
        f"📝 {name}\n\n"
        "Выберите день (время — <b>МСК</b>):"
    )


def _time_prompt(post_name: str, day: date, *, reschedule: bool) -> str:
    title = "Перенести" if reschedule else "Запланировать"
    name = html.escape(post_name or "Без названия")
    return (
        f"📅 <b>{title}</b>\n\n"
        f"📝 {name}\n"
        f"📆 День: <b>{day.strftime('%d.%m.%Y')}</b> (МСК)\n\n"
        "Выберите время:"
    )


def _confirm_prompt(post_name: str, scheduled_at) -> str:
    name = html.escape(post_name or "Без названия")
    when = format_schedule_msk(scheduled_at)
    return (
        f"📅 <b>Подтверждение</b>\n\n"
        f"📝 {name}\n"
        f"🕐 {html.escape(when)}\n\n"
        "Запланировать публикацию?"
    )


async def start_schedule_wizard(
    callback: CallbackQuery,
    state: FSMContext,
    post_id: str,
    *,
    reschedule: bool = False,
) -> None:
    post = await _load_post(post_id)
    if not post:
        await _answer_quietly(callback, "❌ Пост не найден", show_alert=True)
        return
    await state.set_state(None)
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=post.get("name") or "",
        schedule_day=None,
        schedule_hour=None,
        schedule_minute=None,
    )
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        _day_prompt(post.get("name") or "", reschedule=reschedule),
        reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
    )


# --- Entry: Запланировать ---


@router.callback_query(F.data.startswith("schedule_post_"))
async def schedule_post_entry(callback: CallbackQuery, state: FSMContext):
    post_id = callback.data.replace("schedule_post_", "", 1)
    if not post_id:
        await _answer_quietly(callback, "❌ Нет поста", show_alert=True)
        return
    await start_schedule_wizard(callback, state, post_id, reschedule=False)


@router.callback_query(F.data == "schedule_selected_post")
async def schedule_selected_post_entry(callback: CallbackQuery, state: FSMContext):
    user_data = getattr(callback.bot, "user_data", {}).get(callback.from_user.id, {})
    post_id = user_data.get("selected_post")
    if not post_id:
        await _answer_quietly(callback, "❌ Пост не выбран", show_alert=True)
        return
    await start_schedule_wizard(callback, state, post_id, reschedule=False)


@router.callback_query(F.data.startswith("sched_rs_start_"))
async def reschedule_entry(callback: CallbackQuery, state: FSMContext):
    post_id = callback.data.replace("sched_rs_start_", "", 1)
    if not post_id:
        await _answer_quietly(callback, "❌ Нет поста", show_alert=True)
        return
    await start_schedule_wizard(callback, state, post_id, reschedule=True)


# --- Day ---


@router.callback_query(
    F.data.regexp(r"^sched(_rs)?_day_\d+_.+")
)
async def schedule_day_offset(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    # day_{n}_{post_id}
    parts = rest.split("_", 2)
    if len(parts) < 3 or parts[0] != "day":
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    try:
        offset = int(parts[1])
    except ValueError:
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    post_id = parts[2]
    day = msk_now().date() + timedelta(days=offset)
    data = await state.get_data()
    name = data.get("schedule_post_name") or ""
    if data.get("schedule_post_id") != post_id:
        post = await _load_post(post_id)
        name = (post or {}).get("name") or ""
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=name,
        schedule_day=day.isoformat(),
    )
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        _time_prompt(name, day, reschedule=reschedule),
        reply_markup=get_schedule_time_keyboard(post_id, reschedule=reschedule),
    )


@router.callback_query(F.data.regexp(r"^sched(_rs)?_day_custom_.+"))
async def schedule_day_custom(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    post_id = _parse_post_suffix(rest, "day_custom")
    if not post_id:
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    data = await state.get_data()
    name = data.get("schedule_post_name") or ""
    if data.get("schedule_post_id") != post_id:
        post = await _load_post(post_id)
        name = (post or {}).get("name") or ""
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=name,
    )
    await state.set_state(SchedulePost.waiting_custom_date)
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        f"📅 Введите дату в формате <b>ДД.ММ</b> или <b>ДД.ММ.ГГГГ</b> (МСК)\n"
        f"📝 {html.escape(name or 'Без названия')}",
        reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
    )


@router.message(StateFilter(SchedulePost.waiting_custom_date))
async def schedule_custom_date_message(message: Message, state: FSMContext):
    data = await state.get_data()
    post_id = data.get("schedule_post_id")
    reschedule = bool(data.get("schedule_reschedule"))
    name = data.get("schedule_post_name") or ""
    if not post_id:
        await state.clear()
        await message.answer("❌ Сессия планирования сброшена")
        return
    try:
        day = parse_msk_date(message.text or "")
    except ValueError:
        await message.answer("❌ Некорректная дата. Пример: 13.10 или 13.10.2026")
        return
    await state.set_state(None)
    await state.update_data(schedule_day=day.isoformat())
    await message.answer(
        _time_prompt(name, day, reschedule=reschedule),
        reply_markup=get_schedule_time_keyboard(post_id, reschedule=reschedule),
        parse_mode="HTML",
    )


# --- Time ---


@router.callback_query(F.data.regexp(r"^sched(_rs)?_time_\d{3,4}_.+"))
async def schedule_time_preset(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    # time_HHMM_post_id
    if not rest.startswith("time_"):
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    body = rest[len("time_") :]
    hhmm, _, post_id = body.partition("_")
    if not post_id or not hhmm.isdigit() or len(hhmm) not in (3, 4):
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    hhmm = hhmm.zfill(4)
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    await _finish_time_choice(
        callback, state, post_id, hour, minute, reschedule=reschedule
    )


@router.callback_query(F.data.regexp(r"^sched(_rs)?_time_custom_.+"))
async def schedule_time_custom(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    post_id = _parse_post_suffix(rest, "time_custom")
    if not post_id:
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    data = await state.get_data()
    name = data.get("schedule_post_name") or ""
    day_s = data.get("schedule_day")
    await state.update_data(
        schedule_post_id=post_id, schedule_reschedule=reschedule
    )
    await state.set_state(SchedulePost.waiting_custom_time)
    day_line = ""
    if day_s:
        day_line = f"📆 День: <b>{day_s[8:10]}.{day_s[5:7]}.{day_s[:4]}</b>\n"
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        f"🕐 Введите время в формате <b>ЧЧ:ММ</b> (МСК)\n"
        f"📝 {html.escape(name or 'Без названия')}\n"
        f"{day_line}",
        reply_markup=get_schedule_time_keyboard(post_id, reschedule=reschedule),
    )


@router.message(StateFilter(SchedulePost.waiting_custom_time))
async def schedule_custom_time_message(message: Message, state: FSMContext):
    data = await state.get_data()
    post_id = data.get("schedule_post_id")
    reschedule = bool(data.get("schedule_reschedule"))
    if not post_id:
        await state.clear()
        await message.answer("❌ Сессия планирования сброшена")
        return
    try:
        t = parse_msk_time(message.text or "")
    except ValueError:
        await message.answer("❌ Некорректное время. Пример: 18:30")
        return
    await state.set_state(None)
    # Эмулируем CallbackQuery-подобный confirm через message
    day_s = data.get("schedule_day")
    name = data.get("schedule_post_name") or ""
    if not day_s:
        await message.answer("❌ Сначала выберите день")
        await message.answer(
            _day_prompt(name, reschedule=reschedule),
            reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
            parse_mode="HTML",
        )
        return
    day = date.fromisoformat(day_s)
    try:
        scheduled_at = parse_schedule_msk_to_naive_utc(day, t.hour, t.minute)
    except ValueError as exc:
        await message.answer(f"❌ {exc}")
        return
    await state.update_data(
        schedule_hour=t.hour,
        schedule_minute=t.minute,
        schedule_at_iso=scheduled_at.replace(tzinfo=timezone.utc).isoformat(),
    )
    await message.answer(
        _confirm_prompt(name, scheduled_at),
        reply_markup=get_schedule_confirm_keyboard(post_id, reschedule=reschedule),
        parse_mode="HTML",
    )


async def _finish_time_choice(
    callback: CallbackQuery,
    state: FSMContext,
    post_id: str,
    hour: int,
    minute: int,
    *,
    reschedule: bool,
) -> None:
    data = await state.get_data()
    name = data.get("schedule_post_name") or ""
    day_s = data.get("schedule_day")
    if data.get("schedule_post_id") != post_id:
        post = await _load_post(post_id)
        name = (post or {}).get("name") or name
    if not day_s:
        await _answer_quietly(callback, "❌ Сначала выберите день", show_alert=True)
        await _safe_edit(
            callback.message,
            _day_prompt(name, reschedule=reschedule),
            reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
        )
        return
    day = date.fromisoformat(day_s)
    try:
        scheduled_at = parse_schedule_msk_to_naive_utc(day, hour, minute)
    except ValueError as exc:
        await _answer_quietly(callback, f"❌ {exc}", show_alert=True)
        return
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=name,
        schedule_hour=hour,
        schedule_minute=minute,
        schedule_at_iso=scheduled_at.replace(tzinfo=timezone.utc).isoformat(),
    )
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        _confirm_prompt(name, scheduled_at),
        reply_markup=get_schedule_confirm_keyboard(post_id, reschedule=reschedule),
    )


# --- Confirm / cancel / back ---


@router.callback_query(F.data.regexp(r"^sched(_rs)?_confirm_.+"))
async def schedule_confirm(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    post_id = _parse_post_suffix(rest, "confirm")
    if not post_id:
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    data = await state.get_data()
    iso = data.get("schedule_at_iso")
    name = data.get("schedule_post_name") or ""
    if not iso or data.get("schedule_post_id") != post_id:
        await _answer_quietly(callback, "❌ Сессия устарела, выберите снова", show_alert=True)
        await start_schedule_wizard(callback, state, post_id, reschedule=reschedule)
        return
    scheduled_at = datetime.fromisoformat(iso)
    if scheduled_at.tzinfo is not None:
        scheduled_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)

    orch = _orchestrator(callback.bot)
    try:
        if reschedule or bool(data.get("schedule_reschedule")):
            ok = orch.reschedule_post(post_id, scheduled_at)
            if not ok:
                # Нет записей в очереди — создаём заново
                ok = orch.schedule_post(post_id, scheduled_at)
        else:
            ok = orch.schedule_post(post_id, scheduled_at)
    except Exception as e:
        logger.exception("schedule confirm failed: %s", e)
        await _answer_quietly(callback, "❌ Ошибка планирования", show_alert=True)
        return

    await state.clear()
    if not ok:
        await _answer_quietly(
            callback,
            "❌ Не удалось запланировать (проверьте включённые платформы)",
            show_alert=True,
        )
        return

    when = format_schedule_msk(scheduled_at)
    await _answer_quietly(callback, "✅ Запланировано")
    await _safe_edit(
        callback.message,
        f"✅ <b>Публикация запланирована</b>\n\n"
        f"📝 {html.escape(name or 'Без названия')}\n"
        f"🕐 {html.escape(when)}\n\n"
        "Пост появится в разделе «Запланированные».",
        reply_markup=get_scheduled_post_actions_keyboard(post_id),
    )


@router.callback_query(F.data.regexp(r"^sched(_rs)?_back_day_.+"))
async def schedule_back_day(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    post_id = _parse_post_suffix(rest, "back_day")
    if not post_id:
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    data = await state.get_data()
    name = data.get("schedule_post_name") or ""
    await state.set_state(None)
    await state.update_data(
        schedule_day=None, schedule_hour=None, schedule_minute=None, schedule_at_iso=None
    )
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        _day_prompt(name, reschedule=reschedule),
        reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
    )


@router.callback_query(F.data.regexp(r"^sched(_rs)?_cancel_.+"))
async def schedule_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _answer_quietly(callback, "Отменено")
    from app.bot.utils.main_menu import show_home

    await show_home(
        callback.message,
        callback.bot,
        edit=True,
        user_id=callback.from_user.id,
    )


# --- Scheduled list ---


async def _render_scheduled_menu(callback: CallbackQuery) -> None:
    orch = _orchestrator(callback.bot)
    items = orch.list_scheduled_posts()
    if not items:
        text = "📅 <b>Запланированные</b>\n\nПока нет отложенных публикаций."
    else:
        lines = ["📅 <b>Запланированные</b>", ""]
        for i, item in enumerate(items[:15], 1):
            name = html.escape(item.post_name or f"Пост {item.post_id[:8]}")
            when = format_schedule_msk(item.scheduled_at) if item.scheduled_at else "—"
            plats = ", ".join(item.platforms) if item.platforms else "—"
            lines.append(f"{i}. {name}")
            lines.append(f"   🕐 {html.escape(when)}")
            lines.append(f"   📡 {html.escape(plats)}")
        if len(items) > 15:
            lines.append(f"\n… и ещё {len(items) - 15}")
        text = "\n".join(lines)
    await _safe_edit(
        callback.message,
        text,
        reply_markup=get_scheduled_list_keyboard(items),
    )


@router.callback_query(F.data == "scheduled_menu")
async def scheduled_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _answer_quietly(callback)
    await _render_scheduled_menu(callback)


@router.callback_query(F.data.startswith("scheduled_post_"))
async def scheduled_post_card(callback: CallbackQuery, state: FSMContext):
    post_id = callback.data.replace("scheduled_post_", "", 1)
    post = await _load_post(post_id)
    if not post:
        await _answer_quietly(callback, "❌ Пост не найден", show_alert=True)
        return
    orch = _orchestrator(callback.bot)
    items = {i.post_id: i for i in orch.list_scheduled_posts()}
    item = items.get(post_id)
    name = html.escape(post.get("name") or "Без названия")
    photo_count, video_count = post_media_counts(post)
    media_parts = []
    if photo_count:
        media_parts.append(f"📷 {photo_count}")
    if video_count:
        media_parts.append(f"📹 {video_count}")
    media = " · ".join(media_parts) if media_parts else "без медиа"
    when = format_schedule_msk(item.scheduled_at) if item and item.scheduled_at else "—"
    plats = ", ".join(item.platforms) if item and item.platforms else "—"
    text = (
        f"📅 <b>Запланированный пост</b>\n\n"
        f"📝 {name}\n"
        f"{media}\n"
        f"🕐 {html.escape(when)}\n"
        f"📡 {html.escape(plats)}"
    )
    await state.update_data(
        schedule_post_id=post_id,
        schedule_post_name=post.get("name") or "",
        schedule_reschedule=True,
    )
    # для открытия поста из черновиков-флоу
    if not hasattr(callback.bot, "user_data"):
        callback.bot.user_data = {}
    callback.bot.user_data.setdefault(callback.from_user.id, {})["selected_post"] = post_id
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        text,
        reply_markup=get_scheduled_post_actions_keyboard(post_id),
    )


@router.callback_query(F.data.startswith("scheduled_promote_"))
async def scheduled_promote(callback: CallbackQuery, state: FSMContext):
    post_id = callback.data.replace("scheduled_promote_", "", 1)
    orch = _orchestrator(callback.bot)
    ok = orch.promote_scheduled_now(post_id)
    if not ok:
        await _answer_quietly(callback, "❌ Не удалось", show_alert=True)
        return
    await state.clear()
    await _answer_quietly(callback, "✅ В очереди сейчас")
    await _render_scheduled_menu(callback)


@router.callback_query(F.data.startswith("scheduled_cancel_"))
async def scheduled_cancel(callback: CallbackQuery, state: FSMContext):
    post_id = callback.data.replace("scheduled_cancel_", "", 1)
    orch = _orchestrator(callback.bot)
    ok = orch.cancel_scheduled_post(post_id)
    if not ok:
        await _answer_quietly(callback, "❌ Не удалось отменить", show_alert=True)
        return
    await state.clear()
    await _answer_quietly(callback, "✅ Отменено — пост в черновиках")
    await _render_scheduled_menu(callback)
