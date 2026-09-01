import json

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


def test_analysis_rejects_catalog_new_1_sim_title():
    query = parse_iphone_market_query("17 pro max 256")
    listings = [
        MarketListing("used", "iPhone 17 Pro Max, 256 ГБ, eSIM", 90_000, condition="Б/у"),
        MarketListing("shop-new", "iPhone 17 Pro Max, 256 ГБ, 1 SIM", 126_500),
    ]
    result = analyze_market_listings(listings, query)
    by_id = {item.item_id: item for item in result.audited_listings}
    assert by_id["shop-new"].rejection_reason == "new"
    assert by_id["used"].included is True


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
        MarketListing(str(index), "iPhone 13 mini 128 ГБ", 25_000 + index * 100)
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
        MarketListing("1", "iPhone 13 mini 128 ГБ", 25_000),
        MarketListing("2", "iPhone 13 mini 128 ГБ", 26_000),
    ]
    result = analyze_market_listings(listings, query)
    assert result.used_count == 2
    assert result.summary is None
    assert result.is_soft is False
