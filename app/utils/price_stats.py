"""Фильтрация объявлений и робастная оценка рыночной цены."""
from __future__ import annotations

import math
import re
import statistics
from dataclasses import dataclass, replace
from typing import Iterable, Optional, Sequence

from app.integrations.avito.market_search import MarketListing
from app.utils.iphone_market_query import IphoneMarketQuery
from app.utils.iphone_parser import parse_iphone_model


MIN_LISTING_PRICE_RUB = 1_000
MAX_LISTING_PRICE_RUB = 500_000
_EXCLUDED_TITLE_PATTERNS = (
    r"\bчехол\b",
    r"\bстекл[оа]\b",
    r"\bдисплей\b",
    r"\bмуляж\b",
    r"\bреплик[аи]\b",
    r"\bкопи[яи]\b",
    r"\bна\s+запчасти\b",
    r"\bзапчаст",
    r"\bремонт\b",
    r"\bаренд",
    r"\bобмен\b",
    r"\bперв(?:ый|оначальн\w*)\s+взнос\b",
    r"\bежемесячн\w*\s+платеж\b",
)
_MATERIAL_DEFECT_PATTERNS = (
    r"\bне\s+работает\b",
    r"\bнеисправ",
    r"\bразбит",
    r"\bпод\s+восстановление\b",
    r"\bна\s+запчасти\b",
    r"\bбез\s+(?:face\s*id|фейс\s*айди|связи)\b",
)


@dataclass(frozen=True)
class PriceSummary:
    count: int
    median_rub: int
    q25_rub: int
    q75_rub: int


@dataclass(frozen=True)
class MarketAnalysis:
    total_count: int
    matched_count: int
    used_count: int
    outlier_count: int
    summary: Optional[PriceSummary]
    private_summary: Optional[PriceSummary]
    business_summary: Optional[PriceSummary]
    accepted_listings: tuple[MarketListing, ...]
    audited_listings: tuple[MarketListing, ...] = ()
    is_soft: bool = False


def _listing_memory_gb(title: str) -> Optional[int]:
    tb = re.search(r"\b1\s*(?:tb|тб)\b", title, re.IGNORECASE)
    if tb:
        return 1024
    match = re.search(r"\b(\d{2,4})\s*(?:gb|гб)\b", title, re.IGNORECASE)
    if match:
        return int(match.group(1))
    # В заголовках Avito память иногда стоит отдельным последним числом.
    bare = re.findall(r"\b(16|32|64|128|256|512|1024)\b", title)
    return int(bare[-1]) if bare else None


def _looks_used(listing: MarketListing) -> bool:
    condition = (listing.condition or "").lower()
    text = f"{listing.title} {listing.description}".lower()
    if condition:
        if any(word in condition for word in ("б/у", "used", "бывш")):
            return True
        if any(word in condition for word in ("нов", "new")):
            return False
    if re.search(r"\b(?:новый|новая|новое|запечатан\w*|не\s+активир\w*)\b", text):
        return False
    if re.search(r"\bв\s+пл[её]нке\b", text):
        return False
    # Каталожные карточки новых iPhone на Avito часто заканчиваются «, 1 SIM».
    if re.search(r",\s*1\s*SIM\s*$", listing.title, re.IGNORECASE):
        return False
    return True


def listing_rejection_reason(
    listing: MarketListing,
    query: IphoneMarketQuery,
) -> Optional[str]:
    if not (MIN_LISTING_PRICE_RUB <= listing.price_rub <= MAX_LISTING_PRICE_RUB):
        return "price"
    title = listing.title.lower()
    if any(re.search(pattern, title, re.IGNORECASE) for pattern in _EXCLUDED_TITLE_PATTERNS):
        return "excluded_title"
    details = f"{listing.title} {listing.description}".lower()
    if any(re.search(pattern, details, re.IGNORECASE) for pattern in _MATERIAL_DEFECT_PATTERNS):
        return "material_defect"
    parsed_model = parse_iphone_model(listing.title)
    if parsed_model != query.model:
        return "model"
    parsed_memory = _listing_memory_gb(listing.title)
    if parsed_memory != query.memory_gb:
        return "memory"
    if not _looks_used(listing):
        return "new"
    return None


