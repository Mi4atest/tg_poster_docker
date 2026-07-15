"""Утилиты для «застоя по цене» без зависимости от БД."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.utils.time_msk import to_msk

STALE_SORT_PRICE = "price"
STALE_SORT_SALE = "sale"


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


def resolve_sale_start(product: dict) -> Optional[datetime]:
    """Дата начала продажи: published_at / TG / VK / created_at."""
    candidates: list[datetime] = []
    for key in ("published_at", "published_telegram_at", "published_vk_at", "created_at"):
        parsed = parse_product_datetime(product.get(key))
        if parsed:
            candidates.append(parsed)
    return min(candidates) if candidates else None


def days_in_sale(
    product: dict,
    *,
    now: Optional[datetime] = None,
) -> int:
    """Календарные дни в продаже с момента публикации (включительно, минимум 1)."""
    start = resolve_sale_start(product)
    if start is None:
        return 1
    end = now or datetime.now(timezone.utc)
    d0 = to_msk(start).date()
    d1 = to_msk(end).date()
    return max(1, (d1 - d0).days + 1)
