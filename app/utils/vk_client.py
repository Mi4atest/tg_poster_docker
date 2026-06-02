"""VK API sessions: community token (стена) vs market token (market.*)."""
import json
import time
from typing import Optional

import vk_api

from app.config.settings import VK_ACCESS_TOKEN, VK_GROUP_ID, VK_MARKET_ACCESS_TOKEN
from app.services.settings_service import get_settings_service

_DEBUG_LOG = "/root/tg_poster_docker/.cursor/debug-23bea4.log"


def market_token() -> str:
    """Токен для market.*: .env -> DB secret -> fallback на community token."""
    raw = (
        (VK_MARKET_ACCESS_TOKEN or "").strip()
        or _get_secret("vk_market_access_token")
        or (VK_ACCESS_TOKEN or "").strip()
        or _get_secret("vk_access_token")
    )
    while raw.startswith("VK_MARKET_ACCESS_TOKEN="):
        raw = raw.split("=", 1)[1].strip()
    return raw


def community_token() -> str:
    """Токен сообщества: .env -> DB secret."""
    return (VK_ACCESS_TOKEN or "").strip() or _get_secret("vk_access_token")


def resolve_vk_group_id() -> str:
    """ID VK-группы: .env -> DB integrations.vk_group_id."""
    env_gid = str(VK_GROUP_ID or "").strip()
    if env_gid:
        return env_gid
    return _get_integration_value("vk_group_id")


def market_token_source() -> str:
    if (VK_MARKET_ACCESS_TOKEN or "").strip():
        return "VK_MARKET_ACCESS_TOKEN"
    if _get_secret("vk_market_access_token"):
        return "db.secret.vk_market_access_token"
    if (VK_ACCESS_TOKEN or "").strip():
        return "VK_ACCESS_TOKEN"
    if _get_secret("vk_access_token"):
        return "db.secret.vk_access_token"
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


def agent_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: Optional[dict] = None,
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "23bea4",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # #endregion
