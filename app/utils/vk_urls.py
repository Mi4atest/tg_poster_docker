"""Публичные и API-хосты VK: vk.ru вместо vk.com.

Пользовательские ссылки (market/wall/stories/каналы) строим на vk.ru.
API: api.vk.ru (совместим с api.vk.com; библиотека vk_api>=11.10 уже на .ru).
"""
from __future__ import annotations

import re
from typing import Optional

# Пользовательский веб
VK_WEB_HOST = "vk.ru"
VK_WEB_ORIGIN = f"https://{VK_WEB_HOST}"

# API / OAuth / ID (проверено: отвечают так же, как *.vk.com)
VK_API_HOST = "api.vk.ru"
VK_API_ORIGIN = f"https://{VK_API_HOST}"
VK_OAUTH_ORIGIN = "https://oauth.vk.ru"
VK_ID_ORIGIN = "https://id.vk.ru"
VK_DEV_ORIGIN = "https://dev.vk.ru"

# Хосты, которые безопасно переписывать .com → .ru в URL/тексте настроек.
# Не трогаем произвольный текст без хоста (например corp.vk.com в mailto — отдельно).
_REWRITE_HOSTS = (
    "www.vk.com",
    "m.vk.com",
    "api.vk.com",
    "oauth.vk.com",
    "id.vk.com",
    "dev.vk.com",
    "login.vk.com",
    "vk.com",
)

_HOST_RE = re.compile(
    r"(?P<prefix>https?://)?(?P<host>"
    + "|".join(re.escape(h) for h in _REWRITE_HOSTS)
    + r")(?P<rest>/|\?|#|$)",
    re.IGNORECASE,
)


def rewrite_vk_com_to_ru(value: Optional[str]) -> Optional[str]:
    """Заменить хосты *.vk.com на *.vk.ru в строке URL (или None)."""
    if value is None:
        return None
    text = str(value)
    if "vk.com" not in text.lower():
        return text

    def _sub(m: re.Match) -> str:
        host = m.group("host").lower()
        prefix = m.group("prefix") or ""
        rest = m.group("rest")
        if host == "www.vk.com":
            new_host = VK_WEB_HOST
        elif host == "m.vk.com":
            new_host = "m.vk.ru"
        elif host == "api.vk.com":
            new_host = VK_API_HOST
        elif host == "oauth.vk.com":
            new_host = "oauth.vk.ru"
        elif host == "id.vk.com":
            new_host = "id.vk.ru"
        elif host == "dev.vk.com":
            new_host = "dev.vk.ru"
        elif host == "login.vk.com":
            new_host = "login.vk.ru"
        else:
            new_host = VK_WEB_HOST
        return f"{prefix}{new_host}{rest}"

    return _HOST_RE.sub(_sub, text)


def market_product_url(
    group_id: int,
    vk_product_id: int,
    *,
    owner_id: Optional[int] = None,
) -> str:
    """Ссылка на товар VK Market."""
    gid = abs(int(group_id))
    oid = abs(int(owner_id)) if owner_id is not None else gid
    pid = int(vk_product_id)
    return f"{VK_WEB_ORIGIN}/market-{gid}?w=product-{oid}_{pid}"


def wall_post_url(owner_id: int, post_id: int) -> str:
    """Ссылка на пост стены."""
    return f"{VK_WEB_ORIGIN}/wall{int(owner_id)}_{int(post_id)}"


def story_url(owner_id: int, story_id: int) -> str:
    """Ссылка на историю VK."""
    return f"{VK_WEB_ORIGIN}/stories{int(owner_id)}_{int(story_id)}"


def api_method_url(method: str) -> str:
    """URL метода VK API."""
    return f"{VK_API_ORIGIN}/method/{method.lstrip('/')}"
