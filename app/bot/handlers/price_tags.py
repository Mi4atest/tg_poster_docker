"""Выбор товаров и генерация PDF-ценников."""
from __future__ import annotations

import logging
from datetime import date
from html import escape
from typing import List, Set

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery

from app.bot.handlers.new_products_management import safe_edit_message
from app.bot.keyboards.price_tags_keyboard import PAGE_SIZE, get_price_tags_select_keyboard
from app.db.database import run_db
from app.utils.price_tag_data import fetch_available_products_for_tags
from app.utils.price_tag_pdf import build_price_tags_pdf_bytes

logger = logging.getLogger(__name__)
router = Router()


class PriceTagPrint(StatesGroup):
    selecting = State()


def _selection_text(products: List[dict], selected_ids: Set[int], page: int) -> str:
    total = len(products)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    lines = [
        "🏷️ <b>Ценники A4 (PDF)</b>",
        "",
        f"Товаров в наличии: <b>{total}</b>",
        f"Выбрано: <b>{len(selected_ids)}</b>",
        "",
        "Отметьте позиции для печати (16 ценников на лист):",
    ]
    if total == 0:
        lines.append("")
        lines.append("Нет товаров со статусом «В наличии».")
    return "\n".join(lines)


async def _show_select_screen(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    products: List[dict],
    selected_ids: Set[int],
    page: int,
) -> None:
    await state.set_state(PriceTagPrint.selecting)
    await state.update_data(
        price_tag_product_ids=[int(p["id"]) for p in products],
        price_tag_selected=sorted(selected_ids),
        price_tag_page=page,
    )
    text = _selection_text(products, selected_ids, page)
    kb = get_price_tags_select_keyboard(products, selected_ids, page=page)
    await safe_edit_message(
        callback.message,
        text,
        reply_markup=kb,
        parse_mode="HTML",
        disable_link_preview=True,
    )


