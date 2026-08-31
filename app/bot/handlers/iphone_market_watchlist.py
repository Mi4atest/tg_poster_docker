"""Управляемый список автообновления оценки рынка Avito."""
from __future__ import annotations

import html
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.keyboards.iphone_market_watchlist_keyboard import (
    watchlist_confirm_delete_keyboard,
    watchlist_from_result_keyboard,
    watchlist_import_keyboard,
    watchlist_item_keyboard,
    watchlist_item_label,
    watchlist_main_keyboard,
    watchlist_suggest_keyboard,
)
from app.services.iphone_market_price_service import (
    MarketTemporarilyUnavailable,
    user_facing_market_error,
)
from app.services.iphone_market_watchlist_service import get_iphone_market_watchlist_service
from app.services.settings_service import get_settings_service
from app.utils.iphone_parser import get_model_display_name


router = Router()
_MSK = ZoneInfo("Europe/Moscow")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _fmt_msk(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_MSK).strftime("%d.%m %H:%M")


def _tier_label(tier: str) -> str:
    return "🔥 24 ч" if tier != "slow" else "🕐 72 ч"


def _selected_ids(data: dict) -> set[int]:
    raw = data.get("wl_import_selected") or []
    result: set[int] = set()
    for item in raw:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


async def _show_list(callback: CallbackQuery, *, page: int = 0) -> None:
    service = get_iphone_market_watchlist_service()
    rows = await service.list_items()
    settings = get_settings_service()
    enabled = settings.is_avito_market_watchlist_enabled()
    pause = settings.get_avito_market_watchlist_pause_until()
    daily = sum(1 for row in rows if row.get("tier") != "slow")
    slow = len(rows) - daily
    lines = [
        "📋 <b>Список автообновления</b>",
        "",
        f"Автообновление: {'вкл' if enabled else 'выкл'} · {len(rows)} поз. "
        f"(🔥 {daily} / 🕐 {slow})",
    ]
    if pause and pause > _utcnow():
        lines.append(f"Пауза после ограничения Avito до {_fmt_msk(pause)} МСК.")
    else:
        due = [
            row
            for row in rows
            if row.get("enabled")
            and (row.get("next_refresh_at") is None or row["next_refresh_at"] <= _utcnow())
        ]
        if due:
            lines.append(f"К обновлению сейчас: {len(due)}.")
        elif rows:
            nxt = min(
                (row["next_refresh_at"] for row in rows if row.get("next_refresh_at")),
                default=None,
            )
            if nxt:
                lines.append(f"Следующее обновление: {_fmt_msk(nxt)} МСК.")
    lines.extend(
        [
            "",
            "🔥 — раз в сутки, 🕐 — раз в 72 часа.",
            "Добавьте конфигурации из отчётов или каталога б/у.",
        ]
    )
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=watchlist_main_keyboard(rows, page=page),
    )
    await callback.answer()


@router.callback_query(F.data == "avito_market_wl")
async def watchlist_home(callback: CallbackQuery, state: FSMContext):
    await state.update_data(wl_import_selected=[], wl_import_page=0, wl_sug_page=0)
    await _show_list(callback, page=0)


@router.callback_query(F.data.startswith("avito_market_wl:p:"))
async def watchlist_page(callback: CallbackQuery):
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _show_list(callback, page=page)


