"""Тесты разметки кнопки «Каталог б/у» для Telegram."""

from app.utils.telegram_post_markup import (
    build_used_catalog_reply_markup,
    is_valid_catalog_button_url,
    normalize_catalog_url,
)


def test_normalize_catalog_url():
    assert normalize_catalog_url("  https://t.me/x  ") == "https://t.me/x"


def test_is_valid_catalog_button_url():
    assert is_valid_catalog_button_url("https://t.me/AppleShop43/12185")
    assert is_valid_catalog_button_url("http://example.com/path")
    assert not is_valid_catalog_button_url("")
    assert not is_valid_catalog_button_url("t.me/foo")
    assert not is_valid_catalog_button_url("ftp://x")


def test_build_used_catalog_reply_markup():
    kb = build_used_catalog_reply_markup("https://t.me/AppleShop43/12185")
    assert kb is not None
    row = kb.inline_keyboard[0]
    assert len(row) == 1
    assert row[0].text == "🔍 Каталог б/у"
    assert row[0].url == "https://t.me/AppleShop43/12185"


def test_build_used_catalog_reply_markup_invalid():
    assert build_used_catalog_reply_markup("") is None
    assert build_used_catalog_reply_markup("not-a-url") is None