def is_relevant_listing(listing: MarketListing, query: IphoneMarketQuery) -> bool:
    return listing_rejection_reason(listing, query) is None


def _quantile(sorted_values: Sequence[int], fraction: float) -> float:
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(sorted_values[low])
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _summary(listings: Sequence[MarketListing]) -> Optional[PriceSummary]:
    if not listings:
        return None
    prices = sorted(item.price_rub for item in listings)
    return PriceSummary(
        count=len(prices),
        median_rub=round(statistics.median(prices)),
        q25_rub=round(_quantile(prices, 0.25)),
        q75_rub=round(_quantile(prices, 0.75)),
    )


def _without_iqr_outliers(listings: Sequence[MarketListing]) -> tuple[list[MarketListing], int]:
    if len(listings) < 4:
        return list(listings), 0
    prices = sorted(item.price_rub for item in listings)
    q1 = _quantile(prices, 0.25)
    q3 = _quantile(prices, 0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return list(listings), 0
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    accepted = [item for item in listings if lower <= item.price_rub <= upper]
    return accepted, len(listings) - len(accepted)


def analyze_market_listings(
    listings: Iterable[MarketListing],
    query: IphoneMarketQuery,
    *,
    min_sample_size: int = 10,
    min_soft_sample_size: int = 3,
    min_seller_group_size: int = 5,
) -> MarketAnalysis:
    unique = {item.item_id: item for item in listings}
    reasons = {
        item.item_id: listing_rejection_reason(item, query)
        for item in unique.values()
    }
    relevant = [item for item in unique.values() if reasons[item.item_id] is None]
    accepted, outlier_count = _without_iqr_outliers(relevant)
    accepted_ids = {item.item_id for item in accepted}
    for item in relevant:
        if item.item_id not in accepted_ids:
            reasons[item.item_id] = "outlier"
    audited = tuple(
        replace(
            item,
            included=item.item_id in accepted_ids,
            rejection_reason=reasons[item.item_id],
        )
        for item in unique.values()
    )

    # #region agent log
    try:
        from collections import Counter

        from app.integrations.avito.debug_agent_log import agent_dbg

        counts = Counter(reason or "matched" for reason in reasons.values())
        samples = [
            {
                "title": item.title[:100],
                "condition": (item.condition or "")[:40],
                "seller": item.seller_type,
                "reason": reasons[item.item_id],
                "parsed_model": parse_iphone_model(item.title),
                "parsed_memory": _listing_memory_gb(item.title),
            }
            for item in list(unique.values())[:12]
        ]
        agent_dbg(
            "F",
            "price_stats.py:analyze_market_listings",
            "market filter reasons",
            {
                "query_model": query.model,
                "query_memory": query.memory_gb,
                "counts": dict(counts),
                "samples": samples,
            },
        )
    except Exception:
        pass
    # #endregion

    soft_floor = max(1, min(min_soft_sample_size, min_sample_size))
    overall = _summary(accepted) if len(accepted) >= soft_floor else None
    is_soft = overall is not None and len(accepted) < min_sample_size
    private = [item for item in accepted if item.seller_type == "private"]
    business = [item for item in accepted if item.seller_type == "business"]
    private_summary = _summary(private) if len(private) >= min_seller_group_size else None
    business_summary = _summary(business) if len(business) >= min_seller_group_size else None

    return MarketAnalysis(
        total_count=len(unique),
        matched_count=len(relevant),
        used_count=len(accepted),
        outlier_count=outlier_count,
        summary=overall,
        private_summary=private_summary,
        business_summary=business_summary,
        accepted_listings=tuple(accepted),
        audited_listings=audited,
        is_soft=is_soft,
    )
