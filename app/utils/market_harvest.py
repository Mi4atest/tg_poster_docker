"""Бонус-добор чужих карточек в уже существующие снимки рынка.

После живого поиска Avito подмешивает другие модели. Их можно учесть в отчётах
тех моделей, по которым свой поиск уже был: те же фильтры, свой IQR,
без нового HTTP и без сброса таймера watchlist.
"""
from __future__ import annotations

from typing import Optional, Sequence

from app.config.settings import (
    AVITO_MARKET_MIN_SAMPLE_SIZE,
    AVITO_MARKET_MIN_SELLER_GROUP_SIZE,
    AVITO_MARKET_SOFT_SAMPLE_SIZE,
)
from app.integrations.avito.market_search import MarketListing
from app.utils.iphone_market_query import SUPPORTED_MEMORY_GB, IphoneMarketQuery
from app.utils.iphone_parser import parse_iphone_model
from app.utils.price_stats import MarketAnalysis, _listing_memory_gb, analyze_market_listings


def listings_from_audit(raw: object) -> tuple[MarketListing, ...]:
    """Восстановить карточки из listing_audit снимка."""
    if not isinstance(raw, list):
        return ()
    items: list[MarketListing] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            price = int(row.get("price_rub") or 0)
            item_id = str(row.get("id") or "").strip()
            title = str(row.get("title") or "").strip()
        except (TypeError, ValueError):
            continue
        if not item_id or not title or price <= 0:
            continue
        seller = row.get("seller_type")
        items.append(
            MarketListing(
                item_id=item_id,
                title=title,
                price_rub=price,
                url=str(row.get("url") or ""),
                seller_type=str(seller) if seller else None,
                condition=str(row.get("condition") or "") or None,
                city=str(row.get("city") or ""),
                included=(
                    bool(row.get("included"))
                    if row.get("included") is not None
                    else True
                ),
                rejection_reason=str(row.get("rejection_reason") or "") or None,
            )
        )
    items.sort(key=lambda item: item.price_rub)
    return tuple(items)


def listing_market_query(listing: MarketListing) -> Optional[IphoneMarketQuery]:
    """Ключ модель+память по заголовку карточки, либо None если не iPhone."""
    model = parse_iphone_model(listing.title)
    memory = _listing_memory_gb(listing.title)
    if not model or memory not in SUPPORTED_MEMORY_GB:
        return None
    return IphoneMarketQuery(model=model, memory_gb=int(memory))


def group_foreign_listings(
    listings: Sequence[MarketListing],
    source: IphoneMarketQuery,
) -> dict[IphoneMarketQuery, list[MarketListing]]:
    """Корзины чужих моделей. Карточки исходного запроса и неопознанные пропускаются."""
    buckets: dict[IphoneMarketQuery, list[MarketListing]] = {}
    for item in listings:
        target = listing_market_query(item)
        if target is None:
            continue
        if target.model == source.model and target.memory_gb == source.memory_gb:
            continue
        buckets.setdefault(target, []).append(item)
    return buckets


def apply_harvest(
    snapshot: Optional[dict],
    query: IphoneMarketQuery,
    harvested: Sequence[MarketListing],
    *,
    min_sample_size: int = AVITO_MARKET_MIN_SAMPLE_SIZE,
    min_soft_sample_size: int = AVITO_MARKET_SOFT_SAMPLE_SIZE,
    min_seller_group_size: int = AVITO_MARKET_MIN_SELLER_GROUP_SIZE,
) -> Optional[tuple[MarketAnalysis, int]]:
    """Слить уникальные id в существующий успешный снимок и пересчитать фильтры.

    Не создаёт первую котировку: нет success-снимка — None.
    Не затирает хорошую котировку пустым пересчётом.
    """
    if not snapshot or snapshot.get("status") != "success" or not snapshot.get("fetched_at"):
        return None
    if not harvested:
        return None
    existing = listings_from_audit(snapshot.get("listing_audit"))
    if not existing:
        return None
    existing_ids = {item.item_id for item in existing}
    new_items = [item for item in harvested if item.item_id not in existing_ids]
    if not new_items:
        return None
    analysis = analyze_market_listings(
        list(existing) + new_items,
        query,
        min_sample_size=min_sample_size,
        min_soft_sample_size=min_soft_sample_size,
        min_seller_group_size=min_seller_group_size,
    )
    if analysis.summary is None:
        return None
    return analysis, len(new_items)
