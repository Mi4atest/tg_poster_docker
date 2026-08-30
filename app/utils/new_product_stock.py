"""Тексты экранов наличия и скрытия новых позиций (HTML)."""
from __future__ import annotations

import html
from typing import Optional


def format_stock_off_confirm_text(product_name: str) -> str:
    name = html.escape((product_name or "Без названия").strip() or "Без названия")
    return (
        f"📦 <b>{name}</b>\n\n"
        "Сейчас: 🟢 в наличии → станет 🔴 на заказ\n\n"
        "💰 Продажа — в сводку месяца.\n"
        "📦 Перемещение — только снять с наличия, без продажи."
    )


def format_catalog_hide_confirm_text(product_name: str) -> str:
    name = html.escape((product_name or "Без названия").strip() or "Без названия")
    return (
        f"📦 <b>{name}</b>\n\n"
        "Скрыть позицию из каталога.\n"
        "Это не продажа и не снятие с наличия.\n"
        "Позиция исчезнет из «Списка новых» и с площадок."
    )


def availability_label(availability_status: Optional[str]) -> str:
    if availability_status == "available":
        return "🟢 В наличии"
    if availability_status == "on_order":
        return "🔴 На заказ"
    return "—"
