"""Ограниченное чтение одной публичной страницы поиска Avito."""
from __future__ import annotations

import html
import json
import logging
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

import aiohttp

from app.config.settings import (
    AVITO_MARKET_CATEGORY_ID,
    AVITO_MARKET_LOCATION_ID,
)
from app.integrations.avito.browser_fetch import BrowserFetchError, browser_get
from app.integrations.avito.spfa_client import SpfaClient, SpfaCookies, SpfaError, proxy_url
from app.services.settings_service import get_settings_service
from app.utils.iphone_market_query import IphoneMarketQuery


logger = logging.getLogger(__name__)

AVITO_MARKET_ITEMS_URL = "https://www.avito.ru/web/1/js/items"
AVITO_MARKET_MOBILE_API_URL = "https://m.avito.ru/api/11/items"
AVITO_MARKET_WEB_SEARCH_URL = "https://www.avito.ru/all/telefony"
MAX_RESPONSE_BYTES = 4_000_000
# Кэш преобразованных URL (SPFA /avito-url лимит ~2/мин с IP).
_AVITO_URL_CACHE_TTL_SEC = 6 * 3600
_avito_url_cache: dict[str, tuple[float, str]] = {}


class AvitoMarketError(RuntimeError):
    """Базовая ошибка публичной выдачи."""


class AvitoMarketBlockedError(AvitoMarketError):
    """Avito потребовал CAPTCHA или ограничил запросы."""

    def __init__(self, message: str, *, soft: bool = False) -> None:
        super().__init__(message)
        # soft=True: egress уже сменили (sticky session) — короче пауза.
        self.soft = soft


class AvitoMarketParseError(AvitoMarketError):
    """Структура выдачи не содержит распознаваемых карточек."""


@dataclass(frozen=True)
class MarketListing:
    item_id: str
    title: str
    price_rub: int
    url: str = ""
    seller_type: Optional[str] = None
    condition: Optional[str] = None
    description: str = ""
    city: str = ""
    included: Optional[bool] = None
    rejection_reason: Optional[str] = None


class _JsonScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._buffer: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "script":
            return
        values = {key: value or "" for key, value in attrs}
        script_type = values.get("type", "").lower()
        script_id = values.get("id", "").lower()
        marker = values.get("data-marker", "").lower()
        self._capture = (
            script_type in {"application/json", "application/ld+json"}
            or "initial" in script_id
            or "serp" in marker
        )
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            value = "".join(self._buffer).strip()
            if value:
                self.scripts.append(value)
        if tag == "script":
            self._capture = False
            self._buffer = []


def build_market_web_url(query: IphoneMarketQuery) -> str:
    params = urlencode({"q": query.search_text, "s": "104"})
    return f"{AVITO_MARKET_WEB_SEARCH_URL}?{params}"


def build_market_search_url(query: IphoneMarketQuery) -> str:
    """Локальный web-fallback (desktop cookies / без SPFA)."""
    params = urlencode(
        {
            "categoryId": str(AVITO_MARKET_CATEGORY_ID),
            "localPriority": "0",
            "locationId": str(AVITO_MARKET_LOCATION_ID),
            "presentationType": "serp",
            "query": query.search_text,
        }
    )
    return f"{AVITO_MARKET_ITEMS_URL}?{params}"


def build_market_mobile_api_url(query: IphoneMarketQuery) -> str:
    """Локальный fallback под mobile cookies (как SPFA /avito-url → m.avito)."""
    params = urlencode(
        {
            "categoryId": str(AVITO_MARKET_CATEGORY_ID),
            "locationId": str(AVITO_MARKET_LOCATION_ID),
            "query": query.search_text,
        }
    )
    return f"{AVITO_MARKET_MOBILE_API_URL}?{params}"


