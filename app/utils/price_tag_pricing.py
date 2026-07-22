"""Расчёт «цены без скидки» для ценников."""
from __future__ import annotations

import math


def calc_strike_price(cash_rub: int, markup_percent: int) -> int:
    """Цена без скидки: базовая + markup%, округление вверх до 100₽."""
    if cash_rub <= 0:
        return 0
    pct = markup_percent if markup_percent in (5, 10) else 5
    raw = cash_rub * (1 + pct / 100)
    return int(math.ceil(raw / 100) * 100)


def format_price_tag_amount(rub: int) -> str:
    """Формат цены на ценнике: пробелы между тысячами."""
    if rub <= 0:
        return "0"
    s = f"{rub:,}".replace(",", " ")
    return s
