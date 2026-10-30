"""Фильтрация объявлений и робастная оценка рыночной цены."""
from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Iterable, Optional, Sequence

from app.integrations.avito.market_search import MarketListing
from app.utils.iphone_market_query import IphoneMarketQuery
from app.utils.iphone_parser import parse_iphone_model


MIN_LISTING_PRICE_RUB = 1_000
MAX_LISTING_PRICE_RUB = 500_000
DUPLICATE_PRICE_MIN = 3
PRICE_BAND_LOW = 0.45
PRICE_BAND_HIGH = 2.2
MEDIAN_SPIKE_HIGH = 1.8
MEDIAN_SPIKE_LOW = 0.55
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
    duplicate_count: int = 0
    unique_price_count: int = 0
    price_anchor_rub: Optional[int] = None
    median_spike: bool = False


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


def _title_looks_new(listing: MarketListing) -> bool:
    text = f"{listing.title} {listing.description}".lower()
    if re.search(r"\b(?:новый|новая|новое|запечатан\w*|не\s+активир\w*)\b", text):
        return True
    return bool(re.search(r"\bв\s+пл[её]нке\b", text))


_USED_CONDITION_MARKERS = (
    "б/у",
    "б.у",
    "used",
    "бывш",
    "отличн",
    "хорош",
    "средн",
    "удовлетворительн",
    "идеальн",
)
_NEW_CONDITION_MARKERS = ("новое", "new")


def _condition_kind(listing: MarketListing) -> str:
    """used | new | unknown — без эвристик по SIM.

    В выдаче поиска Avito чаще шкала износа б/у («отличное», «хорошее»),
    а не слово «Б/у». Пустое поле в SERP не значит «нет состояния» на карточке:
    поиск уже с фильтром б/у, оценку часто не копируют в JSON списка.
    """
    if _title_looks_new(listing):
        return "new"
    condition = (listing.condition or "").strip().lower()
    if not condition:
        return "used"
    if any(word in condition for word in _USED_CONDITION_MARKERS):
        return "used"
    if any(word in condition for word in _NEW_CONDITION_MARKERS):
        return "new"
    return "unknown"


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
    kind = _condition_kind(listing)
    if kind == "new":
        return "new"
    if kind == "unknown":
        return "unknown_condition"
    return None


def is_relevant_listing(listing: MarketListing, query: IphoneMarketQuery) -> bool:
    return listing_rejection_reason(listing, query) is None


def price_in_anchor_band(
    price_rub: int,
    anchor_rub: Optional[int],
    *,
    low: float = PRICE_BAND_LOW,
    high: float = PRICE_BAND_HIGH,
) -> bool:
    if not anchor_rub or anchor_rub <= 0:
        return True
    return low * anchor_rub <= price_rub <= high * anchor_rub


def median_spikes_vs_anchor(
    median_rub: Optional[int],
    anchor_rub: Optional[int],
    *,
    high: float = MEDIAN_SPIKE_HIGH,
    low: float = MEDIAN_SPIKE_LOW,
) -> bool:
    if median_rub is None or not anchor_rub or anchor_rub <= 0:
        return False
    return median_rub > high * anchor_rub or median_rub < low * anchor_rub


def resolve_price_anchor_rub(
    *,
    daily_points: Optional[Sequence[dict]] = None,
    shop_min_rub: Optional[int] = None,
    shop_max_rub: Optional[int] = None,
) -> Optional[int]:
    """Якорь: последняя устойчивая дневная медиана ok/soft, иначе вилка магазина.

    День, который сам скачет относительно предыдущего якоря (например 20к → 100к),
    в якорь не берём — иначе полоса и антискачок перестают работать.
    """
    points = list(daily_points or [])
    chronological = sorted(
        points,
        key=lambda row: row.get("observed_on") or "",
    )
    anchor: Optional[int] = None
    for point in chronological:
        quality = str(point.get("quality") or "")
        if quality not in {"ok", "soft"}:
            continue
        try:
            median = int(point["median_rub"])
        except (KeyError, TypeError, ValueError):
            continue
        if median <= 0:
            continue
        if anchor is not None and median_spikes_vs_anchor(median, anchor):
            continue
        anchor = median
    if anchor:
        return anchor
    if shop_min_rub and shop_max_rub and shop_min_rub > 0 and shop_max_rub > 0:
        return round((int(shop_min_rub) + int(shop_max_rub)) / 2)
    if shop_min_rub and shop_min_rub > 0:
        return int(shop_min_rub)
    return None


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


