from datetime import UTC, datetime

from app.integrations.avito.market_search import MarketListing
from app.utils.iphone_market_query import parse_iphone_market_query
from app.utils.market_harvest import apply_harvest, group_foreign_listings, listing_market_query
from app.utils.price_stats import analyze_market_listings


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


def _audit(*listings: MarketListing) -> list[dict]:
    return [
        {
            "id": item.item_id,
            "title": item.title,
            "price_rub": item.price_rub,
            "condition": item.condition or "Б/у",
            "seller_type": item.seller_type,
            "included": True,
        }
        for item in listings
    ]


def test_group_foreign_listings_splits_11_pro_and_skips_unknown():
    source = parse_iphone_market_query("11 64")
    listings = [
        MarketListing("11-1", "iPhone 11, 64 ГБ", 16_000, condition="Б/у"),
        MarketListing("pro-1", "iPhone 11 Pro, 64 ГБ", 22_000, condition="Б/у"),
        MarketListing("pro-2", "iPhone 11 Pro, 64 ГБ", 23_500, condition="Б/у"),
        MarketListing("max-1", "iPhone 11 Pro Max, 64 ГБ", 28_000, condition="Б/у"),
        MarketListing("junk", "TECNO Spark 64 ГБ", 8_000, condition="Б/у"),
        MarketListing("no-mem", "iPhone 11 Pro отличный", 20_000, condition="Б/у"),
    ]
    buckets = group_foreign_listings(listings, source)
    keys = {(q.model, q.memory_gb): items for q, items in buckets.items()}
    assert ("iPhone 11", 64) not in keys
    assert [item.item_id for item in keys[("iPhone 11 Pro", 64)]] == ["pro-1", "pro-2"]
    assert [item.item_id for item in keys[("iPhone 11 Pro Max", 64)]] == ["max-1"]
    assert listing_market_query(listings[4]) is None


def test_apply_harvest_skips_missing_or_error_snapshot():
    query = parse_iphone_market_query("11 pro 64")
    harvested = [MarketListing("pro-1", "iPhone 11 Pro, 64 ГБ", 22_000, condition="Б/у")]
    assert apply_harvest(None, query, harvested) is None
    assert apply_harvest({"status": "error", "fetched_at": _now()}, query, harvested) is None
    assert apply_harvest({"status": "success", "fetched_at": None}, query, harvested) is None


def test_apply_harvest_does_not_create_first_quote_from_empty_audit():
    query = parse_iphone_market_query("11 pro 64")
    harvested = [
        MarketListing(str(i), "iPhone 11 Pro, 64 ГБ", 22_000 + i * 100, condition="Б/у")
        for i in range(10)
    ]
    snap = {
        "status": "success",
        "fetched_at": _now(),
        "listing_audit": [],
    }
    assert apply_harvest(snap, query, harvested) is None


def test_apply_harvest_skips_duplicate_ids():
    query = parse_iphone_market_query("11 pro 64")
    existing = [
        MarketListing(str(i), "iPhone 11 Pro, 64 ГБ", 24_000 + i * 100, condition="Б/у")
        for i in range(10)
    ]
    snap = {
        "status": "success",
        "fetched_at": _now(),
        "listing_audit": _audit(*existing),
    }
    assert apply_harvest(snap, query, existing[:2]) is None


def test_harvest_recomputes_iqr_for_target_model():
    target = parse_iphone_market_query("11 pro 64")
    source = parse_iphone_market_query("11 64")
    existing = [
        MarketListing(str(i), "iPhone 11 Pro, 64 ГБ", 24_000 + i * 200, condition="Б/у")
        for i in range(10)
    ]
    cheap = MarketListing("cheap", "iPhone 11 Pro, 64 ГБ", 8_000, condition="Б/у")
    fit = MarketListing("fit", "iPhone 11 Pro, 64 ГБ", 25_000, condition="Б/у")
    snap = {
        "status": "success",
        "fetched_at": _now(),
        "listing_audit": _audit(*existing),
    }
    analysis, added = apply_harvest(snap, target, [cheap, fit])
    assert added == 2
    by_id = {item.item_id: item for item in analysis.audited_listings}
    assert by_id["cheap"].included is False
    assert by_id["cheap"].rejection_reason == "outlier"
    assert by_id["fit"].included is True
    assert by_id["fit"].rejection_reason is None

    source_view = analyze_market_listings([cheap, fit], source)
    assert {item.item_id: item.rejection_reason for item in source_view.audited_listings} == {
        "cheap": "model",
        "fit": "model",
    }


def test_harvest_applies_same_filters_as_dedicated_search():
    target = parse_iphone_market_query("11 pro 64")
    existing = [
        MarketListing(str(i), "iPhone 11 Pro, 64 ГБ", 24_000 + i * 100, condition="Б/у")
        for i in range(10)
    ]
    snap = {
        "status": "success",
        "fetched_at": _now(),
        "listing_audit": _audit(*existing),
    }
    noise = [
        MarketListing("case", "Чехол iPhone 11 Pro 64 ГБ", 1_500, condition="Б/у"),
        MarketListing("new", "iPhone 11 Pro, 64 ГБ", 28_000, condition="Новое"),
        MarketListing("mem", "iPhone 11 Pro, 256 ГБ", 27_000, condition="Б/у"),
    ]
    analysis, added = apply_harvest(snap, target, noise)
    assert added == 3
    by_id = {item.item_id: item for item in analysis.audited_listings}
    assert by_id["case"].rejection_reason == "excluded_title"
    assert by_id["new"].rejection_reason == "new"
    assert by_id["mem"].rejection_reason == "memory"
    assert {item.item_id for item in existing} <= {
        item.item_id for item in analysis.audited_listings if item.included
    }
