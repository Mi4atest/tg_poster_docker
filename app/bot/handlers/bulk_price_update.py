"""Пакетное обновление цен новых товаров."""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Any, Dict, List, Optional

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.bulk_price_keyboard import (
    get_bulk_price_continue_keyboard,
    get_bulk_price_critical_keyboard,
    get_bulk_price_done_keyboard,
    get_bulk_price_mismatch_keyboard,
    get_bulk_price_preview_keyboard,
    get_bulk_price_start_keyboard,
)
from app.bot.handlers.new_products_management import safe_edit_message
from app.bot.handlers.product_management import execute_product_price_update
from app.db.database import run_db
from app.utils.bulk_price_matcher import BulkMatchResult, MatchStatus, match_bulk_lines
from app.utils.bulk_price_parser import BulkPriceNewItemLine, parse_bulk_price_input
from app.utils.price_change import (
    PriceChangeLevel,
    analyze_price_change,
    format_price_change_confirm_prompt,
    format_price_change_html_lines,
)

logger = logging.getLogger(__name__)
router = Router()

TELEGRAM_TEXT_LIMIT = 4090


class BulkPriceUpdate(StatesGroup):
    waiting_for_list = State()
    waiting_critical_confirm = State()
    waiting_mismatch_confirm = State()


def _result_to_dict(r: BulkMatchResult) -> Dict[str, Any]:
    return {
        "line_no": r.line.line_no,
        "raw_label": r.line.raw_label,
        "old_rub": r.line.old_rub,
        "new_rub": r.line.new_rub,
        "status": r.status.value,
        "product_id": r.product_id,
        "product_name": r.product_name,
        "db_price_rub": r.db_price_rub,
        "display_label": r.display_label,
        "is_critical": r.is_critical,
        "candidate_names": [c.get("name", "") for c in r.candidates[:3]],
    }


def _dict_to_result(d: Dict[str, Any]) -> BulkMatchResult:
    from app.utils.bulk_price_parser import BulkPriceLine, parse_label

    line = BulkPriceLine(
        raw_label=d["raw_label"],
        old_rub=int(d["old_rub"]),
        new_rub=int(d["new_rub"]),
        line_no=int(d.get("line_no") or 0),
    )
    parsed = parse_label(line.raw_label)
    price_change = None
    db_price = d.get("db_price_rub")
    if db_price is not None and d.get("status") in (
        MatchStatus.MATCHED.value,
        MatchStatus.PRICE_MISMATCH.value,
    ):
        price_change = analyze_price_change(int(db_price), line.new_rub)

    return BulkMatchResult(
        line=line,
        parsed=parsed,
        status=MatchStatus(d["status"]),
        product_id=d.get("product_id"),
        product_name=d.get("product_name"),
        db_price_rub=d.get("db_price_rub"),
        price_change=price_change,
        display_label=d.get("display_label") or line.raw_label,
    )


def _format_delta_badge(old_rub: int, new_rub: int) -> str:
    """Компактный индикатор изменения: вниз — зелёный, вверх — красный."""
    delta = new_rub - old_rub
    if delta == 0:
        return ""
    amount = abs(delta)
    if delta < 0:
        return f" 🟢↓{amount}"
    return f" 🔴↑{amount}"


def _format_result_line(prefix: str, r: BulkMatchResult) -> str:
    label = html.escape(r.display_label or r.line.raw_label)
    badge = _format_delta_badge(r.line.old_rub, r.line.new_rub)
    if r.status == MatchStatus.MATCHED:
        return f"{prefix} {label}: {r.line.old_rub}₽ → {r.line.new_rub}₽{badge}"
    if r.status == MatchStatus.PRICE_MISMATCH:
        dbp = r.db_price_rub or 0
        return (
            f"{prefix} {label}: в списке {r.line.old_rub}₽, в базе {dbp}₽ → {r.line.new_rub}₽{badge}"
        )
    if r.status == MatchStatus.AMBIGUOUS:
        cand = [html.escape(c.get("name", "")) for c in r.candidates[:2] if c.get("name")]
        suffix = f" ({', '.join(cand)})" if cand else ""
        return f"{prefix} {label}: несколько кандидатов{suffix}"
    if r.status == MatchStatus.NEW_ITEM:
        price = r.line.new_rub
        return f"{prefix} {label}: новая позиция, цена {price}₽ <i>(не применяется)</i>"
    return f"{prefix} {label}"


