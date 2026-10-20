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
from app.utils.vk_public_url import vk_oauth_owner_id, vk_oauth_start_url

logger = logging.getLogger(__name__)

VKID_TOKEN = "https://id.vk.ru/oauth2/auth"
# Обновлять, когда до истечения осталось меньше этого запаса.
REFRESH_SKEW_SEC = 10 * 60
POLL_SEC = 5 * 60

# Необратимые ошибки: не долбить refresh, алертнуть админа.
_FATAL_REFRESH_MARKERS = (
    "invalid_grant",
    "invalid_token",
    "revoked",
    "expired",
    "access_denied",
)

_last_fatal_alert_at = 0.0
_FATAL_ALERT_COOLDOWN_SEC = 6 * 3600


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


def _bound_user_id() -> Optional[int]:
    from app.services.settings_service import get_settings_service

    data = get_settings_service().get_all()
    raw = (data.get("integrations") or {}).get("vk_oauth_user_id")
    try:
        return int(raw) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        return None


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


def _is_fatal_refresh_error(err: object) -> bool:
    text = str(err or "").lower()
    return any(m in text for m in _FATAL_REFRESH_MARKERS)


def _alert_refresh_failure(message: str) -> None:
    global _last_fatal_alert_at
    now = time.time()
    if now - _last_fatal_alert_at < _FATAL_ALERT_COOLDOWN_SEC:
        return
    _last_fatal_alert_at = now
    try:
        from app.config import settings as env_settings
        from app.utils.vk_api_guard import _send_alert_best_effort

        if not getattr(env_settings, "VK_QUOTA_ALERT_ENABLED", True):
            return
        _send_alert_best_effort(
            f"⚠️ VK ID refresh failed\n{message}\n"
            f"Повторите вход: {vk_oauth_start_url()}"
        )
    except Exception:
        logger.warning("VK refresh alert failed", exc_info=True)


def refresh_vk_access_token() -> dict[str, Any]:
    """Обменять refresh_token на новую пару. Сохраняет секреты при успехе."""
    refresh = _get_secret("vk_refresh_token")
    device_id = _device_id()
    app_id = vk_app_id()
    if not refresh:
        return {"ok": False, "error": "нет vk_refresh_token", "fatal": False}
    if not device_id:
        return {
            "ok": False,
            "error": "нет vk_token_device_id — войдите через /vk/oauth/vkid/start",
            "fatal": True,
        }
    if not app_id:
        return {"ok": False, "error": "нет VK_APP_ID", "fatal": True}

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
        fatal = _is_fatal_refresh_error(err)
        logger.error("VK ID refresh failed: %s fatal=%s", err, fatal)
        if fatal:
            _alert_refresh_failure(str(err)[:300])
        return {
            "ok": False,
            "error": str(err)[:300],
            "raw_keys": sorted(data.keys()),
            "fatal": fatal,
        }

    # Сохраняем привязку владельца: user_id из ответа или ранее сохранённый.
    from app.api.endpoints.vk_oauth import _parse_user_id, _store_tokens

    new_uid = _parse_user_id(data)
    bound = _bound_user_id()
    owner = vk_oauth_owner_id()
    expected = owner if owner is not None else bound
    if new_uid is not None and expected is not None and int(new_uid) != int(expected):
        msg = (
            f"refresh user_id={new_uid} не совпадает с владельцем инстанса {expected}"
        )
        logger.error(msg)
        _alert_refresh_failure(msg)
        return {"ok": False, "error": msg, "fatal": True}

    binding_meta = {
        "user_id": new_uid if new_uid is not None else bound,
        "group_id": None,
        "admin_level": None,
        "scope": data.get("scope"),
    }
    try:
        from app.services.settings_service import get_settings_service

        integ = get_settings_service().get_all().get("integrations") or {}
        if integ.get("vk_oauth_group_id") is not None:
            binding_meta["group_id"] = int(integ["vk_oauth_group_id"])
        if integ.get("vk_oauth_admin_level") is not None:
            binding_meta["admin_level"] = int(integ["vk_oauth_admin_level"])
    except Exception:
        pass

    saved = _store_tokens(data, device_id, binding_meta=binding_meta)
    logger.info(
        "VK ID token refreshed, expires_in=%s saved=%s user_id=%s",
        data.get("expires_in"),
        saved,
        binding_meta.get("user_id"),
    )
    return {
        "ok": True,
        "expires_in": data.get("expires_in"),
        "scope": data.get("scope"),
        "saved": saved,
        "user_id": binding_meta.get("user_id"),
    }


def ensure_fresh_vk_token() -> Optional[str]:
    """При необходимости обновить токен. Возвращает краткий итог для логов."""
    if not needs_refresh():
        return None
    result = refresh_vk_access_token()
    if result.get("ok"):
        return f"refreshed expires_in={result.get('expires_in')}"
    prefix = "refresh_fatal" if result.get("fatal") else "refresh_failed"
    return f"{prefix}: {result.get('error')}"
