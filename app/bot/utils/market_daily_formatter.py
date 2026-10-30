"""Форматирование дневной динамики рынка Avito для Telegram HTML."""
from __future__ import annotations

import html
from datetime import date
from typing import Any, Optional

from app.utils.market_daily import QUALITY_OK, QUALITY_SOFT


def _rub(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " ₽"


def _delta_suffix(current: Optional[int], previous: Optional[int]) -> str:
    if current is None or previous is None:
        return ""
    delta = current - previous
    if delta == 0:
        return ""
    sign = "+" if delta > 0 else "−"
    return f" {sign}{_rub(abs(delta)).replace(' ₽', '')}"


def _quoted_median(point: dict[str, Any]) -> Optional[int]:
    quality = str(point.get("quality") or "")
    if quality not in {QUALITY_OK, QUALITY_SOFT}:
        return None
    try:
        return int(point["median_rub"])
    except (KeyError, TypeError, ValueError):
        return None


def format_market_daily_line(
    point: dict[str, Any],
    *,
    previous_median: Optional[int] = None,
) -> str:
    observed = point.get("observed_on")
    if isinstance(observed, date):
        day = observed.strftime("%d.%m")
    else:
        day = "—"
    quality = str(point.get("quality") or "")
    used = int(point.get("used_count") or 0)
    if quality in {QUALITY_OK, QUALITY_SOFT}:
        try:
            median = int(point["median_rub"])
            q25 = int(point["q25_rub"])
            q75 = int(point["q75_rub"])
        except (KeyError, TypeError, ValueError):
            return f"{day}  —"
        mark = " ≈" if quality == QUALITY_SOFT else ""
        delta = _delta_suffix(median, previous_median)
        return (
            f"{day}  {_rub(median)} · {_rub(q25)}–{_rub(q75)}  ({used}){mark}{delta}"
        )
    return f"{day}  —"


def format_market_daily_html(points: list[dict[str, Any]]) -> str:
    """Только дни ok/soft. Пустые gap/thin — счётчиком пропусков."""
    if not points:
        return ""
    chronological = sorted(points, key=lambda row: row.get("observed_on") or date.min)
    quoted = [
        point
        for point in chronological
        if str(point.get("quality") or "") in {QUALITY_OK, QUALITY_SOFT}
        and _quoted_median(point) is not None
    ]
    skipped = len(chronological) - len(quoted)
    if not quoted:
        if skipped:
            return (
                "\n📈 Динамика по дням:\n"
                f"<i>Пропущено дней без выборки: {skipped} "
                "(Avito недоступен или выборка слишком тонкая)</i>"
            )
        return ""
    prev_for_day: dict[Any, Optional[int]] = {}
    last_quoted: Optional[int] = None
    for point in quoted:
        prev_for_day[point.get("observed_on")] = last_quoted
        median = _quoted_median(point)
        if median is not None:
            last_quoted = median
    lines = [
        html.escape(
            format_market_daily_line(
                point,
                previous_median=prev_for_day.get(point.get("observed_on")),
            )
        )
        for point in reversed(quoted)
    ]
    footer = ""
    if skipped:
        footer = (
            f"\n<i>Пропущено дней без выборки: {skipped} "
            "(Avito недоступен)</i>"
        )
    return (
        "\n📈 Динамика по дням:\n"
        "<blockquote expandable>"
        + "\n".join(lines)
        + "</blockquote>"
        + footer
    )
