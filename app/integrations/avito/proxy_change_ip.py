"""Смена внешнего IP у mobileproxy (и совместимых changeip-ссылок)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import aiohttp

logger = logging.getLogger(__name__)

# mobileproxy: минимум 30 с между сменами на один proxy_key.
_MIN_INTERVAL_SEC = 30
# Среднее время смены IP у оператора ~5 с.
_SETTLE_SEC = 5
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
# Residential sticky: ...-session-<id>-sessTime-N:...@host:port
_STICKY_SESSION_RE = re.compile(r"(?i)(-session-)([A-Za-z0-9]+)")

_last_change_monotonic: float = 0.0


def has_sticky_session(proxy: str) -> bool:
    value = (proxy or "").strip()
    return bool(value and _STICKY_SESSION_RE.search(value))


def rotate_sticky_session(proxy: str) -> Optional[str]:
    """Новый session id в login residential-прокси → новый выходной IP."""
    value = (proxy or "").strip()
    if not value or not _STICKY_SESSION_RE.search(value):
        return None
    new_id = secrets.token_hex(5)
    updated, count = _STICKY_SESSION_RE.subn(rf"\g<1>{new_id}", value, count=1)
    if count != 1 or updated == value:
        return None
    return updated


def ensure_json_format(url: str) -> str:
    """Добавить format=json, если его ещё нет в query."""
    value = (url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "format" not in query:
        query["format"] = ["json"]
    flat = [(k, v) for k, values in query.items() for v in values]
    return urlunparse(parsed._replace(query=urlencode(flat)))


def mask_change_ip_url(url: str) -> str:
    """Скрыть proxy_key в отображении настроек."""
    value = (url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if "proxy_key" in query and query["proxy_key"]:
        key = query["proxy_key"][0]
        if len(key) > 8:
            query["proxy_key"] = [f"{key[:4]}…{key[-4:]}"]
        else:
            query["proxy_key"] = ["***"]
    flat = [(k, v) for k, values in query.items() for v in values]
    return urlunparse(parsed._replace(query=urlencode(flat)))


def _parse_change_response(data: Any) -> tuple[bool, Optional[str], str]:
    if not isinstance(data, dict):
        return False, None, "не-JSON ответ"
    status = str(data.get("status") or "").strip().lower()
    code = data.get("code")
    new_ip = data.get("new_ip")
    message = str(data.get("message") or "").strip()
    ok = status in {"ok", "success"} or code == 0
    if ok and new_ip:
        return True, str(new_ip), message or "ok"
    if ok:
        return True, None, message or "ok"
    return False, None, message or f"status={status!r} code={code!r}"


async def change_proxy_ip(
    change_url: str,
    *,
    settle: bool = True,
    force: bool = False,
) -> Optional[str]:
    """GET ссылки смены IP. Возвращает new_ip или None.

    Соблюдает интервал 30 с (если force=False). При успехе ждёт settle ~5 с.
    """
    global _last_change_monotonic

    url = ensure_json_format(change_url)
    if not url:
        # #region agent log
        from app.integrations.avito.debug_agent_log import agent_dbg

        agent_dbg("A", "proxy_change_ip.py:empty", "change_url empty", {})
        # #endregion
        return None

    now = time.monotonic()
    elapsed = now - _last_change_monotonic
    if not force and _last_change_monotonic and elapsed < _MIN_INTERVAL_SEC:
        left = _MIN_INTERVAL_SEC - elapsed
        logger.info(
            "Proxy IP change skipped: cooldown %.0fs left",
            left,
        )
        # #region agent log
        from app.integrations.avito.debug_agent_log import agent_dbg

        agent_dbg(
            "A",
            "proxy_change_ip.py:cooldown",
            "IP change skipped cooldown",
            {"left_sec": round(left, 1)},
        )
        # #endregion
        return None

    # #region agent log
    from app.integrations.avito.debug_agent_log import agent_dbg

    parsed_host = urlparse(url).hostname or ""
    agent_dbg(
        "A",
        "proxy_change_ip.py:start",
        "IP change request start",
        {"host": parsed_host, "has_format_json": "format=json" in url},
    )
    # #endregion

    timeout = aiohttp.ClientTimeout(total=45)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                headers={
                    "Accept": "application/json,text/plain,*/*",
                    "User-Agent": _BROWSER_UA,
                },
            ) as response:
                text = await response.text()
                if response.status >= 400:
                    logger.warning(
                        "Proxy IP change HTTP %s: %s",
                        response.status,
                        (text or "")[:200],
                    )
                    # #region agent log
                    agent_dbg(
                        "A",
                        "proxy_change_ip.py:http_err",
                        "IP change HTTP error",
                        {"status": response.status, "body_prefix": (text or "")[:120]},
                    )
                    # #endregion
                    return None
                data: Any = None
                try:
                    data = json.loads(text) if text else None
                except json.JSONDecodeError:
                    data = text
                ok, new_ip, detail = _parse_change_response(data)
                if not ok:
                    logger.warning("Proxy IP change failed: %s", detail)
                    # #region agent log
                    agent_dbg(
                        "A",
                        "proxy_change_ip.py:fail",
                        "IP change parse fail",
                        {"detail": detail[:200]},
                    )
                    # #endregion
                    return None
                _last_change_monotonic = time.monotonic()
                logger.info("Proxy IP changed: new_ip=%s (%s)", new_ip or "?", detail)
                # #region agent log
                agent_dbg(
                    "A",
                    "proxy_change_ip.py:ok",
                    "IP change ok",
                    {"new_ip": new_ip, "settle": settle},
                )
                # #endregion
                if settle:
                    await asyncio.sleep(_SETTLE_SEC)
                return new_ip
    except Exception as exc:
        logger.warning("Proxy IP change error: %s", exc)
        # #region agent log
        agent_dbg(
            "A",
            "proxy_change_ip.py:exc",
            "IP change exception",
            {"error": type(exc).__name__},
        )
        # #endregion
        return None


async def rotate_egress(
    *,
    proxy: str = "",
    change_url: str = "",
    persist_proxy=None,
) -> tuple[bool, str]:
    """Сменить выходной IP подходящим способом для типа прокси.

    Residential sticky (session- в логине): новый session id + persist.
    Иначе — changeip-ссылка (классический mobile modem).

    Returns:
        (ok, method) method in {session, changeip, none}
    """
    global _last_change_monotonic

    proxy = (proxy or "").strip()
    change_url = (change_url or "").strip()

    if has_sticky_session(proxy):
        now = time.monotonic()
        if _last_change_monotonic and now - _last_change_monotonic < _MIN_INTERVAL_SEC:
            logger.info("Sticky session rotate skipped: cooldown")
            # #region agent log
            from app.integrations.avito.debug_agent_log import agent_dbg

            agent_dbg(
                "A",
                "proxy_change_ip.py:session_cooldown",
                "sticky rotate cooldown",
                {},
            )
            # #endregion
            return False, "none"
        new_proxy = rotate_sticky_session(proxy)
        if not new_proxy:
            return False, "none"
        if persist_proxy is not None:
            persist_proxy(new_proxy)
        _last_change_monotonic = time.monotonic()
        logger.info("Residential sticky session rotated (new egress IP)")
        # #region agent log
        from app.integrations.avito.debug_agent_log import agent_dbg

        agent_dbg(
            "A",
            "proxy_change_ip.py:session_ok",
            "sticky session rotated",
            {"has_session": has_sticky_session(new_proxy)},
        )
        # #endregion
        await asyncio.sleep(5)
        return True, "session"

    if change_url:
        new_ip = await change_proxy_ip(change_url)
        return (True, "changeip") if new_ip else (False, "none")

    return False, "none"
