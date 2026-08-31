"""Дневные точки рынка Avito: качество выборки и правила записи."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from app.utils.time_msk import to_msk

QUALITY_OK = "ok"
QUALITY_SOFT = "soft"
QUALITY_THIN = "thin"
QUALITY_GAP = "gap"

_QUALITY_RANK = {
    QUALITY_GAP: 0,
    QUALITY_THIN: 1,
    QUALITY_SOFT: 2,
    QUALITY_OK: 3,
}

MARKET_DAILY_DAYS = 30


def observed_on_msk(value: datetime | None, *, fallback: Optional[datetime] = None) -> date:
    moment = value or fallback
    if moment is None:
        moment = datetime.utcnow()
    return to_msk(moment).date()


def classify_sample_quality(
    used_count: int,
    *,
    min_sample_size: int = 10,
    min_soft_sample_size: int = 3,
) -> str:
    """ok ≥ 10, soft 3…9, thin < 3. gap — только если живой запрос сорвался."""
    count = max(0, int(used_count or 0))
    if count >= max(1, min_sample_size):
        return QUALITY_OK
    if count >= max(1, min_soft_sample_size):
        return QUALITY_SOFT
    return QUALITY_THIN


def quality_rank(quality: str | None) -> int:
    return _QUALITY_RANK.get(str(quality or ""), -1)


def should_replace_daily(existing_quality: str | None, new_quality: str) -> bool:
    """Более слабая точка дня не перебивает сильную. Та же сила — берём свежее."""
    if existing_quality is None:
        return True
    return quality_rank(new_quality) >= quality_rank(existing_quality)


def quote_is_carried(*, quote_quality: str | None, used_count: int, has_summary: bool) -> bool:
    """Числа на экране вчерашние: живая выборка слишком тонкая, а котировка есть."""
    if not has_summary:
        return False
    return classify_sample_quality(used_count) == QUALITY_THIN and quote_quality != QUALITY_THIN