def _coerce_price(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = int(value)
        return result if result > 0 else None
    if isinstance(value, dict):
        for key in ("value", "amount", "price", "rub"):
            result = _coerce_price(value.get(key))
            if result:
                return result
        return None
    if isinstance(value, str):
        digits = re.sub(r"\D", "", value)
        return int(digits) if digits else None
    return None


def _first_text(data: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return html.unescape(value.strip())
    return ""


def _seller_type(data: dict[str, Any]) -> Optional[str]:
    candidates: list[Any] = [
        data.get("sellerType"),
        data.get("seller_type"),
        data.get("userType"),
    ]
    seller = data.get("seller")
    if isinstance(seller, dict):
        candidates.extend((seller.get("type"), seller.get("userType"), seller.get("isCompany")))
    for value in candidates:
        if isinstance(value, bool):
            return "business" if value else "private"
        if isinstance(value, str):
            normalized = value.lower()
            if any(word in normalized for word in ("company", "business", "shop", "pro", "магаз")):
                return "business"
            if any(word in normalized for word in ("private", "person", "частн")):
                return "private"
    return None


def _condition(data: dict[str, Any]) -> Optional[str]:
    direct = _first_text(data, ("condition", "state"))
    if direct:
        return direct
    iva = data.get("iva")
    if isinstance(iva, dict):
        iva = iva.get("items") or iva.get("values")
    if isinstance(iva, list):
        for item in iva:
            if not isinstance(item, dict):
                continue
            name = _first_text(item, ("name", "title", "label")).lower()
            if "состояни" not in name and "condition" not in name:
                continue
            value = _first_text(item, ("value", "description", "text"))
            if value:
                return value
    return None


def _listing_city(data: dict[str, Any]) -> str:
    geo = data.get("geo")
    if isinstance(geo, dict):
        for key in (
            "city",
            "locationName",
            "addressLocality",
            "address",
            "formattedAddress",
            "name",
        ):
            value = _first_text(geo, (key,))
            if value:
                return value.split(",")[0].strip()
        for nested_key in ("location", "addressDetailed", "geoReference"):
            location = geo.get(nested_key)
            if isinstance(location, dict):
                value = _first_text(
                    location, ("name", "city", "title", "addressLocality")
                )
                if value:
                    return value.split(",")[0].strip()
    for key in ("locationName", "city", "address", "geoAddress", "addressLocality"):
        value = _first_text(data, (key,))
        if value:
            return value.split(",")[0].strip()
    location = data.get("location")
    if isinstance(location, dict):
        value = _first_text(location, ("name", "city", "title"))
        if value:
            return value.split(",")[0].strip()
    return ""


def _listing_from_dict(data: dict[str, Any]) -> Optional[MarketListing]:
    title = _first_text(data, ("title", "name", "itemTitle"))
    price = None
    for key in ("priceDetailed", "price", "priceValue", "value"):
        price = _coerce_price(data.get(key))
        if price:
            break
    raw_id = data.get("id") or data.get("itemId") or data.get("item_id")
    if not title or not price or raw_id is None:
        return None
    item_id = str(raw_id).strip()
    if not item_id:
        return None
    return MarketListing(
        item_id=item_id,
        title=title,
        price_rub=price,
        url=_first_text(data, ("urlPath", "url", "uri")),
        seller_type=_seller_type(data),
        condition=_condition(data),
        description=_first_text(data, ("description", "snippet")),
        city=_listing_city(data),
    )


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _json_candidates(page_html: str) -> Iterable[Any]:
    parser = _JsonScriptParser()
    parser.feed(page_html)
    for script in parser.scripts:
        try:
            yield json.loads(script)
        except (json.JSONDecodeError, TypeError):
            continue

    assignments = re.findall(
        r"(?:window\.)?__(?:initialData|INITIAL_STATE)__\s*=\s*(.+?);?\s*</script>",
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for raw in assignments:
        raw = raw.strip().rstrip(";")
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, str):
                decoded = json.loads(decoded)
            yield decoded
        except (json.JSONDecodeError, TypeError):
            continue


def parse_market_search_html(page_html: str) -> list[MarketListing]:
    lower = page_html.lower()
    if any(
        marker in lower
        for marker in (
            "captcha",
            "подтвердите, что вы не робот",
            "доступ временно ограничен",
            "слишком много запросов",
            "проверка безопасности",
            "pow_challenge",
        )
    ):
        raise AvitoMarketBlockedError("Avito запросил дополнительную проверку")

    listings: dict[str, MarketListing] = {}
    for payload in _json_candidates(page_html):
        for node in _walk_json(payload):
            listing = _listing_from_dict(node)
            if listing is not None:
                listings.setdefault(listing.item_id, listing)
    if not listings:
        raise AvitoMarketParseError("В выдаче Avito не найдены карточки объявлений")
    return list(listings.values())


def parse_market_search_payload(payload: Any) -> list[MarketListing]:
    if not isinstance(payload, dict):
        raise AvitoMarketParseError("Avito вернул некорректный JSON")
    status = str(payload.get("status") or "").lower()
    result = payload.get("result")
    result_text = json.dumps(result, ensure_ascii=False).lower() if result is not None else ""
    if (
        status in {"too-many-requests", "blocked", "captcha"}
        or "captcha" in result_text
        or "pow_challenge" in payload
    ):
        raise AvitoMarketBlockedError("Avito запросил дополнительную проверку")

    candidates: list[Any] = [payload.get("items")]
    for envelope in ("catalog", "data", "result"):
        nested = payload.get(envelope)
        if isinstance(nested, dict):
            candidates.append(nested.get("items"))
    raw_items = next((value for value in candidates if isinstance(value, list)), None)
    if raw_items is None:
        raise AvitoMarketParseError("В JSON Avito отсутствует список объявлений")

    listings: dict[str, MarketListing] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        listing = _listing_from_dict(item)
        if listing is not None:
            listings.setdefault(listing.item_id, listing)
    if not listings:
        raise AvitoMarketParseError("В JSON Avito не найдены карточки объявлений")
    return list(listings.values())


def _parse_response_body(text: str) -> list[MarketListing]:
    if "pow_challenge" in text or "проверка безопасности" in text.lower():
        raise AvitoMarketBlockedError("Avito запросил проверку безопасности")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return parse_market_search_html(text)
    return parse_market_search_payload(payload)


async def _resolve_search_url(
    query: IphoneMarketQuery,
    spfa: Optional[SpfaClient],
    *,
    prefer_mobile: bool = False,
) -> str:
    cache_key = query.cache_key
    cached = _avito_url_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _AVITO_URL_CACHE_TTL_SEC:
        return cached[1]

    if spfa is None:
        return (
            build_market_mobile_api_url(query)
            if prefer_mobile
            else build_market_search_url(query)
        )
    try:
        api_url = await spfa.convert_web_url(build_market_web_url(query))
        _avito_url_cache[cache_key] = (time.monotonic(), api_url)
        return api_url
    except SpfaError as exc:
        logger.warning("SPFA avito-url failed, fallback to local URL: %s", exc)
        return (
            build_market_mobile_api_url(query)
            if prefer_mobile
            else build_market_search_url(query)
        )


async def _handle_block(
    *,
    spfa: Optional[SpfaClient],
    cookies_obj: Optional[SpfaCookies],
    detail: str,
) -> bool:
    """439/CAPTCHA/429 — разблокировка по доке SPFA.

    Residential sticky: новый session в логине прокси (= новый IP).
    Mobile modem: changeip-ссылка.
    Затем бесплатный /unblock/ без покупки cookie.

    Returns:
        True если egress IP удалось сменить.
    """
    if spfa is None or cookies_obj is None:
        return False

    from app.integrations.avito.debug_agent_log import agent_dbg
    from app.integrations.avito.proxy_change_ip import rotate_egress

    # #region agent log
    agent_dbg(
        "B",
        "market_search.py:_handle_block",
        "block handler enter",
        {
            "detail": detail,
            "cookie_id": cookies_obj.cookie_id,
            "mobile": cookies_obj.mobile,
        },
    )
    # #endregion

    settings = get_settings_service()
    change_url = settings.get_avito_market_proxy_change_url()
    market_proxy = settings.get_avito_market_proxy()
    egress_ok, method = await rotate_egress(
        proxy=market_proxy,
        change_url=change_url,
        persist_proxy=lambda value: settings.set_secret("avito_market_proxy", value),
    )
    if egress_ok:
        # Чтобы unblock/следующие запросы шли уже с новым session.
        fresh_proxy = settings.get_avito_market_proxy() or market_proxy
        spfa.proxy = fresh_proxy
        logger.info(
            "Avito block (%s); egress rotated via %s before unblock",
            detail,
            method,
        )
    else:
        logger.warning(
            "Avito block (%s); egress rotate не удался — unblock всё равно",
            detail,
        )
    # #region agent log
    agent_dbg(
        "A",
        "market_search.py:_handle_block:ip",
        "after rotate_egress",
        {"ok": egress_ok, "method": method},
    )
    # #endregion

    refreshed = await spfa.unblock(cookies_obj.cookie_id, previous=cookies_obj)
    if refreshed is None:
        if spfa.last_unblock_was_permanent():
            spfa.invalidate_cookie_cache()
            logger.warning(
                "Avito block (%s); unblock permanent fail, cache cleared id=%s (%s)",
                detail,
                cookies_obj.cookie_id,
                spfa._last_unblock_error[:120],
            )
        else:
            logger.warning(
                "Avito block (%s); unblock transient fail, cache kept id=%s (%s)",
                detail,
                cookies_obj.cookie_id,
                (spfa._last_unblock_error or "")[:120],
            )
        # #region agent log
        agent_dbg(
            "C",
            "market_search.py:_handle_block:unblock_fail",
            "unblock failed",
            {
                "cookie_id": cookies_obj.cookie_id,
                "permanent": spfa.last_unblock_was_permanent(),
                "error": (spfa._last_unblock_error or "")[:160],
            },
        )
        # #endregion
        return egress_ok
    logger.warning(
        "Avito block (%s); cookies refreshed via unblock id=%s (без новой покупки)",
        detail,
        refreshed.cookie_id,
    )
    # #region agent log
    agent_dbg(
        "C",
        "market_search.py:_handle_block:unblock_ok",
        "unblock saved",
        {"cookie_id": refreshed.cookie_id, "keys": len(refreshed.cookies)},
    )
    # #endregion
    return egress_ok


async def _fetch_via_spfa_browser(
    search_url: str,
    *,
    spfa: SpfaClient,
    cookies_obj: SpfaCookies,
    proxy: Optional[str],
    timeout_seconds: int,
) -> list[MarketListing]:
    from urllib.parse import urlparse as _urlparse

    from app.integrations.avito.debug_agent_log import agent_dbg

    async def _once(cookies: SpfaCookies, proxy_value: Optional[str]) -> tuple[int, str, str]:
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.avito.ru/",
            "User-Agent": cookies.user_agent,
        }
        headers.update(cookies.headers)
        headers["User-Agent"] = cookies.user_agent
        return await browser_get(
            search_url,
            headers=headers,
            cookies=cookies.cookies,
            proxy=proxy_value,
            impersonate=cookies.impersonate,
            timeout_seconds=timeout_seconds,
            max_bytes=MAX_RESPONSE_BYTES,
        )

    try:
        status, text, final_url = await _once(cookies_obj, proxy)
    except BrowserFetchError as exc:
        # #region agent log
        agent_dbg(
            "A",
            "market_search.py:_fetch_via_spfa_browser:first_err",
            "first fetch transport error",
            {"error": type(exc).__name__, "detail": str(exc)[:160]},
            run_id="wl",
        )
        # #endregion
        raise AvitoMarketError(str(exc)) from exc

    # #region agent log
    agent_dbg(
        "E",
        "market_search.py:_fetch_via_spfa_browser",
        "Avito response",
        {
            "status": status,
            "host": _urlparse(final_url or search_url).hostname,
            "path_prefix": (_urlparse(final_url or search_url).path or "")[:40],
            "cookie_id": cookies_obj.cookie_id,
            "impersonate": cookies_obj.impersonate,
            "body_prefix": (text or "")[:80],
            "attempt": 1,
        },
    )
    # #endregion
    logger.info(
        "Avito market fetch status=%s host=%s cookie_id=%s attempt=1",
        status,
        _urlparse(final_url or search_url).hostname,
        cookies_obj.cookie_id,
    )

    if status in {403, 429, 439}:
        egress_ok = await _handle_block(
            spfa=spfa, cookies_obj=cookies_obj, detail=f"HTTP {status}"
        )
        if egress_ok:
            # Один повтор на новом sticky-session + обновлённых cookies.
            import asyncio

            await asyncio.sleep(8)
            refreshed = spfa._load_cache() or cookies_obj
            settings = get_settings_service()
            retry_proxy = proxy_url(settings.get_avito_market_proxy()) or proxy
            try:
                status2, text2, final_url2 = await _once(refreshed, retry_proxy)
            except BrowserFetchError as exc:
                # #region agent log
                agent_dbg(
                    "C",
                    "market_search.py:_fetch_via_spfa_browser:retry_err",
                    "retry after block failed",
                    {
                        "first_status": status,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:160],
                    },
                    run_id="wl",
                )
                # #endregion
                raise AvitoMarketBlockedError(
                    f"Avito вернул HTTP {status}",
                    soft=True,
                ) from exc
            # #region agent log
            agent_dbg(
                "E",
                "market_search.py:_fetch_via_spfa_browser:retry",
                "Avito retry after egress rotate",
                {
                    "status": status2,
                    "host": _urlparse(final_url2 or search_url).hostname,
                    "cookie_id": refreshed.cookie_id,
                    "attempt": 2,
                    "first_status": status,
                },
            )
            # #endregion
            logger.info(
                "Avito market fetch status=%s host=%s cookie_id=%s attempt=2 (after rotate)",
                status2,
                _urlparse(final_url2 or search_url).hostname,
                refreshed.cookie_id,
            )
            if status2 == 200 and "captcha" not in (final_url2 or "").lower():
                try:
                    return _parse_response_body(text2)
                except AvitoMarketBlockedError:
                    pass
            status, text, final_url = status2, text2, final_url2
            if status in {403, 429, 439}:
                raise AvitoMarketBlockedError(
                    f"Avito вернул HTTP {status}", soft=True
                )
        # Без ротации / повтор не помог.
        soft = bool(egress_ok)
        raise AvitoMarketBlockedError(f"Avito вернул HTTP {status}", soft=soft)
    if status != 200:
        raise AvitoMarketError(f"Avito вернул HTTP {status}")
    if "captcha" in final_url.lower():
        egress_ok = await _handle_block(
            spfa=spfa, cookies_obj=cookies_obj, detail="captcha redirect"
        )
        raise AvitoMarketBlockedError(
            "Avito перенаправил запрос на CAPTCHA", soft=egress_ok
        )
    try:
        return _parse_response_body(text)
    except AvitoMarketBlockedError:
        egress_ok = await _handle_block(
            spfa=spfa, cookies_obj=cookies_obj, detail="security page"
        )
        raise AvitoMarketBlockedError(
            "Avito запросил проверку безопасности", soft=egress_ok
        )

