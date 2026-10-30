import json
from datetime import date

import pytest

from app.config.settings import AVITO_MARKET_CATEGORY_ID, AVITO_MARKET_LOCATION_ID
from app.integrations.avito.market_search import (
    AvitoMarketBlockedError,
    MarketListing,
    build_market_search_url,
    build_market_web_url,
    parse_market_search_html,
    parse_market_search_payload,
)
from app.utils.iphone_market_query import parse_iphone_market_query
from app.utils.price_stats import analyze_market_listings


def test_market_url_is_one_page_for_kirov_phone_category():
    url = build_market_search_url(parse_iphone_market_query("13 mini 128"))
    assert "/web/1/js/items?" in url
    assert f"locationId={AVITO_MARKET_LOCATION_ID}" in url
    assert f"categoryId={AVITO_MARKET_CATEGORY_ID}" in url
    assert "presentationType=serp" in url
    assert "query=" in url
    assert "p=" not in url
    assert "page=" not in url


def test_search_stays_plain_without_quotes_minus_or_second_page():
    query = parse_iphone_market_query("11 64")
    assert query.search_text == "iPhone 11 64 ГБ"
    assert '"' not in query.search_text
    assert "-Pro" not in query.search_text
    web = build_market_web_url(query)
    api = build_market_search_url(query)
    for url in (web, api):
        assert "%22" not in url
        assert "-Pro" not in url
        assert "p=2" not in url
        assert "page=2" not in url
        assert "offset=" not in url
    assert "f=ASgBAgECAUSyA0Iq" in web
    assert "params%5B110000%5D=329242" in api or "params[110000]=329242" in api


def test_parse_market_search_embedded_json():
    payload = {
        "catalog": {
            "items": [
                {
                    "id": 101,
                    "title": "iPhone 13 mini 128GB",
                    "price": {"value": 27000},
                    "urlPath": "/kirov/telefony/iphone_13_mini_101",
                    "seller": {"type": "private"},
                    "condition": "Б/у",
                }
            ]
        }
    }
    page = f'<html><script type="application/json">{json.dumps(payload)}</script></html>'
    listings = parse_market_search_html(page)
    assert listings == [
        MarketListing(
            item_id="101",
            title="iPhone 13 mini 128GB",
            price_rub=27000,
            url="/kirov/telefony/iphone_13_mini_101",
            seller_type="private",
            condition="Б/у",
        )
    ]


def test_parse_market_search_detects_captcha():
    with pytest.raises(AvitoMarketBlockedError):
        parse_market_search_html("<html>Подтвердите, что вы не робот</html>")


def test_parse_live_shaped_json_payload():
    payload = {
        "totalCount": 1,
        "items": [
            {
                "id": 202,
                "title": "iPhone 13 mini, 128 ГБ, SIM + eSIM",
                "description": "Телефон в хорошем состоянии",
                "urlPath": "/kirov/telefony/iphone_202",
                "priceDetailed": {"value": 26_500},
                "iva": {"items": [{"name": "Состояние", "value": "Б/у"}]},
            }
        ],
    }
    listing = parse_market_search_payload(payload)[0]
    assert listing.item_id == "202"
    assert listing.price_rub == 26_500
    assert listing.condition == "Б/у"
    assert listing.description == "Телефон в хорошем состоянии"


def test_parse_condition_from_nested_iva_and_is_new_flag():
    nested = parse_market_search_payload(
        {
            "items": [
                {
                    "id": 1,
                    "title": "iPhone 17 Pro Max, 256 ГБ, 1 SIM",
                    "priceDetailed": {"value": 126_500},
                    "urlPath": "/moskva/telefony/1",
                    "iva": {
                        "ParamsStep": [
                            {"title": "Состояние", "description": "Новое"},
                        ]
                    },
                }
            ]
        }
    )[0]
    assert nested.condition == "Новое"
    flagged = parse_market_search_payload(
        {
            "items": [
                {
                    "id": 2,
                    "title": "iPhone 17 Pro Max, 256 ГБ",
                    "price": 130_000,
                    "isNew": True,
                    "condition": {"name": "Новое"},
                    "urlPath": "/moskva/telefony/2",
                }
            ]
        }
    )[0]
    assert flagged.condition == "Новое"