def _preview_header(results: List[BulkMatchResult]) -> str:
    new_items = [r for r in results if r.status == MatchStatus.NEW_ITEM]
    ready = [r for r in results if r.is_ready and not r.is_critical]
    critical = [r for r in results if r.is_ready and r.is_critical]
    mismatch = [r for r in results if r.status == MatchStatus.PRICE_MISMATCH]
    ambiguous = [r for r in results if r.status == MatchStatus.AMBIGUOUS]
    not_found = [r for r in results if r.status == MatchStatus.NOT_FOUND]

    lines = [
        "⚡ <b>Пакетное обновление цен</b>",
        "",
        f"Всего строк: {len(results)}",
        f"✅ Готово к применению: {len(ready)}",
        f"🚨 Крупное изменение (по одной): {len(critical)}",
        f"⚠️ Расхождение с базой: {len(mismatch)}",
        f"❓ Неоднозначно: {len(ambiguous)}",
        f"❌ Не найдено: {len(not_found)}",
        f"🆕 Новые позиции (инфо): {len(new_items)}",
    ]
    if not ready and not critical:
        lines.extend(["", "<i>Нет позиций для автоматического применения.</i>"])
    return "\n".join(lines)


def _preview_detail_lines(results: List[BulkMatchResult]) -> List[str]:
    new_items = [r for r in results if r.status == MatchStatus.NEW_ITEM]
    ready = [r for r in results if r.is_ready and not r.is_critical]
    critical = [r for r in results if r.is_ready and r.is_critical]
    mismatch = [r for r in results if r.status == MatchStatus.PRICE_MISMATCH]
    ambiguous = [r for r in results if r.status == MatchStatus.AMBIGUOUS]
    not_found = [r for r in results if r.status == MatchStatus.NOT_FOUND]

    flat: List[tuple[str, BulkMatchResult]] = []
    for group, prefix in (
        (ready, "✅"),
        (critical, "🚨"),
        (mismatch, "⚠️"),
        (ambiguous, "❓"),
        (not_found, "❌"),
        (new_items, "🆕"),
    ):
        for r in group:
            flat.append((prefix, r))

    if not flat:
        return []
    lines = ["<b>Детали:</b>"]
    lines.extend(_format_result_line(prefix, r) for prefix, r in flat)
    return lines


def _split_text_chunks(parts: List[str], max_len: int = TELEGRAM_TEXT_LIMIT) -> List[str]:
    """Разбивает список строк на сообщения, не превышающие лимит Telegram."""
    chunks: List[str] = []
    current: List[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append("\n".join(current))
            current = []

    for part in parts:
        candidate = "\n".join([*current, part]) if current else part
        if len(candidate) <= max_len:
            current.append(part)
            continue
        flush()
        if len(part) <= max_len:
            current.append(part)
            continue
        # Очень длинная одна строка — режем по символам (крайний случай).
        start = 0
        while start < len(part):
            chunks.append(part[start : start + max_len])
            start += max_len

    flush()
    return chunks or [""]


def _format_preview_messages(results: List[BulkMatchResult]) -> List[str]:
    header = _preview_header(results)
    details = _preview_detail_lines(results)
    if not details:
        return [header]
    return _split_text_chunks([header, *details])


async def _send_preview(
    message: Message,
    results: List[BulkMatchResult],
    *,
    ready_count: int,
    critical_count: int,
    mismatch_count: int,
) -> None:
    parts = _format_preview_messages(results)
    keyboard = get_bulk_price_preview_keyboard(
        ready_count,
        critical_count=critical_count,
        mismatch_count=mismatch_count,
    )
    for i, text in enumerate(parts):
        is_last = i == len(parts) - 1
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=keyboard if is_last else None,
        )


