"""Публичные URL инстанса для VK OAuth (без хардкода домена)."""
from __future__ import annotations

from typing import Optional

from app.config import settings as env_settings


def public_base_url() -> str:
    """HTTPS-база текущего инстанса: PUBLIC_BASE_URL → AVITO_FEED → дефолт."""
    raw = (
        getattr(env_settings, "PUBLIC_BASE_URL", None)
        or getattr(env_settings, "AVITO_FEED_PUBLIC_BASE_URL", None)
        or "https://appleshop.ap43.ru"
    )
    return str(raw).strip().rstrip("/")


def vk_oauth_redirect_uri() -> str:
    return f"{public_base_url()}/vk/oauth/callback"


def vk_oauth_start_url() -> str:
    return f"{public_base_url()}/vk/oauth/vkid/start"


def vk_oauth_help_url() -> str:
    return f"{public_base_url()}/vk/oauth/help"


def vk_oauth_owner_id() -> Optional[int]:
    """Назначенный владелец токена инстанса из env (None = проверка отключена)."""
    value = getattr(env_settings, "VK_OAUTH_OWNER_ID", None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
