"""Клиент кабинета mobileproxy (mpsapi.com). Токен — из Настроек, не из прокси-строки."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

MOBILEPROXY_API_URL = "https://mpsapi.com/api.html"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class MobileproxyError(RuntimeError):
    """Ошибка API кабинета mobileproxy."""


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("list", "proxies", "data", "result", "results"):
            nested = data.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        if any(key.startswith("proxy_") or key == "proxy_id" for key in data):
            return [data]
    return []


class MobileproxyClient:
    def __init__(self, token: str, *, timeout_seconds: int = 20) -> None:
        self.token = (token or "").strip()
        self.timeout_seconds = timeout_seconds

    async def _get(self, command: str, **params: Any) -> Any:
        if not self.token:
            raise MobileproxyError("Токен API mobileproxy не задан")
        query: dict[str, str] = {"command": command}
        for key, value in params.items():
            if value is None or value == "":
                continue
            query[key] = str(value)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                MOBILEPROXY_API_URL,
                params=query,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": _BROWSER_UA,
                },
            ) as response:
                text = await response.text()
                if response.status == 429:
                    raise MobileproxyError("Слишком частые запросы к кабинету прокси")
                if response.status >= 400:
                    raise MobileproxyError(
                        f"Кабинет прокси HTTP {response.status}: {text[:200]}"
                    )
                try:
                    data = json.loads(text) if text else {}
                except json.JSONDecodeError as exc:
                    raise MobileproxyError("Кабинет прокси вернул не-JSON") from exc
        if isinstance(data, dict):
            status = str(data.get("status") or "").lower()
            if status in {"err", "error"}:
                message = str(data.get("message") or "ошибка кабинета").strip()
                raise MobileproxyError(message)
        return data

    async def get_balance(self) -> float:
        data = await self._get("get_balance")
        if not isinstance(data, dict):
            raise MobileproxyError("Некорректный ответ баланса")
        return float(data.get("balance") or 0)

    async def get_my_proxies(self) -> list[dict[str, Any]]:
        data = await self._get("get_my_proxy")
        return _as_list(data)

    async def get_residential_traffic(
        self,
        *,
        proxy_id: Optional[int] = None,
        days: int = 90,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"days": max(1, min(365, int(days)))}
        if proxy_id is not None:
            params["proxy_id"] = int(proxy_id)
        data = await self._get("residential_traffic", **params)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("traffic", "list", "data", "result"):
                nested = data.get(key)
                if isinstance(nested, list):
                    return [item for item in nested if isinstance(item, dict)]
        return []
