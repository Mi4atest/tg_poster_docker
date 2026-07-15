"""Форматирование истории смены цены для карточки товара и списков."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.utils.price_change import price_string_to_int_rub
from app.utils.stale_price_utils import parse_product_datetime
from app.utils.time_msk import to_msk

def is_real_price_change(entry: dict[str, Any]) -> bool:
    """Запись истории, отличная от первичной публикации."""
    return (entry.get("source") or "").strip() != "publication"


def real_price_changes(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Смены цены после публикации, от старых к новым."""
    changes = [e for e in history if is_real_price_change(e)]
    return sorted(
        changes,
        key=lambda e: (
            parse_product_datetime(e.get("changed_at")) or datetime.min.replace(tzinfo=None),
            e.get("id") or 0,
        ),
    )


def _price_change_arrow(old_rub: Optional[int], new_rub: Optional[int]) -> str:
    if old_rub is None or new_rub is None:
        return ""
    if new_rub < old_rub:
        return "↓"
    if new_rub > old_rub:
        return "↑"
    return ""


def format_price_change_line_short(entry: dict[str, Any]) -> str:
    """Компактная строка: 💱 12.07: 91900₽ → 89900₽↓"""
    dt = parse_product_datetime(entry.get("changed_at"))
    date_short = to_msk(dt).strftime("%d.%m") if dt else "—"

    old_p = entry.get("old_price")
    new_p = entry.get("new_price") or ""
    old_rub = price_string_to_int_rub(old_p) if old_p else None
    new_rub = price_string_to_int_rub(new_p) if new_p else None

    old_disp = f"{old_rub}₽" if old_rub is not None else (old_p or "—")
    new_disp = f"{new_rub}₽" if new_rub is not None else new_p
    arrow = _price_change_arrow(old_rub, new_rub)
    return f"💱 {date_short}: {old_disp} → {new_disp}{arrow}"


def format_price_history_expandable_html(history: list[dict[str, Any]]) -> str:
    """Сворачиваемый блок истории цен для карточки товара (HTML Telegram)."""
    changes = real_price_changes(history)
    if not changes:
        return ""
    lines = [format_price_change_line_short(entry) for entry in changes]
    body = "\n".join(lines)
    return f"<blockquote expandable>{body}</blockquote>\n"