async def _show_item(callback: CallbackQuery, item: dict) -> None:
    name = html.escape(watchlist_item_label(item, with_price=False))
    median_text = "—"
    if item.get("median_rub") is not None:
        median_text = f"{int(item['median_rub']):,}".replace(",", " ") + " ₽"
    lines = [
        f"📊 <b>{name}</b>",
        f"Интервал: {_tier_label(str(item.get('tier') or 'daily'))}",
        f"Статус: {'вкл' if item.get('enabled') else 'выкл'}",
        f"Медиана: {median_text}",
        f"Данные: {_fmt_msk(item.get('fetched_at'))} МСК",
        f"След. автообновление: {_fmt_msk(item.get('next_refresh_at'))} МСК",
    ]
    from app.bot.handlers.iphone_market_price import load_market_daily_points
    from app.bot.utils.market_daily_formatter import format_market_daily_html
    from app.utils.iphone_market_query import IphoneMarketQuery

    daily_html = ""
    try:
        query = IphoneMarketQuery(
            model=str(item.get("model") or ""),
            memory_gb=int(item.get("memory_gb") or 0),
        )
        daily_html = format_market_daily_html(await load_market_daily_points(query))
    except Exception:
        daily_html = ""
    await callback.message.edit_text(
        "\n".join(lines) + daily_html,
        parse_mode=ParseMode.HTML,
        reply_markup=watchlist_item_keyboard(item),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("avito_market_wl:i:"))
async def watchlist_item(callback: CallbackQuery):
    try:
        item_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректная позиция", show_alert=True)
        return
    item = await get_iphone_market_watchlist_service().get_item(item_id)
    if not item:
        await callback.answer("Позиция не найдена", show_alert=True)
        return
    await _show_item(callback, item)


@router.callback_query(F.data.startswith("avito_market_wl:t:"))
async def watchlist_set_tier(callback: CallbackQuery):
    parts = (callback.data or "").split(":")
    try:
        item_id = int(parts[2])
        tier = "slow" if parts[3] == "s" else "daily"
    except (IndexError, ValueError):
        await callback.answer("Некорректный запрос", show_alert=True)
        return
    updated = await get_iphone_market_watchlist_service().set_tier(item_id, tier)
    if not updated:
        await callback.answer("Позиция не найдена", show_alert=True)
        return
    item = await get_iphone_market_watchlist_service().get_item(item_id) or updated
    await _show_item(callback, item)


@router.callback_query(F.data.startswith("avito_market_wl:e:"))
async def watchlist_toggle_enabled(callback: CallbackQuery):
    try:
        item_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректная позиция", show_alert=True)
        return
    service = get_iphone_market_watchlist_service()
    item = await service.get_item(item_id)
    if not item:
        await callback.answer("Позиция не найдена", show_alert=True)
        return
    updated = await service.set_enabled(item_id, not bool(item.get("enabled")))
    if not updated:
        await callback.answer("Позиция не найдена", show_alert=True)
        return
    item = await service.get_item(item_id) or updated
    await _show_item(callback, item)


@router.callback_query(F.data.startswith("avito_market_wl:rmok:"))
async def watchlist_delete_ok(callback: CallbackQuery):
    try:
        item_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректная позиция", show_alert=True)
        return
    await get_iphone_market_watchlist_service().delete(item_id)
    await _show_list(callback, page=0)


