"""Причина снятия б/у-товара с витрины: продажа или перемещение/списание."""
from __future__ import annotations

import html
from typing import Any, Optional

ARCHIVE_KIND_SALE = "sale"
ARCHIVE_KIND_TRANSFER = "transfer"

# Временная напоминалка снять объявление вручную. Потом выключить.
SHOW_AVITO_TAKE_DOWN_HINT = True

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


def format_unavailable_confirm_text(
    product_name: str,
    avito_url: Optional[str] = None,
) -> str:
    """Шапка экрана «Товар недоступен»: сирена + имя товара (HTML)."""
    name = html.escape((product_name or "Без названия").strip() or "Без названия")
    avito_block = ""
    url = (avito_url or "").strip()
    if SHOW_AVITO_TAKE_DOWN_HINT and url:
        href = html.escape(url, quote=True)
        avito_block = (
            f"\n<b>СНИМИ С АВИТО ССЫЛКА НИЖЕ</b>\n"
            f"🛒 <a href=\"{href}\">{html.escape(url)}</a>\n"
        )
    return (
        f"{_LOOK_DOWN_BANNER}\n\n"
        f"📦 {name}\n"
        f"{avito_block}\n"
        "Зелёная «Продажа» — в архив и в сводку месяца.\n"
        "Красная «Перемещение» — архив без продажи."
    )
