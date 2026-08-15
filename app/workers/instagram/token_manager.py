import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import aiohttp

from app.config import settings as env_settings
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


def _is_api_access_blocked(error: str) -> bool:
    return "API access blocked" in (error or "")


class InstagramGraphTokenManager:
    def __init__(self) -> None:
        self.settings_service = get_settings_service()
        self.api_version = (env_settings.INSTAGRAM_GRAPH_API_VERSION or "v19.0").strip()
        self.timeout_seconds = int(getattr(env_settings, "INSTAGRAM_GRAPH_TIMEOUT_SECONDS", 60) or 60)
        self.refresh_before_days = int(getattr(env_settings, "INSTAGRAM_GRAPH_REFRESH_BEFORE_DAYS", 7) or 7)

    def _base_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}"

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_datetime(self, value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _get_integrations(self) -> dict:
        return self.settings_service.get_all().get("integrations", {})

    def _update_integrations(self, patch: dict) -> None:
        self.settings_service.update({"integrations": patch})

    def _store_access_token(self, token: str, meta: Optional[dict] = None) -> None:
        """Сохраняет User token в encrypted_secrets и чистит устаревшую копию в integrations."""
        value = (token or "").strip()
        self.settings_service.set_secret("instagram_graph_access_token", value)
        patch = {"instagram_graph_access_token": ""}
        if meta:
            patch.update(meta)
        self._update_integrations(patch)

    def get_access_token(self) -> str:
        # Приоритет: secret (бот) → integrations (legacy) → .env
        token_from_secret = (self.settings_service.get_secret("instagram_graph_access_token") or "").strip()
        if token_from_secret:
            return token_from_secret
        token_from_settings = (self._get_integrations().get("instagram_graph_access_token") or "").strip()
        if token_from_settings:
            return token_from_settings
        return (getattr(env_settings, "INSTAGRAM_GRAPH_ACCESS_TOKEN", "") or "").strip()

    def get_ig_user_id(self) -> str:
        user_id = self._get_integrations().get("instagram_graph_user_id") or ""
        if user_id:
            return str(user_id).strip()
        return (getattr(env_settings, "INSTAGRAM_GRAPH_USER_ID", "") or "").strip()

    def get_media_base_url(self) -> str:
        media_base_url = self._get_integrations().get("instagram_graph_media_base_url") or ""
        if media_base_url:
            return media_base_url.rstrip("/")
        return (getattr(env_settings, "INSTAGRAM_GRAPH_MEDIA_BASE_URL", "") or "").rstrip("/")

    def get_app_id(self) -> str:
        app_id = (self._get_integrations().get("instagram_graph_app_id") or "").strip()
        if app_id:
            return app_id
        return (getattr(env_settings, "INSTAGRAM_GRAPH_APP_ID", "") or "").strip()

    def get_app_secret(self) -> str:
        # Приоритет как у access token: secret (бот) → integrations → .env
        secret = (self.settings_service.get_secret("instagram_graph_app_secret") or "").strip()
        if secret:
            return secret
        app_secret = (self._get_integrations().get("instagram_graph_app_secret") or "").strip()
        if app_secret:
            return app_secret
        return (getattr(env_settings, "INSTAGRAM_GRAPH_APP_SECRET", "") or "").strip()

    def _get_app_access_token(self) -> str:
        app_id = self.get_app_id()
        app_secret = self.get_app_secret()
        if not app_id or not app_secret:
            return ""
        return f"{app_id}|{app_secret}"

    async def exchange_to_long_lived_token(
        self, token: str
    ) -> Tuple[bool, str, Optional[str], Optional[datetime]]:
        app_id = self.get_app_id()
        app_secret = self.get_app_secret()
        if not app_id or not app_secret:
            return False, "Не заданы app_id/app_secret для обмена short-lived токена", None, None

        endpoint = f"{self._base_url()}/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": token,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(endpoint, params=params) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    return False, f"Exchange HTTP {response.status}: {payload}", None, None

                new_token = payload.get("access_token")
                if not new_token:
                    return False, f"Exchange response without access_token: {payload}", None, None
                expires_in = payload.get("expires_in")
                expires_at = None
                if expires_in:
                    try:
                        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
                    except Exception:
                        expires_at = None
                return True, "", str(new_token), expires_at

    async def debug_token(self, token: str) -> Tuple[bool, str, Optional[datetime]]:
        app_access_token = self._get_app_access_token()
        if not app_access_token:
            return False, "Не заданы INSTAGRAM_GRAPH_APP_ID/INSTAGRAM_GRAPH_APP_SECRET для /debug_token", None

        endpoint = f"{self._base_url()}/debug_token"
        params = {"input_token": token, "access_token": app_access_token}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(endpoint, params=params) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    return False, f"/debug_token HTTP {response.status}: {payload}", None

                data = (payload or {}).get("data", {})
                is_valid = bool(data.get("is_valid"))
                if not is_valid:
                    return False, f"Токен невалиден: {payload}", None

                expires_at = data.get("expires_at")
                if expires_at:
                    try:
                        expiry_dt = datetime.fromtimestamp(int(expires_at), tz=timezone.utc)
                    except Exception:
                        expiry_dt = None
                else:
                    expiry_dt = None
                return True, "", expiry_dt

    async def refresh_long_lived_token(self, token: str) -> Tuple[bool, str, Optional[str], Optional[datetime]]:
        endpoint = f"{self._base_url()}/refresh_access_token"
        params = {"grant_type": "ig_refresh_token", "access_token": token}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(endpoint, params=params) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    return False, f"Refresh HTTP {response.status}: {payload}", None, None

                new_token = payload.get("access_token") or token
                expires_in = payload.get("expires_in")
                expires_at = None
                if expires_in:
                    try:
                        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
                    except Exception:
                        expires_at = None
                return True, "", str(new_token), expires_at

    async def check_and_refresh_token(self, trigger_alerts: bool = True) -> bool:
        token = self.get_access_token()
        now = datetime.now(timezone.utc)
        if not token:
            self._update_integrations(
                {
                    "instagram_graph_token_last_check_at": self._now_iso(),
                    "instagram_graph_token_last_error": "Отсутствует Instagram Graph access token",
                }
            )
            return False

        valid, error, expires_at = await self.debug_token(token)
        if not valid:
            # Пытаемся one-time обменять short-lived token в long-lived,
            # если токен еще валиден для exchange, но нет long-lived в системе.
            ex_ok, ex_error, ex_token, ex_expires_at = await self.exchange_to_long_lived_token(token)
            if ex_ok and ex_token:
                meta = {
                    "instagram_graph_token_last_check_at": self._now_iso(),
                    "instagram_graph_token_last_error": "",
                }
                if ex_expires_at:
                    meta["instagram_graph_token_expires_at"] = ex_expires_at.isoformat()
                self._store_access_token(ex_token, meta)
                return True

            self._update_integrations(
                {
                    "instagram_graph_token_last_check_at": self._now_iso(),
                    "instagram_graph_token_last_error": ex_error or error,
                }
            )
            return False

        patch = {
            "instagram_graph_token_last_check_at": self._now_iso(),
            "instagram_graph_token_last_error": "",
        }
        if expires_at:
            patch["instagram_graph_token_expires_at"] = expires_at.isoformat()
        self._update_integrations(patch)

        if expires_at:
            days_left = (expires_at - now).total_seconds() / 86400
            if days_left < self.refresh_before_days:
                ok, refresh_error, new_token, new_expires_at = await self.refresh_long_lived_token(token)
                if ok:
                    meta = {
                        "instagram_graph_token_last_error": "",
                        "instagram_graph_token_last_check_at": self._now_iso(),
                    }
                    if new_expires_at:
                        meta["instagram_graph_token_expires_at"] = new_expires_at.isoformat()
                    self._store_access_token(new_token or token, meta)
                    return True

                self._update_integrations(
                    {
                        "instagram_graph_token_last_error": refresh_error,
                        "instagram_graph_token_last_check_at": self._now_iso(),
                    }
                )
                return False

        return True

    async def preflight_or_error(self) -> Tuple[bool, str]:
        token = self.get_access_token()
        user_id = self.get_ig_user_id()
        if not token or not user_id:
            return False, "Graph API не настроен: требуется token и user_id"

        valid, error, expires_at = await self.debug_token(token)
        if not valid:
            if "INSTAGRAM_GRAPH_APP_ID/INSTAGRAM_GRAPH_APP_SECRET" in error:
                self._update_integrations(
                    {
                        "instagram_graph_token_last_check_at": self._now_iso(),
                        "instagram_graph_token_last_error": "Preflight пропущен: не заданы APP_ID/APP_SECRET",
                    }
                )
                return True, ""
            if _is_api_access_blocked(error):
                user_error = (
                    "Meta заблокировала доступ к Graph API приложения (API access blocked, OAuthException #200). "
                    "Это не истёкший токен: проверьте Meta for Developers → ваше приложение → Alerts "
                    "и выполните требуемые действия (верификация бизнеса, App Review, снятие ограничений)."
                )
            else:
                user_error = f"Instagram Graph token невалиден: {error}"
            self._update_integrations(
                {
                    "instagram_graph_token_last_check_at": self._now_iso(),
                    "instagram_graph_token_last_error": user_error,
                }
            )
            return False, user_error

        patch = {
            "instagram_graph_token_last_check_at": self._now_iso(),
            "instagram_graph_token_last_error": "",
        }
        if expires_at:
            patch["instagram_graph_token_expires_at"] = expires_at.isoformat()
            if expires_at <= datetime.now(timezone.utc):
                return False, "Instagram Graph token истек"
        self._update_integrations(patch)
        return True, ""

    def token_expired_by_settings(self) -> bool:
        expires_at_raw = self._get_integrations().get("instagram_graph_token_expires_at") or ""
        expires_at = self._parse_datetime(str(expires_at_raw))
        if not expires_at:
            return False
        return expires_at <= datetime.now(timezone.utc)