def test_parse_ignores_screen_condition_as_listing_state():
    listing = parse_market_search_payload(
        {
            "items": [
                {
                    "id": 3,
                    "title": "iPhone 15, 128 ГБ",
                    "price": 38_000,
                    "urlPath": "/moskva/telefony/3",
                    "iva": {
                        "ParamsStep": [
                            {"title": "Состояние экрана", "description": "Отличное"},
                            {"title": "Состояние", "description": "Б/у"},
                        ]
                    },
                }
            ]
        }
    )[0]
    assert listing.condition == "Б/у"
    query = parse_iphone_market_query("17 pro max 256")
    listings = [
        MarketListing("used", "iPhone 17 Pro Max, 256 ГБ, eSIM", 90_000, condition="Б/у"),
        MarketListing("used-sim", "iPhone 17 Pro Max, 256 ГБ, 1 SIM", 88_000, condition="Б/у"),
        MarketListing("shop-new", "iPhone 17 Pro Max, 256 ГБ, 1 SIM", 126_500, condition="Новое"),
        MarketListing("no-state", "iPhone 17 Pro Max, 256 ГБ, 1 SIM", 126_500),
    ]
    result = analyze_market_listings(listings, query)
    by_id = {item.item_id: item for item in result.audited_listings}
    assert by_id["used"].included is True
    assert by_id["used-sim"].included is True
    assert by_id["shop-new"].rejection_reason == "new"
    assert by_id["no-state"].included is True
    assert by_id["no-state"].rejection_reason is None


def test_used_wear_grades_and_concatenated_serp_condition_are_included():
    query = parse_iphone_market_query("15 128")
    listings = [
        MarketListing("empty", "iPhone 15, 128 ГБ, SIM + eSIM", 32_999),
        MarketListing("good", "iPhone 15, 128 ГБ, SIM + eSIM", 33_500, condition="Хорошее"),
        MarketListing(
            "excellent-blob",
            "iPhone 15, 128 ГБ, SIM + eSIM",
            37_990,
            condition="отличное Полный комплект.",
        ),
        MarketListing("new", "iPhone 15, 128 ГБ", 52_890, condition="Новое"),
        MarketListing("title-new", "Новый iPhone 15, 128 ГБ", 54_000),
    ]
    result = analyze_market_listings(listings, query)
    by_id = {item.item_id: item for item in result.audited_listings}
    assert by_id["empty"].included is True
    assert by_id["good"].included is True
    assert by_id["excellent-blob"].included is True
    assert by_id["new"].rejection_reason == "new"
    assert by_id["title-new"].rejection_reason == "new"
    blob = parse_market_search_payload(
        {
            "items": [
                {
                    "id": 4,
                    "title": "iPhone 15, 128 ГБ",
                    "price": 37_990,
                    "urlPath": "/moskva/telefony/4",
                    "iva": {
                        "ParamsStep": [
                            {"title": "Состояние", "description": "отличное Полный комплект."},
                        ]
                    },
                }
            ]
        }
    )[0]
    assert blob.condition == "Отличное"


def test_parse_json_payload_detects_firewall_status():
    with pytest.raises(AvitoMarketBlockedError):
        parse_market_search_payload(
            {"status": "too-many-requests", "result": {"link": "firewall/captcha/show"}}
        )


def test_analysis_filters_noise_duplicates_and_outlier():
    query = parse_iphone_market_query("13 mini 128")
    listings = [
        MarketListing(
            str(index),
            "iPhone 13 mini 128GB",
            price,
            seller_type="private" if index < 5 else "business",
            condition="Б/у",
        )
        for index, price in enumerate(range(20_000, 30_000, 1_000))
    ]
    listings.extend(
        [
            MarketListing("outlier", "iPhone 13 mini 128GB", 100_000, condition="Б/у"),
            MarketListing("wrong-memory", "iPhone 13 mini 256GB", 27_000, condition="Б/у"),
            MarketListing("wrong-model", "iPhone 13 Pro 128GB", 27_000, condition="Б/у"),
            MarketListing("case", "Чехол iPhone 13 mini 128GB", 1_500, condition="Б/у"),
            MarketListing("new", "Новый iPhone 13 mini 128GB", 28_000, condition="Новое"),
            MarketListing(
                "broken",
                "iPhone 13 mini 128GB",
                15_500,
                condition="Б/у",
                description="Не работает нижний динамик",
            ),
            listings[0],
        ]
    )

    result = analyze_market_listings(listings, query)

    assert result.total_count == 16
    assert result.matched_count == 11
    assert result.used_count == 10
    assert result.outlier_count == 1
    assert result.summary is not None
    assert result.summary.median_rub == 24_500
    assert result.summary.q25_rub == 22_250
    assert result.summary.q75_rub == 26_750
    assert result.private_summary is not None
    assert result.business_summary is not None


