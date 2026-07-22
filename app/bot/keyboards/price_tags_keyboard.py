"""Клавиатуры для выбора товаров и печати ценников."""
from __future__ import annotations

from typing import List, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 8


def get_price_tags_select_keyboard(
    products: Sequence[dict],
    selected_ids: set[int],
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    total = len(products)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    chunk = list(products[start : start + PAGE_SIZE])

    rows: List[List[InlineKeyboardButton]] = []
    for p in chunk:
        pid = int(p["id"])
        mark = "✅" if pid in selected_ids else "⬜"
        name = (p.get("display_label") or p.get("name") or f"#{pid}").strip()
        if len(name) > 36:
            name = name[:33] + "…"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{mark} {name}",
                    callback_data=f"price_tag_toggle_{pid}",
                )
            ]
        )

    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(text="◀️", callback_data=f"price_tags_page_{page - 1}")
        )
    if pages > 1:
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{pages}",
                callback_data="price_tags_noop",
            )
        )
    if page < pages - 1:
        nav.append(
            InlineKeyboardButton(text="▶️", callback_data=f"price_tags_page_{page + 1}")
        )
    if nav:
        rows.append(nav)

    sel_count = len(selected_ids)
    rows.append(
        [
            InlineKeyboardButton(text="✅ Выбрать все", callback_data="price_tags_select_all"),
            InlineKeyboardButton(text="⬜ Снять все", callback_data="price_tags_clear_all"),
        ]
    )
    if sel_count > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🖨️ Сформировать PDF ({sel_count})",
                    callback_data="price_tags_generate",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="new_products_menu"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