def _summary_text(
    applied: int,
    *,
    skipped_critical: int = 0,
    skipped_mismatch: int = 0,
    cancelled: bool = False,
) -> str:
    lines = ["⚡ <b>Пакетное обновление завершено</b>", ""]
    lines.append(f"✅ Применено: {applied}")
    if skipped_critical:
        lines.append(f"⏭ Пропущено (крупное изменение): {skipped_critical}")
    if skipped_mismatch:
        lines.append(f"⏭ Пропущено (расхождения): {skipped_mismatch}")
    if cancelled:
        lines.append("⏹ Остановлено пользователем")
    lines.append("")
    lines.append("Статус синхронизации площадок — в сообщении «📡 Синхронизация площадок».")
    return "\n".join(lines)


def format_mismatch_confirm_prompt(
    product_name: str,
    *,
    list_old_rub: int,
    db_old_rub: int,
    new_rub: int,
    price_change=None,
) -> str:
    name = html.escape(product_name or "Без названия")
    lines = [
        "⚠️ <b>Расхождение с прайсом</b>",
        "",
        f"📦 {name}",
        f"В прайсе (было): {list_old_rub}₽",
        f"Сейчас в базе: {db_old_rub}₽",
        f"Поставить: {new_rub}₽",
        "",
        "<i>«Старая» цена в прайсе не совпала с базой. Новая цена всё равно может быть верной.</i>",
    ]
    if price_change is not None and price_change.level == PriceChangeLevel.CRITICAL:
        lines.extend(["", *format_price_change_html_lines(price_change)])
    lines.append("")
    lines.append("Применить новую цену на всех площадках?")
    return "\n".join(lines)


async def _save_results(state: FSMContext, results: List[BulkMatchResult]) -> None:
    await state.update_data(
        bulk_results=[_result_to_dict(r) for r in results],
        bulk_applied=0,
        bulk_skipped_critical=0,
        bulk_skipped_mismatch=0,
        bulk_changed_ids=[],
        bulk_critical_ids=[
            r.product_id for r in results if r.is_ready and r.is_critical and r.product_id
        ],
        bulk_critical_index=0,
        bulk_mismatch_ids=[
            r.product_id
            for r in results
            if r.status == MatchStatus.PRICE_MISMATCH and r.product_id
        ],
        bulk_mismatch_index=0,
    )


async def _load_results(state: FSMContext) -> List[BulkMatchResult]:
    data = await state.get_data()
    return [_dict_to_result(d) for d in data.get("bulk_results") or []]


def _pending_critical(data: dict) -> List[int]:
    ids: List[int] = list(data.get("bulk_critical_ids") or [])
    index = int(data.get("bulk_critical_index") or 0)
    return ids[index:]


def _pending_mismatch(data: dict) -> List[int]:
    ids: List[int] = list(data.get("bulk_mismatch_ids") or [])
    index = int(data.get("bulk_mismatch_index") or 0)
    return ids[index:]


async def _record_bulk_price_change(state: FSMContext, product_id: int) -> None:
    data = await state.get_data()
    changed: List[int] = list(data.get("bulk_changed_ids") or [])
    if product_id not in changed:
        changed.append(product_id)
    await state.update_data(bulk_changed_ids=changed)


