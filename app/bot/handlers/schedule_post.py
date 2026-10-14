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
    get_schedule_hour_keyboard,
    get_schedule_minute_keyboard,
    get_scheduled_list_keyboard,
    get_scheduled_post_actions_keyboard,
)
from app.bot.utils.post_edit_flow import format_platforms_column_html
from app.bot.utils.post_success_ui import post_media_counts
from app.utils.time_msk import (
    SCHEDULE_INPUT_HINT_HTML,
    format_schedule_confirm_msk,
    format_schedule_human_msk,
    msk_now,
    parse_schedule_flexible,
    parse_schedule_msk_to_naive_utc,
)

logger = logging.getLogger(__name__)
router = Router()


class SchedulePost(StatesGroup):
    waiting_input = State()
    # Алиасы для совместимости со старыми сессиями в памяти.
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


def _day_prompt(post_name: str, *, reschedule: bool, time_hint: str | None = None) -> str:
    title = "Перенести публикацию" if reschedule else "Запланировать публикацию"
    name = html.escape(post_name or "Без названия")
    lines = [
        f"📅 <b>{title}</b>",
        "",
        f"📝 {name}",
    ]
    if time_hint:
        lines.extend(["", f"🕐 Время: <b>{html.escape(time_hint)}</b> — выберите день"])
    lines.extend(
        [
            "",
            "Выберите день или введите дату/время сообщением.",
            f"<i>{SCHEDULE_INPUT_HINT_HTML}</i>",
        ]
    )
    return "\n".join(lines)


def _hour_prompt(post_name: str, day: date, *, reschedule: bool) -> str:
    title = "Перенести" if reschedule else "Запланировать"
    name = html.escape(post_name or "Без названия")
    return (
        f"📅 <b>{title}</b>\n\n"
        f"📝 {name}\n"
        f"📆 День: <b>{day.strftime('%d.%m.%Y')}</b> (МСК)\n\n"
        "Выберите час (или введите время, напр. <code>1005</code>):"
    )


def _minute_prompt(post_name: str, day: date, hour: int, *, reschedule: bool) -> str:
    title = "Перенести" if reschedule else "Запланировать"
    name = html.escape(post_name or "Без названия")
    return (
        f"📅 <b>{title}</b>\n\n"
        f"📝 {name}\n"
        f"📆 День: <b>{day.strftime('%d.%m.%Y')}</b> · час <b>{hour:02d}</b>\n\n"
        "Выберите минуты:"
    )


def _time_prompt(post_name: str, day: date, *, reschedule: bool) -> str:
    return _hour_prompt(post_name, day, reschedule=reschedule)


def _confirm_prompt(post_name: str, scheduled_at) -> str:
    name = html.escape(post_name or "Без названия")
    when = format_schedule_confirm_msk(scheduled_at)
    return (
        f"📅 <b>Подтверждение</b>\n\n"
        f"📝 {name}\n"
        f"🕐 <b>{html.escape(when)}</b>\n\n"
        "Запланировать публикацию?"
    )


async def _goto_confirm(
    *,
    state: FSMContext,
    post_id: str,
    name: str,
    day: date,
    hour: int,
    minute: int,
    reschedule: bool,
    reply,
) -> None:
    try:
        scheduled_at = parse_schedule_msk_to_naive_utc(day, hour, minute)
    except ValueError as exc:
        await reply(f"❌ {exc}")
        return
    await state.set_state(SchedulePost.waiting_input)
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=name,
        schedule_day=day.isoformat(),
        schedule_hour=hour,
        schedule_minute=minute,
        schedule_at_iso=scheduled_at.replace(tzinfo=timezone.utc).isoformat(),
    )
    await reply(
        _confirm_prompt(name, scheduled_at),
        reply_markup=get_schedule_confirm_keyboard(post_id, reschedule=reschedule),
        parse_mode="HTML",
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
    await state.set_state(SchedulePost.waiting_input)
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=post.get("name") or "",
        schedule_day=None,
        schedule_hour=None,
        schedule_minute=None,
        schedule_at_iso=None,
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
    hour = data.get("schedule_hour")
    minute = data.get("schedule_minute")
    await state.set_state(SchedulePost.waiting_input)
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=name,
        schedule_day=day.isoformat(),
    )
    await _answer_quietly(callback)
    if hour is not None and minute is not None:
        async def _reply(text, **kwargs):
            await _safe_edit(callback.message, text, **kwargs)

        await _goto_confirm(
            state=state,
            post_id=post_id,
            name=name,
            day=day,
            hour=int(hour),
            minute=int(minute),
            reschedule=reschedule,
            reply=_reply,
        )
        return
    await _safe_edit(
        callback.message,
        _hour_prompt(name, day, reschedule=reschedule),
        reply_markup=get_schedule_hour_keyboard(post_id, reschedule=reschedule),
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
    time_hint = None
    if data.get("schedule_hour") is not None and data.get("schedule_minute") is not None:
        time_hint = f"{int(data['schedule_hour']):02d}:{int(data['schedule_minute']):02d}"
    await state.set_state(SchedulePost.waiting_input)
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=name,
    )
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        _day_prompt(name, reschedule=reschedule, time_hint=time_hint),
        reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
    )


