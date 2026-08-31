"""Высокоуровневые операции Авито: токен, user_id, цена, архив."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.avito import http_client as avito_http
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


class AvitoArchiveNotAvailableError(Exception):
    """Снятие с публикации недоступно ни через остатки (stock-management), ни через archive POST."""

    pass


def _stock_edit_result_ok(data: Any, item_id: int) -> tuple[bool, str, bool]:
    """По ответу PUT /stock-management/1/stocks — (успех, причина, есть_ли_отказ_Авито_с_текстом_ошибок)."""
    if not isinstance(data, dict):
        return False, "некорректный ответ", False
    # Реальный API может отличаться от swagger: camelCase, обёртка result/data.
    raw = data
    if "stocks" not in raw and isinstance(raw.get("result"), dict):
        raw = raw["result"]
    if "stocks" not in raw and isinstance(raw.get("data"), dict):
        raw = raw["data"]

    rows = raw.get("stocks")
    if not isinstance(rows, list):
        return False, "нет поля stocks", False

    want = int(item_id)
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("item_id")
        if rid is None:
            rid = row.get("itemId")
        try:
            if int(rid or 0) != want:
                continue
        except (TypeError, ValueError):
            continue
        errs = row.get("errors") or row.get("Errors") or []
        ok = row.get("success")
        if ok is None:
            ok = row.get("Success")
        if ok is True or ok == 1 or str(ok).lower() == "true":
            return True, "", False
        if isinstance(errs, list) and errs:
            return False, "; ".join(str(x) for x in errs), True
        return False, f"success={ok!r}", False
    return False, "item_id нет в ответе", False


_token_cache: str = ""
_token_expires_at: float = 0.0
_ITEMS_CACHE_TTL_SEC = 150.0
_items_cache: List[Dict[str, Any]] = []
_items_cache_at: float = 0.0


def _credentials() -> Tuple[str, str]:
    svc = get_settings_service()
    data = svc.get_all()
    cid = str(data.get("integrations", {}).get("avito_client_id") or "").strip()
    sec = svc.get_secret("avito_client_secret").strip()
    return cid, sec


async def get_access_token(force_refresh: bool = False) -> str:
    global _token_cache, _token_expires_at
    now = time.time()
    if not force_refresh and _token_cache and now < _token_expires_at - 30:
        return _token_cache
    cid, sec = _credentials()
    if not cid or not sec:
        raise avito_http.AvitoApiError("Avito: не заданы client_id или client_secret в настройках")
    resp = await avito_http.fetch_client_credentials_token(cid, sec)
    token = resp.get("access_token")
    if not token:
        raise avito_http.AvitoApiError("Avito: в ответе /token нет access_token")
    expires_in = int(resp.get("expires_in") or 3600)
    _token_cache = token
    _token_expires_at = now + max(60, expires_in)
    return token


async def get_avito_user_id() -> int:
    """Идентификатор пользователя (аккаунта) для путей /accounts/{id}/..."""
    svc = get_settings_service()
    data = svc.get_all()
    saved = data.get("integrations", {}).get("avito_user_id")
    if saved not in (None, "", 0, "0"):
        try:
            return int(saved)
        except (TypeError, ValueError):
            pass
    token = await get_access_token()
    acc = await avito_http.fetch_account_self(token)
    uid = acc.get("id")
    if uid is None:
        raise avito_http.AvitoApiError("Avito: в ответе accounts/self нет id")
    uid_int = int(uid)
    svc.update({"integrations": {"avito_user_id": uid_int}})
    return uid_int


async def update_item_price_rub(item_id: int, price_rub: int) -> Dict[str, Any]:
    token = await get_access_token()
    return await avito_http.post_item_price_update(token, item_id, price_rub)


def _resources_from_items_payload(data: Any) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    resources = data.get("resources")
    if isinstance(resources, list):
        return [row for row in resources if isinstance(row, dict)]
    return []


async def fetch_active_listings(*, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Активные объявления кабинета. Кэш ~2.5 мин, чтобы очередь привязки не упиралась в 25 req/min."""
    global _items_cache, _items_cache_at
    now = time.time()
    if not force_refresh and _items_cache and now < _items_cache_at + _ITEMS_CACHE_TTL_SEC:
        return list(_items_cache)
    token = await get_access_token()
    collected: List[Dict[str, Any]] = []
    page = 1
    while page <= 20:
        data = await avito_http.fetch_items(token, status="active", page=page, per_page=99)
        chunk = _resources_from_items_payload(data)
        collected.extend(chunk)
        if len(chunk) < 99:
            break
        page += 1
    _items_cache = collected
    _items_cache_at = now
    logger.info("Avito listings fetched count=%s pages=%s", len(collected), page)
    return list(collected)