def _collapse_price_clusters(
    relevant: Sequence[MarketListing],
    reasons: dict[str, Optional[str]],
    *,
    price_anchor_rub: Optional[int],
) -> tuple[list[MarketListing], int, int]:
    """Схлопнуть одинаковые цены (≥3) в одну точку; вне полосы — выкинуть кластер."""
    by_price: dict[int, list[MarketListing]] = defaultdict(list)
    for item in relevant:
        by_price[item.price_rub].append(item)

    kept: list[MarketListing] = []
    duplicate_count = 0
    band_rejected = 0
    for price, group in sorted(by_price.items()):
        group_sorted = sorted(group, key=lambda item: item.item_id)
        size = len(group_sorted)
        in_band = price_in_anchor_band(price, price_anchor_rub)
        if size >= DUPLICATE_PRICE_MIN:
            keeper = replace(group_sorted[0], cluster_size=size)
            if not in_band:
                for item in group_sorted:
                    reasons[item.item_id] = "price_band"
                band_rejected += size
                continue
            kept.append(keeper)
            for item in group_sorted[1:]:
                reasons[item.item_id] = "duplicate_price"
            duplicate_count += size - 1
            continue
        for item in group_sorted:
            if not in_band:
                reasons[item.item_id] = "price_band"
                band_rejected += 1
                continue
            kept.append(replace(item, cluster_size=1))
    return kept, duplicate_count, band_rejected


def analyze_market_listings(
    listings: Iterable[MarketListing],
    query: IphoneMarketQuery,
    *,
    min_sample_size: int = 10,
    min_soft_sample_size: int = 3,
    min_seller_group_size: int = 5,
    price_anchor_rub: Optional[int] = None,
) -> MarketAnalysis:
    unique = {item.item_id: item for item in listings}
    reasons: dict[str, Optional[str]] = {
        item.item_id: listing_rejection_reason(item, query)
        for item in unique.values()
    }
    relevant = [item for item in unique.values() if reasons[item.item_id] is None]
    collapsed, duplicate_count, band_rejected = _collapse_price_clusters(
        relevant,
        reasons,
        price_anchor_rub=price_anchor_rub,
    )
    accepted, outlier_count = _without_iqr_outliers(collapsed)
    accepted_ids = {item.item_id for item in accepted}
    for item in collapsed:
        if item.item_id not in accepted_ids:
            reasons[item.item_id] = "outlier"

    cluster_sizes = {item.item_id: max(1, int(item.cluster_size or 1)) for item in collapsed}
    for item in accepted:
        cluster_sizes[item.item_id] = max(1, int(item.cluster_size or 1))

    audited = tuple(
        replace(
            item,
            included=item.item_id in accepted_ids,
            rejection_reason=reasons[item.item_id],
            cluster_size=cluster_sizes.get(item.item_id, max(1, int(item.cluster_size or 1))),
        )
        for item in unique.values()
    )
    # Представители кластеров должны нести cluster_size в accepted/audit included rows.
    audited_by_id = {item.item_id: item for item in audited}
    for item in accepted:
        current = audited_by_id.get(item.item_id)
        if current is not None and current.cluster_size != item.cluster_size:
            audited_by_id[item.item_id] = replace(current, cluster_size=item.cluster_size)
    audited = tuple(audited_by_id[item.item_id] for item in unique.values())

    soft_floor = max(1, min(min_soft_sample_size, min_sample_size))
    overall = _summary(accepted) if len(accepted) >= soft_floor else None
    is_soft = overall is not None and len(accepted) < min_sample_size
    spike = median_spikes_vs_anchor(
        overall.median_rub if overall else None,
        price_anchor_rub,
    )
    private = [item for item in accepted if item.seller_type == "private"]
    business = [item for item in accepted if item.seller_type == "business"]
    private_summary = _summary(private) if len(private) >= min_seller_group_size else None
    business_summary = _summary(business) if len(business) >= min_seller_group_size else None

    # #region agent log
    try:
        from collections import Counter

        from app.integrations.avito.debug_agent_log import agent_dbg

        counts = Counter(reason or "matched" for reason in reasons.values())
        kinds = Counter(_condition_kind(item) for item in unique.values())
        samples = [
            {
                "title": item.title[:100],
                "condition": (item.condition or "")[:40],
                "kind": _condition_kind(item),
                "seller": item.seller_type,
                "reason": reasons[item.item_id],
                "price": item.price_rub,
                "cluster": cluster_sizes.get(item.item_id, 1),
                "parsed_model": parse_iphone_model(item.title),
                "parsed_memory": _listing_memory_gb(item.title),
            }
            for item in list(unique.values())[:12]
        ]
        agent_dbg(
            "B",
            "price_stats.py:analyze_market_listings",
            "market filter reasons",
            {
                "query_model": query.model,
                "query_memory": query.memory_gb,
                "counts": dict(counts),
                "kinds": dict(kinds),
                "empty_condition": sum(
                    1 for item in unique.values() if not (item.condition or "").strip()
                ),
                "used_count": len(accepted),
                "matched_count": len(relevant),
                "duplicate_count": duplicate_count,
                "band_rejected": band_rejected,
                "anchor": price_anchor_rub,
                "median": overall.median_rub if overall else None,
                "median_spike": spike,
                "samples": samples,
            },
        )
    except Exception:
        pass
    # #endregion

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
        duplicate_count=duplicate_count,
        unique_price_count=len({item.price_rub for item in accepted}),
        price_anchor_rub=price_anchor_rub,
        median_spike=spike,
    )