@router.callback_query(F.data == "price_tags_select")
async def price_tags_select_start(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        await callback.answer()
    except Exception:
        pass

    def _load():
        return fetch_available_products_for_tags()

    try:
        products = await run_db(_load)
    except Exception:
        logger.exception("price_tags_select_start")
        await callback.answer("Ошибка загрузки товаров", show_alert=True)
        return

    selected = {int(p["id"]) for p in products}
    await _show_select_screen(callback, state, products=products, selected_ids=selected, page=0)


@router.callback_query(F.data == "price_tags_noop")
async def price_tags_noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("price_tags_page_"))
async def price_tags_page(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        page = int(callback.data.replace("price_tags_page_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    data = await state.get_data()
    ids_order = data.get("price_tag_product_ids") or []
    selected = set(int(x) for x in (data.get("price_tag_selected") or []))

    def _load():
        all_prods = fetch_available_products_for_tags()
        by_id = {int(p["id"]): p for p in all_prods}
        return [by_id[i] for i in ids_order if i in by_id]

    products = await run_db(_load)
    await callback.answer()
    await _show_select_screen(callback, state, products=products, selected_ids=selected, page=page)


@router.callback_query(F.data.startswith("price_tag_toggle_"))
async def price_tag_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        pid = int(callback.data.replace("price_tag_toggle_", ""))
    except ValueError:
        await callback.answer("Ошибка", show_alert=True)
        return
    data = await state.get_data()
    selected = set(int(x) for x in (data.get("price_tag_selected") or []))
    page = int(data.get("price_tag_page") or 0)
    ids_order = data.get("price_tag_product_ids") or []

    if pid in selected:
        selected.discard(pid)
    else:
        selected.add(pid)

    def _load():
        all_prods = fetch_available_products_for_tags()
        by_id = {int(p["id"]): p for p in all_prods}
        return [by_id[i] for i in ids_order if i in by_id]

    products = await run_db(_load)
    await callback.answer()
    await _show_select_screen(callback, state, products=products, selected_ids=selected, page=page)


@router.callback_query(F.data == "price_tags_select_all")
async def price_tags_select_all(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = int(data.get("price_tag_page") or 0)
    ids_order = data.get("price_tag_product_ids") or []
    selected = set(int(i) for i in ids_order)

    def _load():
        all_prods = fetch_available_products_for_tags()
        by_id = {int(p["id"]): p for p in all_prods}
        return [by_id[i] for i in ids_order if i in by_id]

    products = await run_db(_load)
    await callback.answer("Все выбраны")
    await _show_select_screen(callback, state, products=products, selected_ids=selected, page=page)


@router.callback_query(F.data == "price_tags_clear_all")
async def price_tags_clear_all(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    page = int(data.get("price_tag_page") or 0)
    ids_order = data.get("price_tag_product_ids") or []

    def _load():
        all_prods = fetch_available_products_for_tags()
        by_id = {int(p["id"]): p for p in all_prods}
        return [by_id[i] for i in ids_order if i in by_id]

    products = await run_db(_load)
    await callback.answer("Выбор снят")
    await _show_select_screen(callback, state, products=products, selected_ids=set(), page=page)


@router.callback_query(F.data == "price_tags_generate")
async def price_tags_generate(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = [int(x) for x in (data.get("price_tag_selected") or [])]
    if not selected:
        await callback.answer("Выберите хотя бы один товар", show_alert=True)
        return
    try:
        await callback.answer("Формирую PDF…")
    except Exception:
        pass
    try:
        await callback.message.bot.send_chat_action(
            callback.message.chat.id, ChatAction.UPLOAD_DOCUMENT
        )
    except Exception:
        pass

    def _build():
        return build_price_tags_pdf_bytes(selected)

    try:
        pdf_bytes = await run_db(_build)
    except ValueError as e:
        await callback.message.answer(f"Не удалось создать PDF: {escape(str(e))}", parse_mode="HTML")
        return
    except Exception:
        logger.exception("price_tags_generate")
        await callback.message.answer("Ошибка при формировании PDF-ценников.")
        return

    await state.clear()
    filename = f"price_tags_{date.today().isoformat()}.pdf"
    doc = BufferedInputFile(pdf_bytes, filename=filename)
    await callback.message.answer_document(
        document=doc,
        caption=f"🏷️ Ценники для печати ({len(selected)} шт.)",
    )


@router.callback_query(F.data == "price_tags_bulk_print")
async def price_tags_bulk_print(callback: CallbackQuery, state: FSMContext) -> None:
    """Печать ценников только для позиций, изменённых в пакетном обновлении (в наличии)."""
    user_id = callback.from_user.id if callback.from_user else None
    ids: List[int] = []
    if user_id is not None and hasattr(callback.message.bot, "user_data"):
        ids = list(callback.message.bot.user_data.get(user_id, {}).get("bulk_price_tag_ids") or [])
    if not ids:
        await callback.answer("Нет изменённых позиций в наличии", show_alert=True)
        return
    try:
        await callback.answer("Формирую PDF…")
    except Exception:
        pass
    try:
        await callback.message.bot.send_chat_action(
            callback.message.chat.id, ChatAction.UPLOAD_DOCUMENT
        )
    except Exception:
        pass

    def _build():
        return build_price_tags_pdf_bytes(ids)

    try:
        pdf_bytes = await run_db(_build)
    except ValueError as e:
        await callback.message.answer(f"Не удалось создать PDF: {escape(str(e))}", parse_mode="HTML")
        return
    except Exception:
        logger.exception("price_tags_bulk_print")
        await callback.message.answer("Ошибка при формировании PDF-ценников.")
        return

    filename = f"price_tags_changed_{date.today().isoformat()}.pdf"
    doc = BufferedInputFile(pdf_bytes, filename=filename)
    await callback.message.answer_document(
        document=doc,
        caption=f"🏷️ Ценники изменённых позиций ({len(ids)} шт.)",
    )
