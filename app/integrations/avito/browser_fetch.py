"""Один HTTP GET с TLS-отпечатком браузера (curl_cffi), как требует SPFA."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_DROP_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}
_ANDROID_FALLBACK = "chrome131_android"
_DESKTOP_FALLBACK = "chrome131"


class BrowserFetchError(RuntimeError):
    """Сетевая/транспортная ошибка curl_cffi."""


def impersonate_is_android(name: str) -> bool:
    value = (name or "").strip().lower()
    return value.endswith("_android") or "android" in value


def impersonate_candidates(preferred: Optional[str]) -> list[str]:
    """Близкий fallback той же платформы. Не смешивать Android TLS с desktop UA."""
    value = (preferred or "").strip()
    if not value:
        return [_ANDROID_FALLBACK]
    names = [value]
    fallback = _ANDROID_FALLBACK if impersonate_is_android(value) else _DESKTOP_FALLBACK
    if fallback not in names:
        names.append(fallback)
    return names


def resolve_impersonate(preferred: Optional[str]) -> str:
    return impersonate_candidates(preferred)[0]


def canonicalize_headers(headers: Optional[dict[str, str]]) -> dict[str, str]:
    """Нижний регистр, без дублей Accept/accept. Как отдаёт SPFA fingerprint.headers."""
    result: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if not key or value is None:
            continue
        lowered = key.strip().lower()
        if not lowered or lowered in _DROP_HEADERS:
            continue
        result[lowered] = str(value)
    return result


def spfa_request_headers(
    fingerprint_headers: Optional[dict[str, str]],
    *,
    user_agent: str,
) -> dict[str, str]:
    """Только заголовки сессии SPFA + UA. Без своего Accept/json."""
    headers = canonicalize_headers(fingerprint_headers)
    ua = (user_agent or "").strip()
    if ua:
        headers["user-agent"] = ua
    if "accept" not in headers:
        headers["accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )
    if "accept-language" not in headers:
        headers["accept-language"] = "ru-RU,ru;q=0.9"
    return headers


async def browser_get(
    url: str,
    *,
    headers: dict[str, str],
    cookies: Optional[dict[str, str]] = None,
    proxy: Optional[str] = None,
    impersonate: Optional[str] = None,
    timeout_seconds: int = 20,
    max_bytes: int = 4_000_000,
) -> tuple[int, str, str]:
    """Вернуть (status, text, final_url). Ровно один GET, без ретраев."""
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError as exc:
        raise BrowserFetchError(
            "Не установлен curl_cffi — нужен для TLS-impersonate SPFA"
        ) from exc

    clean_headers = canonicalize_headers(headers)
    proxy_value = (proxy or "").strip() or None
    candidates = impersonate_candidates(impersonate)
    preferred = candidates[0]

    last_error: Optional[BaseException] = None
    async with AsyncSession() as session:
        for name in candidates:
            try:
                response = await session.get(
                    url,
                    headers=clean_headers,
                    cookies=cookies or {},
                    proxy=proxy_value,
                    impersonate=name,
                    default_headers=False,
                    timeout=timeout_seconds,
                    allow_redirects=True,
                )
            except Exception as exc:  # noqa: BLE001 — транспорт curl_cffi разнородный
                last_error = exc
                msg = str(exc).lower()
                if "impersonat" in msg or "not supported" in msg:
                    logger.warning(
                        "Avito market fail code=transport impersonate=%s "
                        "detail=unsupported",
                        name,
                    )
                    continue
                raise BrowserFetchError(f"Запрос через curl_cffi не удался: {exc}") from exc

            content = response.content or b""
            if len(content) > max_bytes:
                raise BrowserFetchError("Ответ Avito превышает допустимый размер")
            try:
                text = response.text
            except Exception:
                text = content.decode("utf-8", errors="replace")
            if name != preferred:
                logger.warning(
                    "Avito market note code=transport impersonate_requested=%s "
                    "impersonate_used=%s tls_may_mismatch=1",
                    preferred,
                    name,
                )
            return int(response.status_code), text, str(response.url)

    raise BrowserFetchError(
        f"Не удалось выполнить запрос (impersonate): {last_error}"
    )
