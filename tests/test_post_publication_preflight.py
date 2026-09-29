"""Preflight лимитов текста поста перед постановкой в очередь."""

from app.utils.post_publication_preflight import (
    AVITO_DESCRIPTION_MAX_LENGTH,
    AVITO_TITLE_MAX_LENGTH,
    INSTAGRAM_CAPTION_MAX_LENGTH,
    TELEGRAM_CAPTION_MAX_UNITS,
    TELEGRAM_MESSAGE_MAX_UNITS,
    VK_MESSAGE_MAX_LENGTH,
    check_post_preflight,
    enabled_platforms_for_preflight,
    format_preflight_html,
)
from app.utils.telegram_text_units import telegram_text_units
from app.utils.text_formatter import format_for_telegram


class _FakeSettings:
    _PLATFORMS = ("vk", "telegram", "instagram", "max", "avito")

    def __init__(
        self,
        *,
        enabled: dict | None = None,
        avito_queue: bool = True,
        signature: bool = False,
    ):
        self._enabled = {p: False for p in self._PLATFORMS}
        if enabled:
            self._enabled.update(enabled)
        self._avito_queue = avito_queue
        self._signature = signature

    def is_platform_enabled(self, platform: str) -> bool:
        return bool(self._enabled.get(platform, False))

    def is_avito_queue_allowed(self) -> bool:
        return self._avito_queue

    def is_signature_enabled(self) -> bool:
        return self._signature


def _post(text: str, *, photos=None, videos=None, name="Test") -> dict:
    return {
        "text": text,
        "photos": photos or [],
        "videos": videos or [],
        "name": name,
    }


def test_telegram_text_only_uses_4096_limit(monkeypatch):
    monkeypatch.setattr(
        "app.utils.post_publication_preflight.get_settings_service",
        lambda: _FakeSettings(enabled={"telegram": True}, signature=False),
    )
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _FakeSettings(signature=False),
    )
    text = "Короткий пост\n\n" + "x" * 200
    results = check_post_preflight(_post(text))
    assert len(results) == 1
    tg = results[0]
    assert tg.platform == "telegram"
    assert tg.ok is True
    body = format_for_telegram(text, signature_enabled=False)
    assert tg.detail_ru.startswith(f"~{telegram_text_units(body)} из {TELEGRAM_MESSAGE_MAX_UNITS}")


def test_telegram_with_photo_uses_caption_limit(monkeypatch):
    monkeypatch.setattr(
        "app.utils.post_publication_preflight.get_settings_service",
        lambda: _FakeSettings(enabled={"telegram": True}, signature=False),
    )
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _FakeSettings(signature=False),
    )
    text = "Заголовок\n\n" + "y" * 900
    results = check_post_preflight(_post(text, photos=["file_id"]))
    tg = next(r for r in results if r.platform == "telegram")
    assert "подпись к фото" in tg.detail_ru
    assert str(TELEGRAM_CAPTION_MAX_UNITS) in tg.detail_ru
    body = format_for_telegram(text, signature_enabled=False)
    if telegram_text_units(body) > TELEGRAM_CAPTION_MAX_UNITS:
        assert tg.ok is False
    else:
        assert tg.ok is True


def test_long_text_fails_vk_and_instagram(monkeypatch):
    monkeypatch.setattr(
        "app.utils.post_publication_preflight.get_settings_service",
        lambda: _FakeSettings(
            enabled={"vk": True, "instagram": True, "telegram": False, "max": False, "avito": False},
            signature=False,
        ),
    )
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _FakeSettings(signature=False),
    )
    text = "iPhone\n\n" + "z" * 5000
    results = check_post_preflight(_post(text))
    by_platform = {r.platform: r for r in results}
    assert by_platform["vk"].ok is False
    assert str(VK_MESSAGE_MAX_LENGTH) in by_platform["vk"].detail_ru
    assert by_platform["instagram"].ok is False
    assert str(INSTAGRAM_CAPTION_MAX_LENGTH) in by_platform["instagram"].detail_ru


def test_avito_title_and_description_limits(monkeypatch):
    monkeypatch.setattr(
        "app.utils.post_publication_preflight.get_settings_service",
        lambda: _FakeSettings(enabled={"avito": True}, signature=False),
    )
    long_title = "🔥" + "iPhone " + "X" * 120 + "🔥"
    text = f"{long_title}\n\n💵Цена: 1000р.\n\n" + "описание " * 2000
    results = check_post_preflight(_post(text, name="fallback"))
    av = next(r for r in results if r.platform == "avito")
    assert av.platform == "avito"
    assert av.ok is False
    assert str(AVITO_TITLE_MAX_LENGTH) in av.detail_ru or str(
        AVITO_DESCRIPTION_MAX_LENGTH
    ) in av.detail_ru


def test_enabled_platforms_filter(monkeypatch):
    svc = _FakeSettings(
        enabled={"vk": True, "telegram": False, "instagram": False, "max": False, "avito": False},
    )
    assert enabled_platforms_for_preflight(svc) == ["vk"]

    svc_avito_off = _FakeSettings(enabled={"avito": True}, avito_queue=False)
    assert enabled_platforms_for_preflight(svc_avito_off) == []


def test_format_preflight_html_shows_hint_when_failed():
    from app.utils.post_publication_preflight import PlatformPreflightResult

    html = format_preflight_html(
        [
            PlatformPreflightResult("vk", "VK", True, "~100 из 4096"),
            PlatformPreflightResult("telegram", "TG", False, "~1200 из 1024"),
        ]
    )
    assert "Проверка перед публикацией" in html
    assert "VK ✅" in html
    assert "TG ❌" in html
    assert "может не опубликоваться" in html


def test_build_post_ready_text_includes_preflight(monkeypatch):
    monkeypatch.setattr(
        "app.utils.post_publication_preflight.get_settings_service",
        lambda: _FakeSettings(enabled={"vk": True}, signature=False),
    )
    monkeypatch.setattr(
        "app.utils.text_formatter.get_settings_service",
        lambda: _FakeSettings(signature=False),
    )
    from app.bot.utils.post_success_ui import build_post_ready_text

    post = _post("Короткий\n\n💵Цена: 1р.")
    text = build_post_ready_text("Имя", 0, 0, post=post)
    assert "Проверка перед публикацией" in text
    assert "VK ✅" in text