@router.callback_query(F.data.startswith("avito_market_wl:rm:"))
async def watchlist_delete_ask(callback: CallbackQuery):
    try:
        item_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректная позиция", show_alert=True)
        return
    await callback.message.edit_text(
        "Удалить позицию из автообновления?",
        reply_markup=watchlist_confirm_delete_keyboard(item_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("avito_market_wl:run:"))
async def watchlist_run(callback: CallbackQuery):
    try:
        item_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректная позиция", show_alert=True)
        return
    await callback.answer("Обновляю…")
    await callback.bot.send_chat_action(callback.message.chat.id, ChatAction.TYPING)
    try:
        _item, estimate, outcome = await get_iphone_market_watchlist_service().refresh_item(
            item_id,
            source="manual",
        )
    except MarketTemporarilyUnavailable as exc:
        await callback.message.answer(user_facing_market_error(str(exc)))
        return
    except Exception:
        await callback.message.answer(
            "Не получилось обновить эту позицию. Подождите пару минут и нажмите ещё раз."
        )
        return
    from app.bot.handlers.iphone_market_price import (
        _result_keyboard,
        format_market_estimate,
        load_market_daily_points,
        load_shop_price_range,
    )

    if estimate is None:
        await callback.answer("Позиция не найдена", show_alert=True)
        return
    hint = ""
    if outcome == "cache":
        hint = "\n\nПоказан сохранённый результат — живой запрос не требовался."
    elif outcome == "stale":
        hint = "\n\nAvito не пустил свежий запрос, показан прошлый отчёт. Фон автообновления на паузе."
    await callback.message.edit_text(
        format_market_estimate(
            estimate,
            shop_range=await load_shop_price_range(estimate.query),
            daily_points=await load_market_daily_points(estimate.query),
        )
        + hint,
        parse_mode=ParseMode.HTML,
        reply_markup=_result_keyboard(history_page=0),
        disable_web_page_preview=True,
    )


async def _show_import(callback: CallbackQuery, state: FSMContext, *, page: int | None = None) -> None:
    data = await state.get_data()
    if page is None:
        page = int(data.get("wl_import_page") or 0)
    tier = str(data.get("wl_import_tier") or "daily")
    selected = _selected_ids(data)
    rows = await get_iphone_market_watchlist_service().list_import_candidates()
    await state.update_data(wl_import_page=page, wl_import_tier=tier)
    text = (
        "📥 <b>Добавить из сохранённых отчётов</b>\n\n"
        "Отметьте конфигурации и подтвердите. "
        "Уже добавленные скрыты.\n"
        f"Выбрано: {len(selected)}. Интервал для новых: {_tier_label(tier)}."
    )
    if not rows:
        text = "📥 Нет новых конфигураций в сохранённых отчётах."
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=watchlist_import_keyboard(rows, page=page, selected=selected, tier=tier),
    )
    await callback.answer()


@router.callback_query(F.data == "avito_market_wl:imp")
async def watchlist_import(callback: CallbackQuery, state: FSMContext):
    await _show_import(callback, state, page=0)


@router.callback_query(F.data.startswith("avito_market_wl:imp:p:"))
async def watchlist_import_page(callback: CallbackQuery, state: FSMContext):
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _show_import(callback, state, page=page)


@router.callback_query(F.data.startswith("avito_market_wl:imp:g:"))
async def watchlist_import_toggle(callback: CallbackQuery, state: FSMContext):
    try:
        snap_id = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректный отчёт", show_alert=True)
        return
    data = await state.get_data()
    selected = _selected_ids(data)
    if snap_id in selected:
        selected.remove(snap_id)
    else:
        selected.add(snap_id)
    await state.update_data(wl_import_selected=sorted(selected))
    await _show_import(callback, state)


@router.callback_query(F.data == "avito_market_wl:imp:td")
async def watchlist_import_tier_daily(callback: CallbackQuery, state: FSMContext):
    await state.update_data(wl_import_tier="daily")
    await _show_import(callback, state)


@router.callback_query(F.data == "avito_market_wl:imp:ts")
async def watchlist_import_tier_slow(callback: CallbackQuery, state: FSMContext):
    await state.update_data(wl_import_tier="slow")
    await _show_import(callback, state)


@router.callback_query(F.data == "avito_market_wl:imp:ok")
async def watchlist_import_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = sorted(_selected_ids(data))
    if not selected:
        await callback.answer("Ничего не выбрано", show_alert=True)
        return
    tier = str(data.get("wl_import_tier") or "daily")
    added = await get_iphone_market_watchlist_service().import_snapshots(selected, tier=tier)
    await state.update_data(wl_import_selected=[])
    await callback.answer(f"Добавлено: {added}")
    await _show_list(callback, page=0)