def invalidate_items_cache() -> None:
    global _items_cache, _items_cache_at
    _items_cache = []
    _items_cache_at = 0.0


async def archive_item(item_id: int, *, post=None, db=None) -> Dict[str, Any]:
    """
    Снятие с публикации / «нет в наличии»:

    1. ``PUT /stock-management/1/stocks`` с ``quantity: 0`` — только для товаров «Новый».
    2. ``POST .../items/{id}/archive`` — если выдан доступ в кабинете приложения.
    3. Автозагрузка (DateEnd + AvitoId) — fallback для б/у с привязанным постом (лимит 1 upload/час).

    Если объявление уже ``old``/``removed`` — только ответ getItemInfo.
    """
    token = await get_access_token()
    user_id = await get_avito_user_id()
    info = await avito_http.fetch_item(token, user_id, item_id)
    if not isinstance(info, dict):
        info = {}
    status = info.get("status")
    if status in ("old", "removed"):
        return info

    stock_note = ""
    stock_errors = False
    try:
        stock_out = await avito_http.put_stock_management_stocks_zero(token, item_id)
        ok, why, stock_errors = _stock_edit_result_ok(stock_out, item_id)
        if ok:
            return stock_out if isinstance(stock_out, dict) else {}
        stock_note = f"Управление остатками: {why}"
    except avito_http.AvitoApiError as se:
        stock_note = f"Управление остатками: HTTP {se.status}"

    archive_http_status: Optional[int] = None
    try:
        out = await avito_http.post_item_archive(token, user_id, item_id)
        return out if isinstance(out, dict) else {}
    except avito_http.AvitoApiError as e:
        archive_http_status = e.status
        if e.status == 403:
            raise avito_http.AvitoApiError(
                "Avito: нет права на архивирование (HTTP 403). В кабинете приложения — «Запросить доступ» к API "
                "«Управление остатками» и/или объявлениям.",
                status=403,
                body=e.body,
            ) from e
        if e.status != 404:
            raise avito_http.AvitoApiError(
                f"Avito archive: {e}. {stock_note}"[:500],
                status=e.status,
                body=e.body,
            ) from e

    if post is not None and db is not None:
        try:
            from app.integrations.avito.autoload_archive import archive_item_via_autoload

            out = await archive_item_via_autoload(item_id, post, db=db)
            return out
        except AvitoArchiveNotAvailableError as ae:
            raise AvitoArchiveNotAvailableError(
                f"{stock_note or 'API archive недоступен'}. {ae}"
            ) from ae
        except avito_http.AvitoApiError as ae:
            raise AvitoArchiveNotAvailableError(
                f"{stock_note or 'API archive недоступен'}. Автозагрузка: {ae}"[:500]
            ) from ae

    detail = (
        f"{stock_note}. POST .../items/{{id}}/archive — HTTP {archive_http_status or 404}. "
        "Для б/у объявлений подключите в кабинете разработчика API объявлений или используйте автозагрузку "
        "(привязка к посту в боте)."
    )
    if stock_errors:
        detail += " API остатков работает только для товаров «Новый»."
    raise AvitoArchiveNotAvailableError(detail)


async def fetch_item_info(item_id: int) -> Dict[str, Any]:
    token = await get_access_token()
    user_id = await get_avito_user_id()
    return await avito_http.fetch_item(token, user_id, item_id)


async def try_create_item_via_zero_slot(body: Dict[str, Any]) -> Tuple[Optional[int], Optional[str], Dict[str, Any]]:
    """
    Попытка создать объявление: POST .../items/0/ с телом (часть контрактов Авито для черновика).
    Возвращает (item_id, url, raw_response).
    """
    from app.integrations.avito.create_item import extract_item_id_and_url

    token = await get_access_token()
    user_id = await get_avito_user_id()
    data = await avito_http.post_item_update(token, user_id, 0, body)
    iid, url = extract_item_id_and_url(data)
    return iid, url, data


def invalidate_token_cache() -> None:
    global _token_cache, _token_expires_at
    _token_cache = ""
    _token_expires_at = 0.0
    invalidate_items_cache()
