"""Тесты фазы B: черновик Авито и разбор ответа API."""
import pytest

from app.integrations.avito.create_item import (
    build_core_item_payload,
    draft_to_description_suffix,
    extract_item_id_and_url,
    price_string_to_int_rub,
)


def test_draft_to_description_suffix():
    assert draft_to_description_suffix(None) == ""
    assert draft_to_description_suffix({}) == ""
    assert "экран" in draft_to_description_suffix({"screen_level": 1, "body_level": 0})
    assert "корпус" in draft_to_description_suffix({"screen_level": 0, "body_level": 2})


def test_price_string_to_int_rub():
    assert price_string_to_int_rub(None) is None
    assert price_string_to_int_rub("45 000 ₽") == 45000
    assert price_string_to_int_rub("abc") is None


def test_build_core_item_payload():
    body = build_core_item_payload(
        post_text="iPhone 13\n💵 50000₽\nОтличное состояние",
        post_name="Test",
        avito_draft={"screen_level": 1, "body_level": 0},
        category_id=111,
        location_id=637640,
    )
    assert body["category_id"] == 111
    assert body["location_id"] == 637640
    assert "title" in body
    assert "description" in body
    assert body["price"] == 50000


@pytest.mark.parametrize(
    "data,expect_id,expect_url",
    [
        ({"id": 123, "url": "https://avito.ru/123"}, 123, "https://avito.ru/123"),
        ({"result": {"id": 7}}, 7, None),
        ({"item": {"item_id": 99, "link": "https://x"}}, 99, "https://x"),
    ],
)
def test_extract_item_id_and_url(data, expect_id, expect_url):
    iid, url = extract_item_id_and_url(data)
    assert iid == expect_id
    assert url == expect_url