async def _finish_bulk_session(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    cancelled: bool = False,
) -> None:
    data = await state.get_data()
    applied = int(data.get("bulk_applied") or 0)
    skipped_critical = int(data.get("bulk_skipped_critical") or 0)
    skipped_mismatch = int(data.get("bulk_skipped_mismatch") or 0)
    changed_ids: List[int] = list(data.get("bulk_changed_ids") or [])

    def _filter_in_stock():
        from app.utils.price_tag_data import filter_in_stock_product_ids

        return filter_in_stock_product_ids(changed_ids)

    in_stock_changed = await run_db(_filter_in_stock)
    user_id = callback.from_user.id if callback.from_user else None
    if user_id is not None and hasattr(callback.message.bot, "user_data"):
        callback.message.bot.user_data.setdefault(user_id, {})["bulk_price_tag_ids"] = in_stock_changed

    # Гарантированно обновляем ТГ-прайс после пакетного применения (debounce ~20с),
    # даже если отдельные sync-job'ы ещё в очереди или custom не попал в флаг refresh.
    if applied > 0 and not cancelled:
        try:
            from app.services.price_sync_service import get_price_sync_service

            svc = get_price_sync_service()
            svc.start(callback.message.bot)
            svc.schedule_availability_list_refresh()
        except Exception:
            logger.exception("Failed to schedule availability list refresh after bulk")

    await state.clear()
    await callback.message.answer(
        _summary_text(
            applied,
            skipped_critical=skipped_critical,
            skipped_mismatch=skipped_mismatch,
            cancelled=cancelled,
        ),
        parse_mode="HTML",
        reply_markup=get_bulk_price_done_keyboard(changed_in_stock_count=len(in_stock_changed)),
    )


async def _offer_next_step(callback: CallbackQuery, state: FSMContext) -> None:
    """После применения готовых/крупных — предложить следующий шаг, если остались позиции."""
    data = await state.get_data()
    pending_crit = _pending_critical(data)
    pending_mis = _pending_mismatch(data)

    if pending_crit:
        await _show_next_critical(callback, state)
        return
    if pending_mis:
        await callback.message.answer(
            f"Осталось проверить расхождений: {len(pending_mis)}",
            reply_markup=get_bulk_price_continue_keyboard("mismatch", len(pending_mis)),
        )
        return

    await _finish_bulk_session(callback, state)


@router.callback_query(F.data == "bulk_price_finish")
async def bulk_price_finish(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _finish_bulk_session(callback, state)


@router.callback_query(F.data == "bulk_price_start")
async def bulk_price_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BulkPriceUpdate.waiting_for_list)
    text = (
        "⚡ <b>Пакетное обновление цен</b>\n\n"
        "Вставьте список цен в формате:\n"
        "<code>17 256 🔵(esim) -: 67900 → 68500 (-400₽)</code>\n"
        "<code>iPad 11 (A16 ) 128 - blue: 34500 → 33900 (-600₽)</code>\n\n"
        "Каждая строка — отдельная позиция. Пустые строки игнорируются."
    )
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=get_bulk_price_start_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "bulk_price_cancel")
async def bulk_price_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    from app.bot.handlers.new_products_management import new_products_menu

    await new_products_menu(callback)
    await callback.answer()


