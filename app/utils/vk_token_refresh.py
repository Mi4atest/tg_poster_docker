"""Обновление Access Token VK ID через Refresh Token (1 час / 180 дней).

Документация: id.vk.ru — grant_type=refresh_token на id.vk.ru/oauth2/auth.
Старый refresh после обмена недействителен — всегда сохраняем новую пару.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Optional

import requests

from app.utils.vk_client import vk_app_id

logger = logging.getLogger(__name__)

VKID_TOKEN = "https://id.vk.ru/oauth2/auth"
# Обновлять, когда до истечения осталось меньше этого запаса.
REFRESH_SKEW_SEC = 10 * 60
POLL_SEC = 5 * 60


def _get_secret(name: str) -> str:
    from app.services.settings_service import get_settings_service

    return str(get_settings_service().get_secret(name) or "").strip()


def _device_id() -> str:
    from app.services.settings_service import get_settings_service

    data = get_settings_service().get_all()
    return str((data.get("integrations") or {}).get("vk_token_device_id") or "").strip()


def _expires_at() -> int:
    from app.services.settings_service import get_settings_service

    data = get_settings_service().get_all()
    try:
        return int((data.get("integrations") or {}).get("vk_token_expires_at") or 0)
    except (TypeError, ValueError):
        return 0


def needs_refresh(*, now: Optional[float] = None) -> bool:
    """True, если это VK ID-токен с refresh и скоро истечёт (или уже истёк)."""
    token = _get_secret("vk_access_token")
    refresh = _get_secret("vk_refresh_token")
    if not refresh or not token.startswith("vk2.a."):
        return False
    exp = _expires_at()
    if exp <= 0:
        return True
    return float(now if now is not None else time.time()) >= exp - REFRESH_SKEW_SEC


def refresh_vk_access_token() -> dict[str, Any]:
    """Обменять refresh_token на новую пару. Сохраняет секреты при успехе."""
    refresh = _get_secret("vk_refresh_token")
    device_id = _device_id()
    app_id = vk_app_id()
    if not refresh:
        return {"ok": False, "error": "нет vk_refresh_token"}
    if not device_id:
        return {"ok": False, "error": "нет vk_token_device_id — войдите через /vk/oauth/vkid/start"}
    if not app_id:
        return {"ok": False, "error": "нет VK_APP_ID"}

    state = secrets.token_urlsafe(16)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": app_id,
        "device_id": device_id,
        "state": state,
    }
    try:
        from app.services.settings_service import get_settings_service

        secret = str(get_settings_service().get_secret("vk_app_secret") or "").strip()
        if secret:
            payload["client_secret"] = secret
    except Exception:
        pass

    resp = requests.post(
        VKID_TOKEN,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    data = resp.json()
    if not data.get("access_token"):
        err = data.get("error") or data.get("error_description") or data
        logger.error("VK ID refresh failed: %s", err)
        return {"ok": False, "error": str(err)[:300], "raw_keys": sorted(data.keys())}

    from app.api.endpoints.vk_oauth import _store_tokens

    saved = _store_tokens(data, device_id)
    logger.info(
        "VK ID token refreshed, expires_in=%s saved=%s",
        data.get("expires_in"),
        saved,
    )
    # #region agent log
    try:
        from app.utils.vk_debug_log import vk_dbg

        vk_dbg(
            "R",
            "vk_token_refresh.py:refresh_vk_access_token",
            "VK ID refresh OK",
            {
                "expires_in": data.get("expires_in"),
                "scope": str(data.get("scope") or "")[:120],
                "prefix": str(data.get("access_token") or "")[:6],
            },
            run_id="post-fix",
        )
    except Exception:
        pass
    # #endregion
    return {
        "ok": True,
        "expires_in": data.get("expires_in"),
        "scope": data.get("scope"),
        "saved": saved,
    }


def ensure_fresh_vk_token() -> Optional[str]:
    """При необходимости обновить токен. Возвращает краткий итог для логов."""
    if not needs_refresh():
        return None
    result = refresh_vk_access_token()
    if result.get("ok"):
        return f"refreshed expires_in={result.get('expires_in')}"
    return f"refresh_failed: {result.get('error')}"
