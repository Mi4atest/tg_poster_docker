"""Сравнение старой и новой цены, пороги предупреждений, форматирование для бота."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

# Пороги «защиты от дурака» (под типичные правки ±2000₽ на 25k–130k).
WARN_PCT = 5.0
WARN_MIN_RUB = 3000
CRITICAL_PCT = 15.0
CRITICAL_MIN_RUB = 10000
CRITICAL_RATIO_LOW = 0.7
CRITICAL_RATIO_HIGH = 1.3


class PriceChangeLevel(str, Enum):
    NORMAL = "normal"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass(frozen=True)
class PriceChangeInfo:
    old_rub: int
    new_rub: int
    delta_rub: int
    pct: float
    level: PriceChangeLevel

    @property
    def needs_confirm(self) -> bool:
        return self.level == PriceChangeLevel.CRITICAL


def price_string_to_int_rub(price: Optional[str]) -> Optional[int]:
    if not price:
        return None
    clean = re.sub(r"[^\d]", "", str(price))
    if not clean:
        return None
    try:
        v = int(clean)
        return v if v > 0 else None
    except ValueError:
        return None


def format_price_rub_display(rub: int) -> str:
    """Компактный формат для Telegram (без пробелов в числе)."""
    return f"{rub}₽"


def analyze_price_change(old_rub: int, new_rub: int) -> PriceChangeInfo:
    delta = new_rub - old_rub
    if old_rub > 0:
        pct = (delta / old_rub) * 100.0
    else:
        pct = 0.0 if delta == 0 else 100.0

    abs_delta = abs(delta)
    abs_pct = abs(pct)
    ratio = new_rub / old_rub if old_rub > 0 else 1.0

    level = PriceChangeLevel.NORMAL
    if old_rub > 0:
        if (
            abs_pct >= CRITICAL_PCT
            or abs_delta >= CRITICAL_MIN_RUB
            or ratio < CRITICAL_RATIO_LOW
            or ratio > CRITICAL_RATIO_HIGH
        ):
            level = PriceChangeLevel.CRITICAL
        elif abs_pct >= WARN_PCT and abs_delta >= WARN_MIN_RUB:
            level = PriceChangeLevel.WARN

    return PriceChangeInfo(
        old_rub=old_rub,
        new_rub=new_rub,
        delta_rub=delta,
        pct=pct,
        level=level,
    )


def _format_delta_rub(delta: int) -> str:
    sign = "+" if delta > 0 else "−" if delta < 0 else ""
    return f"{sign}{format_price_rub_display(abs(delta))}"


def _format_delta_pct(pct: float) -> str:
    if abs(pct) < 0.05:
        return "0%"
    sign = "+" if pct > 0 else "−"
    return f"{sign}{abs(pct):.1f}%".replace(".0%", "%")


def format_price_change_html_lines(info: PriceChangeInfo) -> List[str]:
    """Строки HTML: баннер (если нужен) и «было → стало»."""
    lines: List[str] = []
    if info.level == PriceChangeLevel.CRITICAL:
        lines.append(
            "🚨 ‼️ <b>СИЛЬНОЕ ИЗМЕНЕНИЕ ЦЕНЫ</b> ‼️ 🚨\n"
            "<i>Проверьте цифры и что это нужный товар.</i>"
        )
    elif info.level == PriceChangeLevel.WARN:
        lines.append("⚠️ <b>Заметное изменение цены</b>")

    old_d = html.escape(format_price_rub_display(info.old_rub))
    new_d = html.escape(format_price_rub_display(info.new_rub))
    delta_d = html.escape(_format_delta_rub(info.delta_rub))
    pct_d = html.escape(_format_delta_pct(info.pct))
    lines.append(f"📊 {old_d} → {new_d}  ({delta_d}, {pct_d})")
    return lines


def format_price_change_confirm_prompt(product_name: str, info: PriceChangeInfo) -> str:
    name = html.escape(product_name or "Без названия")
    block = "\n".join(format_price_change_html_lines(info))
    return (
        f"💰 <b>Подтвердите изменение цены</b>\n\n"
        f"📦 {name}\n\n"
        f"{block}\n\n"
        f"Применить новую цену на всех площадках?"
    )
