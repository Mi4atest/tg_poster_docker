"""Тесты URL-валидации и текстовых блоков каталога б/у."""

from app.utils.telegram_post_markup import is_valid_catalog_button_url, normalize_catalog_url
from app.utils.text_formatter import format_for_telegram, format_for_vk


class _DummySettingsService:
    def __init__(self, *, enabled: bool, tg_url: str, vk_url: str = ""):
        self._enabled = enabled
        self._tg_url = tg_url
        self._vk_url = vk_url

    def get_all(self):
        return {
            "signatures": {
                "telegram_used_catalog_button_enabled": self._enabled,
                "telegram_used_catalog_url": self._tg_url,
                "vk_used_catalog_url": self._vk_url,
            }
        }


def test_normalize_catalog_url():
    assert normalize_catalog_url("  https://t.me/x  ") == "https://t.me/x"


def test_is_valid_catalog_button_url():
    assert is_valid_catalog_button_url("https://t.me/AppleShop43/12185")
    assert is_valid_catalog_button_url("http://example.com/path")
    assert not is_valid_catalog_button_url("")
    assert not is_valid_catalog_button_url("t.me/foo")
    assert not is_valid_catalog_button_url("ftp://x")


def test_format_for_telegram_adds_catalog_quote_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _DummySettingsService(enabled=True, tg_url="https://t.me/AppleShop43/12185"),
    )
    out = format_for_telegram("Тестовый пост", signature_enabled=False)
    assert "> *🔄 Не подошла эта модель?*" in out
    assert "[нашем каталоге](https://t.me/AppleShop43/12185)" in out


def test_format_for_telegram_keeps_old_text_when_catalog_toggle_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _DummySettingsService(enabled=False, tg_url="https://t.me/AppleShop43/12185"),
    )
    out = format_for_telegram("Тестовый пост", signature_enabled=False)
    assert "Не подошла эта модель" not in out
    assert "[нашем каталоге]" not in out


def test_format_for_telegram_skips_catalog_quote_for_invalid_url(monkeypatch):
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _DummySettingsService(enabled=True, tg_url="not-a-url"),
    )
    out = format_for_telegram("Тестовый пост", signature_enabled=False)
    assert "Не подошла эта модель" not in out
    assert "[нашем каталоге]" not in out


def test_format_for_vk_adds_catalog_block_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _DummySettingsService(enabled=True, tg_url="", vk_url="https://vk.cc/cS1pH9"),
    )
    out = format_for_vk("Тестовый пост", signature_enabled=False)
    assert "🔄 Не подошла эта модель?" in out
    assert "Подборка б/у товаров: https://vk.cc/cS1pH9" in out


def test_format_for_vk_skips_catalog_block_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _DummySettingsService(enabled=False, tg_url="", vk_url="https://vk.cc/cS1pH9"),
    )
    out = format_for_vk("Тестовый пост", signature_enabled=False)
    assert "🔄 Не подошла эта модель?" not in out
    assert "Подборка б/у товаров:" not in out


def test_format_for_vk_skips_catalog_block_for_invalid_url(monkeypatch):
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _DummySettingsService(enabled=True, tg_url="", vk_url="vk.cc/cS1pH9"),
    )
    out = format_for_vk("Тестовый пост", signature_enabled=False)
    assert "🔄 Не подошла эта модель?" not in out
    assert "Подборка б/у товаров:" not in out
