"""Inline-клавиатура для постов в Telegram-канал (кнопка «Каталог б/у»)."""

from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from aiogram.enums import ButtonStyle
except ImportError:  # pragma: no cover — старый aiogram без Bot API 9.4+
    ButtonStyle = None  # type: ignore[misc, assignment]


def normalize_catalog_url(raw: str) -> str:
    return (raw or "").strip()


def is_valid_catalog_button_url(raw: str) -> bool:
    """Допустимы только http(s) с непустым host (для InlineKeyboardButton.url)."""
    from urllib.parse import urlparse

    u = urlparse(normalize_catalog_url(raw))
    return u.scheme in ("http", "https") and bool(u.netloc)


def build_used_catalog_reply_markup(url: str) -> Optional[InlineKeyboardMarkup]:
    u = normalize_catalog_url(url)
    if not u or not is_valid_catalog_button_url(u):
        return None
    kwargs = {"text": "🔍 Каталог б/у", "url": u}
    if ButtonStyle is not None:
        kwargs["style"] = ButtonStyle.PRIMARY
    btn = InlineKeyboardButton(**kwargs)
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])
