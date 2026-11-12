"""Сводка результатов поиска товаров: агрегаты и компактные строки архива."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Optional

from app.utils.archive_kind import is_transfer_archive
from app.utils.color_emoji import replace_color_with_emoji
from app.utils.price_change import price_string_to_int_rub
from app.utils.product_formatter import format_product_name_for_list
from app.utils.stale_price_utils import parse_product_datetime, resolve_sale_start
from app.utils.time_msk import to_msk


def archive_sale_price_rub(product: dict[str, Any]) -> Optional[int]:
    """Цена продажи в рублях: final_price (без эмодзи оплаты), иначе price."""
    for key in ("final_price", "price"):
        rub = price_string_to_int_rub(product.get(key))
        if rub is not None:
            return rub
    return None


def archive_end_dt(product: dict[str, Any]) -> Optional[datetime]:
    return parse_product_datetime(product.get("archived_at")) or parse_product_datetime(
        product.get("updated_at")
    )


def days_listed(product: dict[str, Any]) -> Optional[int]:
    """Дней в продаже до снятия (включительно, минимум 1)."""
    start = resolve_sale_start(product)
    end = archive_end_dt(product)
    if not start or not end:
        return None
    d0 = to_msk(start).date()
    d1 = to_msk(end).date()
    return max(1, (d1 - d0).days + 1)


def _dt_sort_ts(value: Optional[datetime]) -> float:
    """UTC timestamp для сортировки; None → очень старое."""
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def sort_archive_for_search(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Продажи сверху (свежие первые), перемещения в конце."""

    def _key(p: dict[str, Any]) -> tuple:
        transfer = 1 if is_transfer_archive(p) else 0
        # Свежие выше → отрицательный timestamp
        return (
            transfer,
            -_dt_sort_ts(archive_end_dt(p)),
            -_dt_sort_ts(parse_product_datetime(p.get("created_at"))),
            -int(p.get("id") or 0),
        )

    return sorted(products, key=_key)


def format_archive_compact_label(product: dict[str, Any]) -> str:
    """Короткое имя с цветовым эмодзи из COLOR_EMOJI_MAP проекта."""
    name = format_product_name_for_list(product.get("name") or "Без названия")
    return replace_color_with_emoji(name)


def format_archive_compact_line(product: dict[str, Any]) -> str:
    """Одна строка архива: имя · цена/не прод. · ДД.ММ · Nd."""
    label = format_archive_compact_label(product)
    end = archive_end_dt(product)
    date_s = to_msk(end).strftime("%d.%m") if end else "—"
    days = days_listed(product)
    days_s = f"{days}д" if days is not None else "—д"

    if is_transfer_archive(product):
        mid = "📦 не прод."
    else:
        rub = archive_sale_price_rub(product)
        mid = f"{rub}₽" if rub is not None else "—"

    return f"• {label}  {mid}  {date_s}  · {days_s}"


def _format_price_range(prices: list[int]) -> str:
    lo, hi = min(prices), max(prices)
    if lo == hi:
        return f"{lo}₽"
    return f"{lo}–{hi}₽"


def _format_days_range(days: list[int]) -> str:
    lo, hi = min(days), max(days)
    if lo == hi:
        return f"{lo} дн."
    return f"{lo}–{hi} дн."


def format_search_aggregates(archive: list[dict[str, Any]]) -> list[str]:
    """Строки агрегатов по архиву (без заголовка запроса)."""
    if not archive:
        return []

    sales = [p for p in archive if not is_transfer_archive(p)]
    transfers = [p for p in archive if is_transfer_archive(p)]
    lines: list[str] = []

    kind_bits: list[str] = []
    if sales:
        kind_bits.append(f"продаж: {len(sales)}")
    if transfers:
        kind_bits.append(f"перемещ.: {len(transfers)}")
    if kind_bits:
        lines.append(f"📁 В архиве: {len(archive)} ({' · '.join(kind_bits)})")
    else:
        lines.append(f"📁 В архиве: {len(archive)}")

    prices = [r for r in (archive_sale_price_rub(p) for p in sales) if r is not None]
    days_vals = [d for d in (days_listed(p) for p in sales) if d is not None]

    if prices:
        med = int(median(prices))
        lines.append(f"💰 Продажи: {_format_price_range(prices)} · мед. {med}₽")

    last = None
    if sales:
        last = max(
            sales,
            key=lambda p: (_dt_sort_ts(archive_end_dt(p)), int(p.get("id") or 0)),
        )

    last_bits: list[str] = []
    if days_vals:
        last_bits.append(_format_days_range(days_vals))
    if last is not None:
        end = archive_end_dt(last)
        if end:
            last_bits.append(f"посл.: {to_msk(end).strftime('%d.%m')}")
        rub = archive_sale_price_rub(last)
        if rub is not None:
            last_bits.append(f"{rub}₽")
    if last_bits:
        lines.append(f"⏱ {' · '.join(last_bits)}")

    return lines


def format_product_search_text(
    query: str,
    active: list[dict[str, Any]],
    archive: list[dict[str, Any]],
    *,
    archive_expanded: bool = False,
    arc_page: int = 0,
    per_page: int = 10,
) -> str:
    """Полный текст экрана поиска: агрегаты всегда; строки архива — при раскрытии."""
    parts: list[str] = [f"🔍 Результаты поиска: «{query}»", ""]

    if active:
        parts.append(f"📦 В наличии: {len(active)}")
    else:
        parts.append("📦 В наличии: ничего не найдено")

    if archive:
        # format_search_aggregates уже начинает с «📁 В архиве…»
        parts.extend(format_search_aggregates(archive))
        if not active and not archive_expanded:
            parts.append("")
            parts.append("Нажмите «📁 В архиве», чтобы посмотреть архивные позиции.")
    elif not active:
        # на всякий случай: оба пустые обрабатываются снаружи
        pass

    if archive and archive_expanded:
        total = len(archive)
        last_page = max(0, (total - 1) // per_page)
        page = min(max(0, arc_page), last_page)
        start = page * per_page
        end = min(start + per_page, total)
        page_items = archive[start:end]
        total_pages = last_page + 1
        parts.append("")
        parts.append(f"Архив · стр. {page + 1}/{total_pages}:")
        for p in page_items:
            parts.append(format_archive_compact_line(p))

    return "\n".join(parts)
