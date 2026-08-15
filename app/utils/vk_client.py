"""VK API sessions: community token (стена) vs market token (market.*)."""
from typing import Optional

import vk_api

from app.config.settings import VK_ACCESS_TOKEN, VK_GROUP_ID, VK_MARKET_ACCESS_TOKEN
from app.services.settings_service import get_settings_service


def market_token() -> str:
    """Токен для market.*: секрет из настроек (БД) -> .env fallback.

    Поле «Токен VK» в боте пишет vk_access_token — его берём первым,
    иначе свежий токен из UI перекрывается протухшим bootstrap vk_market_access_token.
    """
    raw = (
        _get_secret("vk_access_token")
        or _get_secret("vk_market_access_token")
        or (VK_MARKET_ACCESS_TOKEN or "").strip()
        or (VK_ACCESS_TOKEN or "").strip()
    )
    while raw.startswith("VK_MARKET_ACCESS_TOKEN="):
        raw = raw.split("=", 1)[1].strip()
    return raw


def community_token() -> str:
    """Токен сообщества: секрет из настроек (БД) -> .env fallback."""
    return _get_secret("vk_access_token") or (VK_ACCESS_TOKEN or "").strip()


def resolve_vk_group_id() -> str:
    """ID VK-группы: .env -> DB integrations.vk_group_id."""
    env_gid = str(VK_GROUP_ID or "").strip()
    if env_gid:
        return env_gid
    return _get_integration_value("vk_group_id")


def resolved_vk_group_id_int() -> int:
    """Числовой ID группы VK; ошибка, если не задан ни в .env, ни в настройках."""
    raw = resolve_vk_group_id()
    if not raw:
        raise ValueError(
            "VK group ID is not configured (set VK_GROUP_ID in .env or vk_group_id in bot settings)"
        )
    return abs(int(raw))


def market_token_source() -> str:
    if _get_secret("vk_access_token"):
        return "db.secret.vk_access_token"
    if _get_secret("vk_market_access_token"):
        return "db.secret.vk_market_access_token"
    if (VK_MARKET_ACCESS_TOKEN or "").strip():
        return "VK_MARKET_ACCESS_TOKEN"
    if (VK_ACCESS_TOKEN or "").strip():
        return "VK_ACCESS_TOKEN"
    return "missing"


def _get_secret(name: str) -> str:
    try:
        return str(get_settings_service().get_secret(name) or "").strip()
    except Exception:
        return ""


def _get_integration_value(name: str) -> str:
    try:
        settings = get_settings_service().get_all()
        return str(settings.get("integrations", {}).get(name) or "").strip()
    except Exception:
        return ""


def get_market_vk_session(api_version: str = "5.199") -> vk_api.VkApi:
    return vk_api.VkApi(token=market_token(), api_version=api_version)


def get_community_vk_session(api_version: str = "5.199") -> vk_api.VkApi:
    return vk_api.VkApi(token=community_token(), api_version=api_version)


def vk_api_error_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "code", None)
    if code is not None:
        return int(code)
    err = getattr(exc, "error", None)
    if isinstance(err, dict) and err.get("error_code") is not None:
        return int(err["error_code"])
    return None