@router.message(BulkPriceUpdate.waiting_for_list)
async def bulk_price_receive(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("❌ Отправьте непустой список цен.")
        return

    lines = parse_bulk_price_input(text)
    price_lines, new_item_lines, _skipped = lines

    if not price_lines and not new_item_lines:
        await message.answer(
            "❌ Не удалось распознать ни одной строки.\n"
            "Формат: <code>название: 67900 → 68500</code>",
            parse_mode="HTML",
        )
        return

    try:
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    results = await run_db(match_bulk_lines, price_lines)
    for item in new_item_lines:
        from app.utils.bulk_price_parser import BulkPriceLine, parse_label

        pseudo = BulkPriceLine(
            raw_label=item.raw_label,
            old_rub=0,
            new_rub=item.new_rub,
            line_no=item.line_no,
        )
        results.append(
            BulkMatchResult(
                line=pseudo,
                parsed=parse_label(item.raw_label),
                status=MatchStatus.NEW_ITEM,
                display_label=item.raw_label,
            )
        )
    results.sort(key=lambda r: r.line.line_no)
    await _save_results(state, results)
    await state.set_state(None)

    ready_count = sum(1 for r in results if r.is_ready and not r.is_critical)
    critical_count = sum(1 for r in results if r.is_ready and r.is_critical)
    mismatch_count = sum(1 for r in results if r.status == MatchStatus.PRICE_MISMATCH)
    await _send_preview(
        message,
        results,
        ready_count=ready_count,
        critical_count=critical_count,
        mismatch_count=mismatch_count,
    )


@router.callback_query(F.data == "bulk_price_critical_start")
async def bulk_price_critical_start(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not _pending_critical(data):
        await callback.answer("Нет позиций с крупным изменением", show_alert=True)
        return
    await callback.answer()
    await _show_next_critical(callback, state)


@router.callback_query(F.data == "bulk_price_mismatch_start")
async def bulk_price_mismatch_start(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not _pending_mismatch(data):
        await callback.answer("Нет расхождений для проверки", show_alert=True)
        return
    await callback.answer()
    await _show_next_mismatch(callback, state)


@router.callback_query(F.data == "bulk_price_apply")
async def bulk_price_apply(callback: CallbackQuery, state: FSMContext) -> None:
    results = await _load_results(state)
    ready = [r for r in results if r.is_ready and not r.is_critical]
    if not ready:
        await callback.answer("Нет готовых позиций для применения", show_alert=True)
        return

    await callback.answer()
    try:
        await callback.message.bot.send_chat_action(callback.message.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    applied = int((await state.get_data()).get("bulk_applied") or 0)
    for r in ready:
        formatted = f"{r.line.new_rub}₽"
        old_rub = r.db_price_rub or r.line.old_rub
        summary, _ = await execute_product_price_update(
            r.product_id,
            formatted,
            old_rub,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
        )
        if summary:
            applied += 1
            await _record_bulk_price_change(state, r.product_id)
        await asyncio.sleep(0.3)

    await state.update_data(bulk_applied=applied)
    await _offer_next_step(callback, state)


async def _show_next_critical(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    critical_ids: List[int] = list(data.get("bulk_critical_ids") or [])
    index = int(data.get("bulk_critical_index") or 0)
    results = await _load_results(state)

    if index >= len(critical_ids):
        await _offer_next_step(callback, state)
        return

    product_id = critical_ids[index]
    match = next((r for r in results if r.product_id == product_id), None)
    if not match:
        await state.update_data(bulk_critical_index=index + 1)
        await _show_next_critical(callback, state)
        return

    db_old = match.db_price_rub or match.line.old_rub
    price_change = match.price_change or analyze_price_change(db_old, match.line.new_rub)

    await state.update_data(
        bulk_critical_product_id=product_id,
        bulk_critical_formatted_price=f"{match.line.new_rub}₽",
        bulk_critical_old_rub=db_old,
    )
    await state.set_state(BulkPriceUpdate.waiting_critical_confirm)
    name = match.product_name or match.display_label
    await callback.message.answer(
        format_price_change_confirm_prompt(name, price_change),
        parse_mode="HTML",
        reply_markup=get_bulk_price_critical_keyboard(product_id),
    )


async def _show_next_mismatch(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    mismatch_ids: List[int] = list(data.get("bulk_mismatch_ids") or [])
    index = int(data.get("bulk_mismatch_index") or 0)
    results = await _load_results(state)

    if index >= len(mismatch_ids):
        await _offer_next_step(callback, state)
        return

    product_id = mismatch_ids[index]
    match = next(
        (
            r
            for r in results
            if r.product_id == product_id and r.status == MatchStatus.PRICE_MISMATCH
        ),
        None,
    )
    if not match or match.db_price_rub is None:
        await state.update_data(bulk_mismatch_index=index + 1)
        await _show_next_mismatch(callback, state)
        return

    db_old = match.db_price_rub
    price_change = analyze_price_change(db_old, match.line.new_rub)

    await state.update_data(
        bulk_mismatch_product_id=product_id,
        bulk_mismatch_formatted_price=f"{match.line.new_rub}₽",
        bulk_mismatch_old_rub=db_old,
    )
    await state.set_state(BulkPriceUpdate.waiting_mismatch_confirm)
    name = match.product_name or match.display_label
    await callback.message.answer(
        format_mismatch_confirm_prompt(
            name,
            list_old_rub=match.line.old_rub,
            db_old_rub=db_old,
            new_rub=match.line.new_rub,
            price_change=price_change,
        ),
        parse_mode="HTML",
        reply_markup=get_bulk_price_mismatch_keyboard(product_id),
    )


@router.callback_query(F.data.startswith("bulk_price_confirm_"))
async def bulk_price_critical_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        product_id = int(callback.data.replace("bulk_price_confirm_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    data = await state.get_data()
    if data.get("bulk_critical_product_id") != product_id:
        await callback.answer("Сессия устарела", show_alert=True)
        return

    formatted = data.get("bulk_critical_formatted_price")
    old_rub = int(data.get("bulk_critical_old_rub") or 0)
    if not formatted:
        await callback.answer("Нет сохранённой цены", show_alert=True)
        return

    await callback.answer()
    summary, _ = await execute_product_price_update(
        product_id,
        formatted,
        old_rub,
        bot=callback.message.bot,
        chat_id=callback.message.chat.id,
    )

    applied = int(data.get("bulk_applied") or 0)
    if summary:
        applied += 1
        await _record_bulk_price_change(state, product_id)
    index = int(data.get("bulk_critical_index") or 0) + 1
    await state.update_data(bulk_applied=applied, bulk_critical_index=index)
    await state.set_state(None)

    if summary:
        await callback.message.answer(summary, parse_mode="HTML")

    await _show_next_critical(callback, state)


@router.callback_query(F.data.startswith("bulk_price_skip_"))
async def bulk_price_critical_skip(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    skipped = int(data.get("bulk_skipped_critical") or 0) + 1
    index = int(data.get("bulk_critical_index") or 0) + 1
    await state.update_data(bulk_skipped_critical=skipped, bulk_critical_index=index)
    await state.set_state(None)
    await callback.answer("Пропущено")
    await _show_next_critical(callback, state)


@router.callback_query(F.data.startswith("bulk_mismatch_confirm_"))
async def bulk_price_mismatch_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        product_id = int(callback.data.replace("bulk_mismatch_confirm_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return

    data = await state.get_data()
    if data.get("bulk_mismatch_product_id") != product_id:
        await callback.answer("Сессия устарела", show_alert=True)
        return

    formatted = data.get("bulk_mismatch_formatted_price")
    old_rub = int(data.get("bulk_mismatch_old_rub") or 0)
    if not formatted:
        await callback.answer("Нет сохранённой цены", show_alert=True)
        return

    await callback.answer()
    summary, _ = await execute_product_price_update(
        product_id,
        formatted,
        old_rub,
        bot=callback.message.bot,
        chat_id=callback.message.chat.id,
    )

    applied = int(data.get("bulk_applied") or 0)
    if summary:
        applied += 1
        await _record_bulk_price_change(state, product_id)
    index = int(data.get("bulk_mismatch_index") or 0) + 1
    await state.update_data(bulk_applied=applied, bulk_mismatch_index=index)
    await state.set_state(None)

    if summary:
        await callback.message.answer(summary, parse_mode="HTML")

    await _show_next_mismatch(callback, state)


@router.callback_query(F.data.startswith("bulk_mismatch_skip_"))
async def bulk_price_mismatch_skip(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    skipped = int(data.get("bulk_skipped_mismatch") or 0) + 1
    index = int(data.get("bulk_mismatch_index") or 0) + 1
    await state.update_data(bulk_skipped_mismatch=skipped, bulk_mismatch_index=index)
    await state.set_state(None)
    await callback.answer("Пропущено")
    await _show_next_mismatch(callback, state)


@router.callback_query(F.data == "bulk_price_stop")
async def bulk_price_stop(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _finish_bulk_session(callback, state, cancelled=True)
