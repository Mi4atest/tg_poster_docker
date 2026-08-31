"""Мэтч б/у ↔ объявления кабинета Авито по фикстурам из живого дампа."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.bot.keyboards.product_keyboard import get_avito_match_keyboard
from app.integrations.avito import actions as avito_actions
from app.integrations.avito import http_client as avito_http
from app.services.avito_listing_match import (
    extract_shop_code,
    listing_memory_gb,
    listings_from_api_rows,
    match_product_to_listings,
)

# Нормализованные title с Авито (телефоны — без магазинного кода).
_DUMP_ROWS = [
    {
        "id": 8299420433,
        "title": "iPhone 13, 128 ГБ, SIM + eSIM",
        "price": 22500,
        "url": "https://www.avito.ru/8299420433",
        "category": {"id": 84, "name": "Телефоны"},
    },
    {
        "id": 8111111111,
        "title": "iPhone 13, 128 ГБ, SIM + eSIM",
        "price": 23500,
        "url": "https://www.avito.ru/8111111111",
        "category": {"id": 84, "name": "Телефоны"},
    },
    {
        "id": 8222222222,
        "title": "iPhone 13, 128 ГБ, SIM + eSIM",
        "price": 23500,
        "url": "https://www.avito.ru/8222222222",
        "category": {"id": 84, "name": "Телефоны"},
    },
    {
        "id": 8333333333,
        "title": "iPhone 13, 128 ГБ, SIM + eSIM",
        "price": 23500,
        "url": "https://www.avito.ru/8333333333",
        "category": {"id": 84, "name": "Телефоны"},
    },
    {
        "id": 8444444444,
        "title": "iPhone 14, 128 ГБ, SIM + eSIM",
        "price": 22500,
        "url": "https://www.avito.ru/8444444444",
        "category": {"id": 84, "name": "Телефоны"},
    },
    {
        "id": 8555555555,
        "title": "AirPods Max Pink бу A2096 (526115)",
        "price": 28900,
        "url": "https://www.avito.ru/8555555555",
        "category": {"id": 32, "name": "Аудио и видео"},
    },
]


def _listings():
    return listings_from_api_rows(_DUMP_ROWS)


def test_listing_memory_gb_ignores_model_number():
    assert listing_memory_gb("iPhone 13, 128 ГБ, SIM + eSIM") == "128"
    assert listing_memory_gb("iPhone 13 128Gb Pink 3434") == "128"
    assert listing_memory_gb("iPhone 16 Pro Max, 1 ТБ") == "1024"
    assert listing_memory_gb("iPhone 13, SIM + eSIM") is None


def test_extract_shop_code_from_used_names():
    assert extract_shop_code("iPhone 13 128Gb Pink 3434") == "3434"
    assert extract_shop_code("iPhone 13 128gb White (3312)") == "3312"
    assert extract_shop_code("AirPods Max Pink (526115)") == "526115"
    assert extract_shop_code("iPhone 13, 128 ГБ, SIM + eSIM") is None


def test_phone_titles_have_no_shop_codes():
    listings = _listings()
    phone_titles = [item.title for item in listings if "iPhone" in item.title]
    shop_codes = ["3434", "3312", "4415", "3873"]
    for code in shop_codes:
        assert all(code not in title for title in phone_titles)


def test_unique_iphone_13_128_at_22500_matches_one_id():
    listings = _listings()
    hits = match_product_to_listings(
        {"name": "iPhone 13 128Gb Pink 3434", "price": "22 500 ₽"},
        listings,
    )
    assert [item.item_id for item in hits] == [8299420433]


def test_three_iphone_13_128_at_23500_are_all_candidates():
    listings = _listings()
    hits = match_product_to_listings(
        {"name": "iPhone 13 128Gb Blue 1111", "price": "23500"},
        listings,
    )
    assert [item.item_id for item in hits] == [8111111111, 8222222222, 8333333333]


def test_occupied_avito_item_id_excluded_from_pool():
    listings = _listings()
    hits = match_product_to_listings(
        {"name": "iPhone 13 128Gb Pink 3434", "price": "22500"},
        listings,
        occupied_item_ids={8299420433},
    )
    assert hits == []

    remaining = match_product_to_listings(
        {"name": "iPhone 13 128Gb White 2222", "price": "23500"},
        listings,
        occupied_item_ids={8111111111},
    )
    assert [item.item_id for item in remaining] == [8222222222, 8333333333]


def test_headphones_match_by_shop_code_in_title():
    listings = _listings()
    hits = match_product_to_listings(
        {"name": "AirPods Max Pink (526115)", "price": "28 900 ₽"},
        listings,
    )
    assert [item.item_id for item in hits] == [8555555555]


def test_iphone_does_not_match_by_shop_code_alone():
    """Код из имени б/у есть, в телефонном title его нет — мэтч только по модели/памяти/цене."""
    listings = _listings()
    hits = match_product_to_listings(
        {"name": "iPhone 13 128Gb Pink 3434", "price": "99999"},
        listings,
    )
    assert hits == []


def test_match_keyboard_single_candidate_requires_confirm():
    kb = get_avito_match_keyboard(
        42,
        [{"item_id": 8299420433, "title": "iPhone 13, 128 ГБ", "price_rub": 22500}],
    )
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "Это оно" in labels
    assert "Нет среди этих" in labels
    assert "Вставить ссылку / id" in labels
    assert "Пропустить" not in labels
    assert "avm_ok_42_8299420433" in cbs
    assert all(len(cb) <= 64 for cb in cbs)


def test_match_keyboard_collision_lists_each_candidate():
    cands = [
        {"item_id": 8111111111, "title": "iPhone 13, 128 ГБ", "price_rub": 23500},
        {"item_id": 8222222222, "title": "iPhone 13, 128 ГБ", "price_rub": 23500},
        {"item_id": 8333333333, "title": "iPhone 13, 128 ГБ", "price_rub": 23500},
    ]
    kb = get_avito_match_keyboard(7, cands, in_queue=True, back_data="products_menu")
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert cbs.count("avm_ok_7_8111111111") == 1
    assert cbs.count("avm_ok_7_8222222222") == 1
    assert cbs.count("avm_ok_7_8333333333") == 1
    assert "avm_skip_7" in cbs
    assert "avm_none_7" in cbs
    assert "Пропустить" in labels
    assert "Это оно" not in labels


def test_resources_from_items_payload():
    rows = avito_actions._resources_from_items_payload(
        {"resources": [{"id": 1, "title": "a"}, "skip", {"id": 2}]}
    )
    assert rows == [{"id": 1, "title": "a"}, {"id": 2}]
    assert avito_actions._resources_from_items_payload(None) == []
    assert avito_actions._resources_from_items_payload({"resources": None}) == []


def test_fetch_items_builds_core_v1_items_path():
    captured: dict = {}

    async def fake_request(method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        return {"resources": []}

    async def run():
        with patch.object(avito_http, "_request_json", side_effect=fake_request):
            return await avito_http.fetch_items("tok", status="active", page=2, per_page=99)

    data = asyncio.run(run())
    assert data == {"resources": []}
    assert captured["method"] == "GET"
    assert captured["path"].startswith("/core/v1/items?")
    assert "status=active" in captured["path"]
    assert "per_page=99" in captured["path"]
    assert "page=2" in captured["path"]


def test_fetch_active_listings_paginates_and_caches():
    avito_actions.invalidate_items_cache()
    page1 = {"resources": [{"id": i, "title": f"t{i}", "price": 1} for i in range(99)]}
    page2 = {"resources": [{"id": 100, "title": "last", "price": 2}]}
    calls: list[int] = []

    async def fake_fetch(_token, *, status, page, per_page):
        calls.append(page)
        assert status == "active"
        assert per_page == 99
        return page1 if page == 1 else page2

    async def run():
        with patch.object(avito_actions, "get_access_token", AsyncMock(return_value="tok")):
            with patch.object(avito_http, "fetch_items", side_effect=fake_fetch):
                first = await avito_actions.fetch_active_listings()
                second = await avito_actions.fetch_active_listings()
                await avito_actions.fetch_active_listings(force_refresh=True)
                return first, second

    try:
        first, second = asyncio.run(run())
        assert len(first) == 100
        assert first[-1]["id"] == 100
        assert first == second
        assert calls == [1, 2, 1, 2]
    finally:
        avito_actions.invalidate_items_cache()
