"""Один HTTP GET с TLS-отпечатком браузера (curl_cffi), как требует SPFA."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Запасные impersonate, если SPFA вернул неизвестный для текущей curl_cffi.
_IMPERSONATE_FALLBACKS = (
    "chrome131_android",
    "chrome131",
    "chrome124",
    "chrome120",
)


class BrowserFetchError(RuntimeError):
    """Сетевая/транспортная ошибка curl_cffi."""


def resolve_impersonate(preferred: Optional[str]) -> str:
    value = (preferred or "").strip()
    if value:
        return value
    return _IMPERSONATE_FALLBACKS[0]


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

    clean_headers = {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"host", "content-length", "connection", "transfer-encoding"}
    }
    proxy_value = (proxy or "").strip() or None
    candidates = []
    preferred = resolve_impersonate(impersonate)
    candidates.append(preferred)
    for item in _IMPERSONATE_FALLBACKS:
        if item not in candidates:
            candidates.append(item)

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
                    timeout=timeout_seconds,
                    allow_redirects=True,
                )
            except Exception as exc:  # noqa: BLE001 — транспорт curl_cffi разнородный
                last_error = exc
                msg = str(exc).lower()
                if "impersonat" in msg or "not supported" in msg:
                    logger.warning("curl_cffi impersonate=%s недоступен: %s", name, exc)
                    continue
                raise BrowserFetchError(f"Запрос через curl_cffi не удался: {exc}") from exc

            content = response.content or b""
            if len(content) > max_bytes:
                raise BrowserFetchError("Ответ Avito превышает допустимый размер")
            charset = "utf-8"
            try:
                # response.text уже декодирован; для лимита смотрим raw.
                text = response.text
            except Exception:
                text = content.decode(charset, errors="replace")
            if name != preferred:
                logger.info("curl_cffi: использован fallback impersonate=%s", name)
            return int(response.status_code), text, str(response.url)

    raise BrowserFetchError(
        f"Не удалось выполнить запрос (impersonate): {last_error}"
    )
