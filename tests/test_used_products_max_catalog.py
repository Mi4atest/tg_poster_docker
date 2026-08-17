"""Unit-тесты каталога б/у в Max (без API и БД)."""
from datetime import datetime, timedelta, timezone

from app.bot.utils.used_products_max_channel_updater import (
    _build_full_text,
    _chat_title_from_payload,
    _product_max_href,
    split_text_into_chunks,
)
from app.integrations.max.client import extract_message_id
from app.utils.text_formatter import format_for_max


class _DummySettingsService:
    def __init__(self, *, enabled: bool, max_url: str = ""):
        self._enabled = enabled
        self._max_url = max_url

    def get_all(self):
        return {
            "signatures": {
                "telegram_used_catalog_button_enabled": self._enabled,
                "max_used_catalog_url": self._max_url,
            }
        }


def test_chat_title_from_payload():
    assert _chat_title_from_payload({"title": "AppleShop"}) == "AppleShop"
    assert _chat_title_from_payload({"result": {"name": "Канал"}}) == "Канал"
    assert _chat_title_from_payload(None) == ""
    assert _chat_title_from_payload({}) == ""


def test_product_max_href_skips_mid_and_empty():
    assert _product_max_href({}) is None
    assert _product_max_href({"max_share_url": "max://channel/-1/abc"}) is None
    assert _product_max_href({"max_share_url": "https://max.ru/c/-1/mid.abc"}) is None
    assert (
        _product_max_href({"max_share_url": "https://max.ru/c/-1/AZ8oBFuhI1A"})
        == "https://max.ru/c/-1/AZ8oBFuhI1A"
    )


def test_split_text_into_chunks_respects_newlines():
    lines = [f"line-{i}" for i in range(20)]
    text = "\n".join(lines)
    chunks = split_text_into_chunks(text, max_len=30)
    assert len(chunks) > 1
    assert all(len(c) <= 30 for c in chunks)
    assert "line-0" in chunks[0]


def test_build_full_text_uses_max_links_and_novelties():
    now = datetime.now(timezone.utc)
    products = [
        {
            "id": 1,
            "name": "iPhone 15 128Gb Black 1111",
            "price": "50000₽",
            "max_list_href": "https://max.ru/c/-1/abc",
            "vk_product_link": "https://vk.ru/market-1_2",
            "published_max_at": now,
        },
        {
            "id": 2,
            "name": "iPhone 13 128Gb White 2222",
            "price": "30000₽",
            "max_list_href": None,
            "published_max_at": now - timedelta(days=3),
        },
    ]
    text = _build_full_text(products)
    assert "Каталог б/у (2)" in text
    assert "🆕 Новинки:" in text
    assert 'href="https://max.ru/c/-1/abc"' in text
    assert "1111" in text
    assert "2222" in text
    assert "href=\"https://vk.ru/market-1_2\"" in text


def test_extract_message_id_variants():
    assert extract_message_id({"message": {"body": {"mid": "mid.abc"}}}) == "mid.abc"
    assert extract_message_id({"result": {"message_id": "x1"}}) == "x1"
    assert extract_message_id({"message_id": "top"}) == "top"
    assert extract_message_id(None) is None


def test_format_for_max_adds_catalog_quote(monkeypatch):
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _DummySettingsService(enabled=True, max_url="https://max.ru/c/-1/AZ8o"),
    )
    out = format_for_max("Тестовый пост", signature_enabled=False)
    assert "Не подошла эта модель" in out
    assert "[нашем каталоге](https://max.ru/c/-1/AZ8o)" in out
    assert "**🔄 Не подошла эта модель?**" in out


def test_format_for_max_skips_catalog_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _DummySettingsService(enabled=False, max_url="https://max.ru/c/-1/AZ8o"),
    )
    out = format_for_max("Тестовый пост", signature_enabled=False)
    assert "Не подошла эта модель" not in out