@router.message(
    StateFilter(
        SchedulePost.waiting_input,
        SchedulePost.waiting_custom_date,
        SchedulePost.waiting_custom_time,
    )
)
async def schedule_input_message(message: Message, state: FSMContext):
    data = await state.get_data()
    post_id = data.get("schedule_post_id")
    reschedule = bool(data.get("schedule_reschedule"))
    name = data.get("schedule_post_name") or ""
    if not post_id:
        await state.clear()
        await message.answer("❌ Сессия планирования сброшена")
        return

    day_s = data.get("schedule_day")
    prefer_time = bool(day_s)
    try:
        day_parsed, t_parsed = parse_schedule_flexible(
            message.text or "", prefer_time=prefer_time
        )
    except ValueError:
        await message.answer(
            "❌ Не понял ввод.\n\n" + SCHEDULE_INPUT_HINT_HTML,
            parse_mode="HTML",
        )
        await state.set_state(SchedulePost.waiting_input)
        return

    day = date.fromisoformat(day_s) if day_s else day_parsed
    if day_parsed is not None:
        day = day_parsed

    hour = data.get("schedule_hour")
    minute = data.get("schedule_minute")
    if t_parsed is not None:
        hour, minute = t_parsed.hour, t_parsed.minute

    await state.set_state(SchedulePost.waiting_input)

    if day is not None and hour is not None and minute is not None:
        await _goto_confirm(
            state=state,
            post_id=post_id,
            name=name,
            day=day,
            hour=int(hour),
            minute=int(minute),
            reschedule=reschedule,
            reply=message.answer,
        )
        return

    if day is not None and (hour is None or minute is None):
        await state.update_data(schedule_day=day.isoformat())
        await message.answer(
            _hour_prompt(name, day, reschedule=reschedule),
            reply_markup=get_schedule_hour_keyboard(post_id, reschedule=reschedule),
            parse_mode="HTML",
        )
        return

    if t_parsed is not None and day is None:
        await state.update_data(schedule_hour=hour, schedule_minute=minute)
        time_hint = f"{int(hour):02d}:{int(minute):02d}"
        await message.answer(
            _day_prompt(name, reschedule=reschedule, time_hint=time_hint),
            reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
            parse_mode="HTML",
        )
        return

    await message.answer(
        "❌ Не понял ввод.\n\n" + SCHEDULE_INPUT_HINT_HTML,
        parse_mode="HTML",
    )


# --- Time: hour → minutes ---


@router.callback_query(F.data.regexp(r"^sched(_rs)?_hour_\d{2}_.+"))
async def schedule_hour_choice(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    # hour_HH_post_id
    if not rest.startswith("hour_"):
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    body = rest[len("hour_") :]
    hh, _, post_id = body.partition("_")
    if not post_id or not hh.isdigit():
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    hour = int(hh)
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
    await state.set_state(SchedulePost.waiting_input)
    await state.update_data(
        schedule_post_id=post_id,
        schedule_reschedule=reschedule,
        schedule_post_name=name,
        schedule_pick_hour=hour,
    )
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        _minute_prompt(name, day, hour, reschedule=reschedule),
        reply_markup=get_schedule_minute_keyboard(post_id, hour, reschedule=reschedule),
    )


