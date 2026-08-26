"""Причина снятия б/у-товара с витрины: продажа или перемещение/списание."""
from __future__ import annotations

from typing import Any, Optional

ARCHIVE_KIND_SALE = "sale"
ARCHIVE_KIND_TRANSFER = "transfer"


def normalize_archive_kind(value: Optional[str]) -> str:
    """transfer — только явное значение; всё остальное (в т.ч. NULL) = продажа."""
    if (value or "").strip() == ARCHIVE_KIND_TRANSFER:
        return ARCHIVE_KIND_TRANSFER
    return ARCHIVE_KIND_SALE


def is_transfer_archive(product: Optional[dict[str, Any]]) -> bool:
    if not product:
        return False
    return normalize_archive_kind(product.get("archive_kind")) == ARCHIVE_KIND_TRANSFER


def format_unavailable_confirm_text(product_name: str, archive_kind: str) -> str:
    """Шапка экрана «Товар недоступен»: целиком зависит от режима."""
    name = (product_name or "Без названия").strip() or "Без названия"
    if normalize_archive_kind(archive_kind) == ARCHIVE_KIND_TRANSFER:
        return (
            "📦 Перемещение\n\n"
            f"📦 {name}\n\n"
            "Товар уйдёт в архив. В сводку продаж за месяц не попадёт."
        )
    return (
        "💰 Продажа\n\n"
        f"📦 {name}\n\n"
        "Уйдёт в архив и в сводку месяца.\n"
        "Если это перемещение или списание — нажмите «Продажа» ниже."
    )


def archive_kind_toggle_answer(archive_kind: str) -> str:
    if normalize_archive_kind(archive_kind) == ARCHIVE_KIND_TRANSFER:
        return "Режим: перемещение"
    return "Режим: продажа"