async def fetch_market_listings(
    query: IphoneMarketQuery,
    *,
    timeout_seconds: int = 20,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[MarketListing]:
    """Загрузить ровно одну страницу без агрессивных повторов."""
    settings = get_settings_service()
    spfa_key = settings.get_spfa_api_key()
    market_proxy = settings.get_avito_market_proxy()
    use_spfa = settings.is_avito_market_spfa_enabled() and bool(spfa_key)
    spfa = SpfaClient(spfa_key, proxy=market_proxy) if use_spfa else None
    prefer_mobile = bool(use_spfa and market_proxy)
    search_url = await _resolve_search_url(query, spfa, prefer_mobile=prefer_mobile)
    proxy = proxy_url(market_proxy) or None

    # SPFA-путь: TLS-impersonate через curl_cffi (aiohttp даёт 439 даже с cookies).
    if spfa is not None:
        try:
            cookies_obj = await spfa.get_cookies(prefer_mobile=prefer_mobile)
        except SpfaError as exc:
            raise AvitoMarketBlockedError(f"SPFA cookies недоступны: {exc}") from exc
        return await _fetch_via_spfa_browser(
            search_url,
            spfa=spfa,
            cookies_obj=cookies_obj,
            proxy=proxy,
            timeout_seconds=timeout_seconds,
        )

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; tg-poster-market/1.0)",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://www.avito.ru/",
    }
    owns_session = session is None
    if session is None:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            headers=headers,
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            trust_env=False,
        )
    try:
        async with session.get(
            search_url,
            allow_redirects=True,
            proxy=proxy,
            headers=headers,
        ) as response:
            if response.status in {403, 429, 439}:
                raise AvitoMarketBlockedError(f"Avito вернул HTTP {response.status}")
            if response.status != 200:
                raise AvitoMarketError(f"Avito вернул HTTP {response.status}")
            body = await response.content.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise AvitoMarketParseError("Страница Avito превышает допустимый размер")
            text = body.decode(response.charset or "utf-8", errors="replace")
            if "captcha" in str(response.url).lower():
                raise AvitoMarketBlockedError("Avito перенаправил запрос на CAPTCHA")
            return _parse_response_body(text)
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise AvitoMarketError("Не удалось получить выдачу Avito") from exc
    finally:
        if owns_session:
            await session.close()
