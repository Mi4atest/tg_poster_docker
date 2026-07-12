"""Утилиты для «застоя по цене» без зависимости от БД."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.utils.time_msk import to_msk


def parse_product_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def days_without_price_change(
    price_changed_at: Optional[datetime | str],
    *,
    now: Optional[datetime] = None,
) -> int:
    """Календарные дни с последней смены цены (включительно, минимум 1)."""
    dt = parse_product_datetime(price_changed_at)
    if dt is None:
        return 1
    end = now or datetime.now(timezone.utc)
    d0 = to_msk(dt).date()
    d1 = to_msk(end).date()
    return max(1, (d1 - d0).days + 1)