async def _show_suggest(callback: CallbackQuery, state: FSMContext, *, page: int | None = None) -> None:
    data = await state.get_data()
    if page is None:
        page = int(data.get("wl_sug_page") or 0)
    tier = str(data.get("wl_sug_tier") or "daily")
    rows = await get_iphone_market_watchlist_service().list_catalog_suggestions()
    await state.update_data(wl_sug_page=page, wl_sug_tier=tier, wl_sug_rows=rows)
    text = (
        "💡 <b>Рекомендации из каталога б/у</b>\n\n"
        "Конфигурации активных б/у товаров, которых ещё нет в списке. "
        "XR и модели без памяти скрыты. Добавление только вручную.\n"
        f"Интервал для новых: {_tier_label(tier)}."
    )
    if not rows:
        text = "💡 Нет новых рекомендаций из каталога б/у."
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=watchlist_suggest_keyboard(rows, page=page, tier=tier),
    )
    await callback.answer()


@router.callback_query(F.data == "avito_market_wl:sug")
async def watchlist_suggest(callback: CallbackQuery, state: FSMContext):
    await _show_suggest(callback, state, page=0)


@router.callback_query(F.data.startswith("avito_market_wl:sug:p:"))
async def watchlist_suggest_page(callback: CallbackQuery, state: FSMContext):
    try:
        page = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await _show_suggest(callback, state, page=page)


@router.callback_query(F.data == "avito_market_wl:sug:td")
async def watchlist_suggest_tier_daily(callback: CallbackQuery, state: FSMContext):
    await state.update_data(wl_sug_tier="daily")
    await _show_suggest(callback, state)


@router.callback_query(F.data == "avito_market_wl:sug:ts")
async def watchlist_suggest_tier_slow(callback: CallbackQuery, state: FSMContext):
    await state.update_data(wl_sug_tier="slow")
    await _show_suggest(callback, state)


@router.callback_query(F.data.startswith("avito_market_wl:sug:a:"))
async def watchlist_suggest_add(callback: CallbackQuery, state: FSMContext):
    try:
        idx = int((callback.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("Некорректная рекомендация", show_alert=True)
        return
    data = await state.get_data()
    rows = data.get("wl_sug_rows") or []
    if idx < 0 or idx >= len(rows):
        rows = await get_iphone_market_watchlist_service().list_catalog_suggestions()
    if idx < 0 or idx >= len(rows):
        await callback.answer("Рекомендация устарела", show_alert=True)
        return
    row = rows[idx]
    tier = str(data.get("wl_sug_tier") or "daily")
    await get_iphone_market_watchlist_service().add_catalog_suggestion(
        str(row["model"]),
        int(row["memory_gb"]),
        tier=tier,
    )
    await callback.answer("Добавлено")
    await _show_suggest(callback, state)


@router.callback_query(F.data == "avito_market_wl:fromr")
async def watchlist_from_result(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    model = str(data.get("avito_last_model") or "")
    memory = data.get("avito_last_memory")
    if not model or memory is None:
        await callback.answer("Сначала сделайте поиск модели", show_alert=True)
        return
    existing = await get_iphone_market_watchlist_service().get_by_config(model, int(memory))
    if existing:
        await callback.answer("Уже в списке автообновления")
        await _show_item(callback, existing)
        return
    short = html.escape(get_model_display_name(model))
    await callback.message.edit_text(
        f"Добавить <b>{short} {int(memory)} ГБ</b> в автообновление?",
        parse_mode=ParseMode.HTML,
        reply_markup=watchlist_from_result_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.in_({"avito_market_wl:fromr:d", "avito_market_wl:fromr:s"}))
async def watchlist_from_result_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    model = str(data.get("avito_last_model") or "")
    memory = data.get("avito_last_memory")
    if not model or memory is None:
        await callback.answer("Сначала сделайте поиск модели", show_alert=True)
        return
    tier = "slow" if (callback.data or "").endswith(":s") else "daily"
    item = await get_iphone_market_watchlist_service().add(
        model,
        int(memory),
        tier=tier,
        source="search",
        last_snapshot_id=data.get("avito_last_snapshot_id"),
        fetched_at=_utcnow(),
    )
    await callback.answer("Добавлено")
    await _show_item(callback, item)
