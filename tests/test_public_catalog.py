"""Unit-тесты публичного каталога (без БД)."""

from app.api.endpoints.public_catalog import (
    _avito_public_url,
    _best_max_href,
    _build_links,
    _file_urls,
    _row_to_public,
)


def test_file_urls_builds_telegram_proxy_paths():
    urls = _file_urls(["AgAC123", " BAAC456 "])
    assert len(urls) == 2
    assert urls[0].endswith("/api/telegram/file/AgAC123")
    assert urls[1].endswith("/api/telegram/file/BAAC456")
    assert urls[0].startswith("https://")


def test_file_urls_empty_and_json_string():
    assert _file_urls(None) == []
    assert _file_urls([]) == []
    assert _file_urls("") == []
    urls = _file_urls('["fid1"]')
    assert len(urls) == 1
    assert urls[0].endswith("/api/telegram/file/fid1")


def test_best_max_href_prefers_share_then_https_then_max_scheme():
    assert _best_max_href("max://-1/2", "https://max.ru/c/-1/99") == "https://max.ru/c/-1/99"
    assert _best_max_href("https://max.ru/c/1/2", None) == "https://max.ru/c/1/2"
    assert _best_max_href("max://-129/55", None) == "https://max.ru/c/-129/55"
    assert _best_max_href(None, None) is None


def test_avito_url_from_item_id():
    assert _avito_public_url(None, "12345") == "https://www.avito.ru/12345"
    assert _avito_public_url("https://www.avito.ru/x", "1") == "https://www.avito.ru/x"


def test_row_to_public_collects_all_links():
    row = {
        "id": 1,
        "name": "Phone",
        "display_label": None,
        "price": "100₽",
        "collection_name": "iPhone б/у",
        "status": "active",
        "availability_status": None,
        "photos": [],
        "videos": [],
        "storage_path": None,
        "telegram_link": "https://t.me/x/1",
        "vk_product_id": 10,
        "vk_product_link": "https://vk.com/market?w=product-1_2",
        "vk_post_link": "https://vk.com/wall-1_2",
        "max_link": "max://-1/2",
        "max_share_url": "https://max.ru/c/-1/2",
        "instagram_link": "https://instagram.com/p/abc",
        "avito_url": None,
        "avito_item_id": "999",
        "created_at": None,
        "custom_button_id": None,
    }
    p = _row_to_public(row, "used")
    assert p.links.telegram.startswith("https://t.me/")
    assert p.links.vk_market
    assert p.links.vk_post
    assert p.links.max == "https://max.ru/c/-1/2"
    assert p.links.instagram
    assert p.links.avito == "https://www.avito.ru/999"
    assert p.vk_post_link == p.links.vk_post
    assert _build_links(
        telegram=None,
        vk_market=None,
        vk_post=None,
        max_link=None,
        max_share_url=None,
        instagram=None,
        avito=None,
    ).telegram is None
