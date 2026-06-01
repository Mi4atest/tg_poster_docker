"""VK API sessions: community token (стена) vs market token (market.*)."""
import json
import time
from typing import Optional

import vk_api

from app.config.settings import VK_ACCESS_TOKEN, VK_MARKET_ACCESS_TOKEN

_DEBUG_LOG = "/root/tg_poster_docker/.cursor/debug-23bea4.log"


def market_token() -> str:
    """Токен для market.* (user vk1.a с scope market). Fallback — VK_ACCESS_TOKEN."""
    raw = (VK_MARKET_ACCESS_TOKEN or "").strip() or (VK_ACCESS_TOKEN or "")
    while raw.startswith("VK_MARKET_ACCESS_TOKEN="):
        raw = raw.split("=", 1)[1].strip()
    return raw


def community_token() -> str:
    """Токен сообщества для wall/photos от группы."""
    return VK_ACCESS_TOKEN or ""


def market_token_source() -> str:
    if (VK_MARKET_ACCESS_TOKEN or "").strip():
        return "VK_MARKET_ACCESS_TOKEN"
    return "VK_ACCESS_TOKEN"


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