@router.callback_query(F.data.regexp(r"^sched(_rs)?_min_\d{2}_.+"))
async def schedule_minute_choice(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    if not rest.startswith("min_"):
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    body = rest[len("min_") :]
    mm, _, post_id = body.partition("_")
    if not post_id or not mm.isdigit():
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    minute = int(mm)
    data = await state.get_data()
    hour = data.get("schedule_pick_hour")
    if hour is None:
        await _answer_quietly(callback, "❌ Сначала выберите час", show_alert=True)
        return
    await _finish_time_choice(
        callback, state, post_id, int(hour), minute, reschedule=reschedule
    )


@router.callback_query(F.data.regexp(r"^sched(_rs)?_back_hour_.+"))
async def schedule_back_hour(callback: CallbackQuery, state: FSMContext):
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
    post_id = _parse_post_suffix(rest, "back_hour")
    if not post_id:
        await _answer_quietly(callback, "❌ Ошибка", show_alert=True)
        return
    data = await state.get_data()
    name = data.get("schedule_post_name") or ""
    day_s = data.get("schedule_day")
    await state.set_state(SchedulePost.waiting_input)
    await state.update_data(schedule_pick_hour=None)
    await _answer_quietly(callback)
    if not day_s:
        await _safe_edit(
            callback.message,
            _day_prompt(name, reschedule=reschedule),
            reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
        )
        return
    day = date.fromisoformat(day_s)
    await _safe_edit(
        callback.message,
        _hour_prompt(name, day, reschedule=reschedule),
        reply_markup=get_schedule_hour_keyboard(post_id, reschedule=reschedule),
    )


@router.callback_query(F.data.regexp(r"^sched(_rs)?_time_\d{3,4}_.+"))
async def schedule_time_preset_legacy(callback: CallbackQuery, state: FSMContext):
    """Старые сообщения с пресетами ЧЧММ — сразу к подтверждению."""
    reschedule = _is_reschedule(callback.data)
    rest = _strip_schedule_prefix(callback.data)
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
    await state.set_state(SchedulePost.waiting_input)
    day_line = ""
    if day_s:
        day_line = f"📆 День: <b>{day_s[8:10]}.{day_s[5:7]}.{day_s[:4]}</b>\n"
    await _answer_quietly(callback)
    await _safe_edit(
        callback.message,
        f"🕐 Введите время (МСК): <b>1610</b> или <b>16:10</b>\n"
        f"📝 {html.escape(name or 'Без названия')}\n"
        f"{day_line}",
        reply_markup=get_schedule_hour_keyboard(post_id, reschedule=reschedule),
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
        await state.set_state(SchedulePost.waiting_input)
        await state.update_data(schedule_hour=hour, schedule_minute=minute)
        await _answer_quietly(callback)
        await _safe_edit(
            callback.message,
            _day_prompt(
                name,
                reschedule=reschedule,
                time_hint=f"{hour:02d}:{minute:02d}",
            ),
            reply_markup=get_schedule_day_keyboard(post_id, reschedule=reschedule),
        )
        return
    day = date.fromisoformat(day_s)
    await _answer_quietly(callback)

    async def _reply(text, **kwargs):
        await _safe_edit(callback.message, text, **kwargs)

    await _goto_confirm(
        state=state,
        post_id=post_id,
        name=name,
        day=day,
        hour=hour,
        minute=minute,
        reschedule=reschedule,
        reply=_reply,
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

    when = format_schedule_confirm_msk(scheduled_at)
    await _answer_quietly(callback, "✅ Запланировано")
    await _safe_edit(
        callback.message,
        f"✅ <b>Публикация запланирована</b>\n\n"
        f"📝 {html.escape(name or 'Без названия')}\n"
        f"🕐 <b>{html.escape(when)}</b>\n\n"
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
    await state.set_state(SchedulePost.waiting_input)
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
            when = (
                format_schedule_human_msk(item.scheduled_at)
                if item.scheduled_at
                else "—"
            )
            lines.append(f"{i}. {name}")
            lines.append(f"🕐 <b>{html.escape(when)}</b>")
            plats_col = format_platforms_column_html(getattr(item, "platforms", None))
            if plats_col:
                lines.append(plats_col)
            lines.append("")
        if len(items) > 15:
            lines.append(f"… и ещё {len(items) - 15}")
        text = "\n".join(lines).rstrip()
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
    when = (
        format_schedule_human_msk(item.scheduled_at)
        if item and item.scheduled_at
        else "—"
    )
    plats_col = format_platforms_column_html(
        item.platforms if item else None
    )
    text_parts = [
        "📅 <b>Запланированный пост</b>",
        "",
        f"📝 {name}",
        media,
        f"🕐 <b>{html.escape(when)}</b>",
    ]
    if plats_col:
        text_parts.extend(["", plats_col])
    text = "\n".join(text_parts)
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
