"""Клиент SPFA: cookies и преобразование URL поиска Avito."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aiohttp

from app.config.settings import BASE_DIR

logger = logging.getLogger(__name__)

SPFA_API_BASE = "https://spfa.pro/api"
DEFAULT_COOKIE_CACHE_PATH = BASE_DIR / "media" / "avito_market_cache" / "spfa_cookies.json"


class SpfaError(RuntimeError):
    """Ошибка SPFA API."""


class SpfaBalanceError(SpfaError):
    """Недостаточно средств или неверный ключ."""


@dataclass(frozen=True)
class SpfaCookies:
    cookie_id: str
    cookies: dict[str, str]
    user_agent: str
    fingerprint: dict[str, Any]
    mobile: bool
    purchased_at: float

    @property
    def impersonate(self) -> Optional[str]:
        value = self.fingerprint.get("impersonate")
        return str(value) if value else None

    @property
    def headers(self) -> dict[str, str]:
        raw = self.fingerprint.get("headers")
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items() if v is not None}
        return {"user-agent": self.user_agent}


def proxy_url(proxy: str) -> str:
    """Нормализовать proxy-строку к URL для aiohttp."""
    value = (proxy or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    return value


def validate_proxy_for_mobile(proxy: str) -> str:
    value = (proxy or "").strip()
    if not value or "@" not in value:
        raise SpfaError(
            "Для mobile cookies SPFA нужен прокси вида login:password@host:port"
        )
    return value


class SpfaClient:
    def __init__(
        self,
        api_key: str,
        *,
        proxy: str = "",
        cache_path: Optional[Path] = None,
        cookie_ttl_sec: int = 11 * 3600,
    ) -> None:
        self.api_key = api_key.strip()
        self.proxy = (proxy or "").strip()
        self.cache_path = Path(cache_path or DEFAULT_COOKIE_CACHE_PATH)
        self.cookie_ttl_sec = cookie_ttl_sec
        self._last_unblock_error: str = ""

    def last_unblock_was_permanent(self) -> bool:
        """410/404/истёк срок — кэш можно сбрасывать; transient — нет."""
        msg = (self._last_unblock_error or "").lower()
        return any(
            marker in msg
            for marker in (
                "не найден",
                "12 час",
                "больше 12",
                "gone",
                "410",
                "404",
                "not found",
            )
        )
    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{SPFA_API_BASE}{path}"
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "tg-poster-market/1.0",
                },
            ) as response:
                text = await response.text()
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError as exc:
                    raise SpfaError(f"SPFA вернул не-JSON ({response.status})") from exc
                if response.status in {401, 403}:
                    raise SpfaBalanceError(
                        data.get("message")
                        or f"SPFA отказал в доступе HTTP {response.status}"
                    )
                if response.status >= 400:
                    raise SpfaError(
                        data.get("message") or f"SPFA HTTP {response.status}: {text[:300]}"
                    )
                if isinstance(data, dict) and data.get("success") is False:
                    raise SpfaError(str(data.get("message") or "SPFA success=false"))
                return data if isinstance(data, dict) else {"results": data}

    async def get_balance(self) -> float:
        data = await self._post("/balance/", {"api_key": self.api_key})
        return float(data.get("balance") or 0)

    async def convert_web_url(self, web_url: str) -> str:
        data = await self._post("/avito-url/", {"url": web_url})
        api_url = data.get("api_url")
        if not api_url:
            raise SpfaError("SPFA не вернул api_url")
        return str(api_url)

    def _load_cache(self) -> Optional[SpfaCookies]:
        if not self.cache_path.exists():
            return None
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        purchased_at = float(raw.get("purchased_at") or 0)
        if purchased_at and time.time() - purchased_at > self.cookie_ttl_sec:
            return None
        cookies = raw.get("cookies")
        if not isinstance(cookies, dict) or not cookies:
            return None
        user_agent = str(raw.get("user_agent") or "").strip()
        if not user_agent:
            return None
        return SpfaCookies(
            cookie_id=str(raw.get("id") or ""),
            cookies={str(k): str(v) for k, v in cookies.items()},
            user_agent=user_agent,
            fingerprint=raw.get("fingerprint") if isinstance(raw.get("fingerprint"), dict) else {},
            mobile=bool(raw.get("mobile")),
            purchased_at=purchased_at or time.time(),
        )

    def _save_cache(self, cookies: SpfaCookies) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": cookies.cookie_id,
            "cookies": cookies.cookies,
            "user_agent": cookies.user_agent,
            "fingerprint": cookies.fingerprint,
            "mobile": cookies.mobile,
            "purchased_at": cookies.purchased_at,
            "proxy_fingerprint": bool(self.proxy),
        }
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    async def purchase_cookies(self, *, mobile: bool) -> SpfaCookies:
        if mobile:
            proxy = validate_proxy_for_mobile(self.proxy)
            path = "/cookies/mobile/"
            payload: dict[str, Any] = {
                "api_key": self.api_key,
                "mobile": True,
                "proxy": proxy,
            }
        else:
            path = "/cookies/"
            payload = {"api_key": self.api_key}
            if self.proxy:
                payload["proxy"] = self.proxy

        data = await self._post(path, payload)
        results = data.get("results") or {}
        cookies = results.get("cookies")
        if not isinstance(cookies, dict) or not cookies:
            raise SpfaError("SPFA вернул пустые cookies")
        fingerprint = (
            results.get("fingerprint")
            if isinstance(results.get("fingerprint"), dict)
            else {}
        )
        headers = (
            fingerprint.get("headers")
            if isinstance(fingerprint.get("headers"), dict)
            else {}
        )
        user_agent = str(
            results.get("user_agent") or headers.get("user-agent") or ""
        ).strip()
        if not user_agent:
            raise SpfaError("SPFA не вернул user-agent")
        item = SpfaCookies(
            cookie_id=str(results.get("id") or ""),
            cookies={str(k): str(v) for k, v in cookies.items()},
            user_agent=user_agent,
            fingerprint=fingerprint,
            mobile=bool(
                results.get("mobile") if results.get("mobile") is not None else mobile
            ),
            purchased_at=time.time(),
        )
        self._save_cache(item)
        logger.info("SPFA cookies purchased id=%s mobile=%s", item.cookie_id, item.mobile)
        return item

    async def get_cookies(self, *, prefer_mobile: bool) -> SpfaCookies:
        cached = self._load_cache()
        if cached is not None:
            if prefer_mobile and not cached.mobile and self.proxy:
                pass
            else:
                return cached
        return await self.purchase_cookies(mobile=prefer_mobile and bool(self.proxy))

    def invalidate_cookie_cache(self) -> None:
        """Сбросить локальный кэш cookies."""
        try:
            if self.cache_path.exists():
                self.cache_path.unlink()
        except OSError as exc:
            logger.warning("Не удалось удалить кэш SPFA cookies: %s", exc)

    async def unblock(
        self,
        cookie_id: str,
        *,
        previous: Optional[SpfaCookies] = None,
    ) -> Optional[SpfaCookies]:
        """Бесплатно обновить cookies. При api_key возвращает актуальные значения.

        Сохраняет UA/fingerprint из previous (или из локального кэша).
        Новый cookie не покупается.
        """
        if not cookie_id:
            return None
        self._last_unblock_error = ""
        base = previous or self._load_cache()
        payload: dict[str, Any] = {"id": int(cookie_id) if str(cookie_id).isdigit() else cookie_id}
        if self.api_key:
            payload["api_key"] = self.api_key
        if self.proxy:
            payload["proxy"] = self.proxy
        try:
            data = await self._post("/unblock/", payload)
        except SpfaError as exc:
            self._last_unblock_error = str(exc)
            logger.warning("SPFA unblock failed id=%s: %s", cookie_id, exc)
            return None

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, dict):
            self._last_unblock_error = "no results in response"
            logger.info("SPFA unblock ok id=%s (cookies в ответе нет)", cookie_id)
            return None
        raw_cookies = results.get("cookies")
        if not isinstance(raw_cookies, dict) or not raw_cookies:
            self._last_unblock_error = "empty cookies in results"
            logger.info("SPFA unblock ok id=%s без cookies в results", cookie_id)
            return None

        user_agent = ""
        fingerprint: dict[str, Any] = {}
        mobile = False
        purchased_at = time.time()
        if base is not None:
            user_agent = base.user_agent
            fingerprint = dict(base.fingerprint or {})
            mobile = base.mobile
            purchased_at = base.purchased_at or purchased_at
        headers = (
            fingerprint.get("headers")
            if isinstance(fingerprint.get("headers"), dict)
            else {}
        )
        if not user_agent:
            user_agent = str(headers.get("user-agent") or "").strip()
        if not user_agent:
            # fallback для unblock без локального кэша (сухой прогон / после сброса)
            user_agent = (
                "Mozilla/5.0 (Linux; Android 15; SM-S938B) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.7178.123 Mobile Safari/537.36"
            )
            fingerprint = {
                "impersonate": "chrome131_android",
                "headers": {"user-agent": user_agent},
            }
            mobile = True

        refreshed = SpfaCookies(
            cookie_id=str(results.get("id") or cookie_id),
            cookies={str(k): str(v) for k, v in raw_cookies.items()},
            user_agent=user_agent,
            fingerprint=fingerprint,
            mobile=mobile,
            purchased_at=purchased_at,
        )
        self._save_cache(refreshed)
        logger.info(
            "SPFA cookies refreshed via unblock id=%s keys=%s",
            refreshed.cookie_id,
            len(refreshed.cookies),
        )
        return refreshed