def test_analysis_publishes_soft_sample_with_flag():
    query = parse_iphone_market_query("13 mini 128")
    listings = [
        MarketListing(str(index), "iPhone 13 mini 128 ГБ", 25_000 + index * 100, condition="Б/у")
        for index in range(5)
    ]
    result = analyze_market_listings(listings, query)
    assert result.used_count == 5
    assert result.summary is not None
    assert result.is_soft is True
    assert result.summary.median_rub == 25_200


def test_analysis_tiny_sample_stays_empty():
    query = parse_iphone_market_query("13 mini 128")
    listings = [
        MarketListing("1", "iPhone 13 mini 128 ГБ", 25_000, condition="Б/у"),
        MarketListing("2", "iPhone 13 mini 128 ГБ", 26_000, condition="Б/у"),
    ]
    result = analyze_market_listings(listings, query)
    assert result.used_count == 2
    assert result.summary is None
    assert result.is_soft is False


def test_duplicate_price_cluster_and_band_cut_shop_clones():
    from app.utils.price_stats import median_spikes_vs_anchor, resolve_price_anchor_rub

    query = parse_iphone_market_query("13 128")
    listings = [
        MarketListing("cheap1", "iPhone 13, 128 ГБ, SIM + eSIM", 5_999),
        MarketListing("cheap2", "iPhone 13, 128 ГБ, SIM + eSIM", 7_990),
        MarketListing("a", "iPhone 13, 128 ГБ, SIM + eSIM", 18_000),
        MarketListing("b", "iPhone 13, 128 ГБ", 19_000),
        MarketListing("c", "iPhone 13, 128 ГБ, 2 SIM", 19_000),
        MarketListing("d", "iPhone 13, 128 ГБ, 1 SIM", 19_800),
        MarketListing("e", "iPhone 13, 128 ГБ, SIM + eSIM", 19_990),
        MarketListing("f", "iPhone 13, 128 ГБ, SIM + eSIM", 19_990),
        MarketListing("g", "iPhone 13, 128 ГБ, SIM + eSIM", 19_990),
        MarketListing("h", "iPhone 13, 128 ГБ, SIM + eSIM", 20_408),
        MarketListing("i", "iPhone 13, 128 ГБ, SIM + eSIM", 21_990),
        MarketListing("j", "iPhone 13, 128 ГБ, SIM + eSIM", 23_990),
        *[
            MarketListing(f"clone{n}", "iPhone 13, 128 ГБ, SIM + eSIM", 100_000, city="Рязанская обл.")
            for n in range(12)
        ],
        MarketListing("pro", "iPhone 13 Pro, 128 ГБ, SIM + eSIM", 26_500),
    ]
    result = analyze_market_listings(listings, query, price_anchor_rub=20_000)
    by_id = {item.item_id: item for item in result.audited_listings}
    assert by_id["clone0"].rejection_reason == "price_band"
    assert by_id["clone1"].rejection_reason == "price_band"
    assert result.duplicate_count >= 2  # 19_990 ×3 → 2 дубля
    assert by_id["e"].included is True
    assert by_id["e"].cluster_size == 3
    assert by_id["f"].rejection_reason == "duplicate_price"
    assert result.summary is not None
    assert result.summary.median_rub < 40_000
    assert result.summary.q75_rub < 50_000
    assert result.median_spike is False

    spiked = analyze_market_listings(
        [
            MarketListing(str(i), "iPhone 13, 128 ГБ", 100_000 + i)
            for i in range(12)
        ],
        query,
        price_anchor_rub=20_000,
    )
    # все вне полосы → пустая/тонкая выборка
    assert spiked.used_count == 0 or spiked.summary is None or spiked.median_spike

    assert resolve_price_anchor_rub(
        daily_points=[
            {
                "observed_on": date(2026, 10, 11),
                "quality": "ok",
                "median_rub": 18_995,
            },
            {
                "observed_on": date(2026, 10, 22),
                "quality": "ok",
                "median_rub": 100_000,
            },
        ]
    ) == 18_995
    assert median_spikes_vs_anchor(100_000, 20_000) is True
    assert median_spikes_vs_anchor(19_500, 20_000) is False
