"""Причина снятия б/у-товара с витрины: продажа или перемещение/списание."""
from __future__ import annotations

import html
from typing import Any, Optional

ARCHIVE_KIND_SALE = "sale"
ARCHIVE_KIND_TRANSFER = "transfer"

_LOOK_DOWN_BANNER = (
    "🚨 ‼️ <b>ПОСМОТРИТЕ ВНИЗ, ПРЕЖДЕ ЧЕМ НАЖАТЬ</b> ‼️ 🚨\n"
    "<i>Зелёная — продажа, в сводку месяца. "
    "Красная — перемещение, в сводку и отчёт не уйдёт.</i>"
)


def normalize_archive_kind(value: Optional[str]) -> str:
    """transfer — только явное значение; всё остальное (в т.ч. NULL) = продажа."""
    if (value or "").strip() == ARCHIVE_KIND_TRANSFER:
        return ARCHIVE_KIND_TRANSFER
    return ARCHIVE_KIND_SALE


def is_transfer_archive(product: Optional[dict[str, Any]]) -> bool:
    if not product:
        return False
    return normalize_archive_kind(product.get("archive_kind")) == ARCHIVE_KIND_TRANSFER


def format_unavailable_confirm_text(product_name: str) -> str:
    """Шапка экрана «Товар недоступен»: сирена + имя товара (HTML)."""
    name = html.escape((product_name or "Без названия").strip() or "Без названия")
    return (
        f"{_LOOK_DOWN_BANNER}\n\n"
        f"📦 {name}\n\n"
        "Зелёная «Продажа» — в архив и в сводку месяца.\n"
        "Красная «Перемещение» — архив без продажи."
    )
