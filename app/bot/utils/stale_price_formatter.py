"""Форматирование экрана «Застой по цене» (б/у)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.utils.stale_price_utils import STALE_SORT_PRICE, STALE_SORT_SALE
from app.utils.stale_price_utils import days_in_sale, days_without_price_change
from app.utils.color_emoji import replace_color_with_emoji
from app.utils.price_change import price_string_to_int_rub
from app.utils.product_formatter import format_product_name_for_list
from app.utils.time_msk import format_status_date_msk


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _short_product_label(product: dict[str, Any]) -> str:
    name = format_product_name_for_list(product.get("name") or "Без названия")
    return replace_color_with_emoji(name)


def _format_price_display(price: Optional[str]) -> str:
    if not price:
        return "—"
    rub = price_string_to_int_rub(price)
    if rub is not None:
        return f"{rub}₽"
    return str(price)


def _format_stale_days_suffix(
    product: dict[str, Any],
    *,
    sort_mode: str = STALE_SORT_PRICE,
) -> str:
    """Одна цифра в строке — смысл задаёт тумблер.

    По цене: дней без смены цены (+ ↺ если цена реально менялась).
    По продаже: дней с публикации в Telegram (fallback: VK / created_at).
    """
    if sort_mode == STALE_SORT_SALE:
        return f" · {days_in_sale(product)}д."

    days_price = days_without_price_change(
        product.get("price_changed_at") or product.get("created_at")
    )
    mark = "↺" if product.get("price_repriced") else ""
    return f" · {days_price}д.{mark}"


def format_stale_list_line(
    index: int,
    product: dict[str, Any],
    *,
    sort_mode: str = STALE_SORT_PRICE,
) -> str:
    """Одна строка рейтинга: 1. 14 Pro 128Gb 🟡 2273 — 39500₽ · 110д."""
    label = _short_product_label(product)
    price = _format_price_display(product.get("price"))
    suffix = _format_stale_days_suffix(product, sort_mode=sort_mode)
    return f"{index}. {label} — {price}{suffix}"


def format_stale_list_header(
    total: int,
    badge_count: int,
    min_days: int,
    *,
    sort_mode: str = STALE_SORT_PRICE,
) -> str:
    if sort_mode == STALE_SORT_SALE:
        return (
            f"🕰 <b>Застой по цене (б/у)</b> · 📅 по давности в продаже\n"
            f"Всего: {total} · без смены ≥{min_days}д.: {badge_count}\n"
            f"<i>Nд. — дней в продаже с публикации в TG</i>\n\n"
        )
    return (
        f"🕰 <b>Застой по цене (б/у)</b>\n"
        f"Всего: {total} · без смены ≥{min_days}д.: {badge_count}\n"
        f"<i>Nд. — без смены цены · ↺ — цена менялась</i>\n\n"
    )


def format_stale_list_text(
    products: list[dict[str, Any]],
    badge_count: int,
    min_days: int,
    *,
    sort_mode: str = STALE_SORT_PRICE,
) -> str:
    """Полный текстовый список активных б/у."""
    if not products:
        return "🕰 <b>Застой по цене (б/у)</b>\n\nНет активных б/у товаров."
    header = format_stale_list_header(len(products), badge_count, min_days, sort_mode=sort_mode)
    lines = [
        format_stale_list_line(i, p, sort_mode=sort_mode)
        for i, p in enumerate(products, 1)
    ]
    return header + "\n".join(lines)


def _format_history_line(entry: dict[str, Any]) -> str:
    dt = _parse_dt(entry.get("changed_at"))
    ts = format_status_date_msk(dt) if dt else "—"
    old_p = entry.get("old_price")
    new_p = entry.get("new_price") or ""
    source = (entry.get("source") or "").strip()

    old_rub = price_string_to_int_rub(old_p) if old_p else None
    new_rub = price_string_to_int_rub(new_p) if new_p else None
    new_disp = f"{new_rub}₽" if new_rub is not None else new_p

    if old_p is None or source == "publication":
        return f"{ts} — → {new_disp} (публикация)"
    old_disp = f"{old_rub}₽" if old_rub is not None else old_p
    if old_disp == new_disp:
        return f"{ts} — {old_disp} (без изменения)"
    return f"{ts} — {old_disp} → {new_disp}"


def format_stale_detail_text(
    product: dict[str, Any],
    history: list[dict[str, Any]],
) -> str:
    """Экран истории цен по одному товару."""
    name = product.get("name") or "Без названия"
    price = _format_price_display(product.get("price"))
    days = days_without_price_change(
        product.get("price_changed_at") or product.get("created_at")
    )
    sale_days = days_in_sale(product)
    lines = [
        f"📦 <b>{name}</b>",
        f"💵 Сейчас: {price} · {days}д. без смены · {sale_days}д. в продаже",
        "",
        "📈 <b>История цен:</b>",
    ]
    if not history:
        lines.append("<i>Записей пока нет</i>")
    else:
        for entry in history:
            lines.append(_format_history_line(entry))
    return "\n".join(lines)


def stale_button_label(badge_count: int) -> str:
    return f"🕰 Застой ({badge_count})"
