"""Шапка кастомных папок в «Список новых»."""
from __future__ import annotations

from html import escape
from typing import List

from app.utils.price_change import price_string_to_int_rub
from app.utils.product_label import button_label_for_product


def _normalize_price_display(price_str: str | None) -> str:
    if not price_str:
        return "—"
    s = str(price_str).strip()
    num = price_string_to_int_rub(s)
    if num is None:
        return s
    return f"{num}₽"


def format_custom_subtree_lines(products: List[dict]) -> str:
    """Строки товаров для шапки (все позиции поддерева, с ВК-ссылкой)."""
    if not products:
        return ""
    lines: List[str] = []
    for p in products:
        nm = escape(button_label_for_product(p))
        price = _normalize_price_display(p.get("price"))
        vk = p.get("vk_product_link") or ""
        if vk:
            lines.append(f'{nm} - {price} <a href="{vk}">ВК</a>')
        else:
            lines.append(f"{nm} - {price}")
    return "\n".join(lines)


def build_custom_branch_header(
    label: str,
    *,
    subtree_products: List[dict],
    has_child_nodes: bool,
    has_leaf_products: bool,
) -> str:
    """
    Шапка кастомной папки: все товары поддерева (не только в наличии),
    как на экране версии iPhone, плюс призыв к выбору дочерней кнопки.
    """
    text = f"🆕 {escape(label)}\n\n"
    body = format_custom_subtree_lines(subtree_products)
    if body:
        text += body + "\n\n"
    if has_child_nodes:
        text += "Выберите модель:"
    elif has_leaf_products:
        text += "Выберите позицию:"
    elif not subtree_products:
        text += "Пока пусто. Добавьте подкнопки или товары в Настройках → Меню новые."
    return text
