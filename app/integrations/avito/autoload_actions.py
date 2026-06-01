"""Автозагрузка: upload и отчёт по объявлению."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from app.integrations.avito import http_client as avito_http
from app.integrations.avito.actions import get_access_token
from app.integrations.avito.autoload_xml import sanitize_ad_id

logger = logging.getLogger(__name__)


async def trigger_autoload_upload() -> Dict[str, Any]:
    token = await get_access_token()
    return await avito_http.post_autoload_upload(token)


async def fetch_autoload_item_report(ad_id: str) -> Dict[str, Any]:
    token = await get_access_token()
    q = sanitize_ad_id(ad_id)
    return await avito_http.get_autoload_report_items(token, q)


def parse_item_report_row(row: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], Optional[str], list]:
    """(file_ad_id, avito_id, status, messages) для одной строки отчёта."""
    file_ad_id = row.get("ad_id") or row.get("Id") or row.get("id")
    if file_ad_id is not None:
        file_ad_id = str(file_ad_id)
    avito_id = row.get("avito_id")
    try:
        avito_id_int = int(avito_id) if avito_id is not None else None
    except (TypeError, ValueError):
        avito_id_int = None
    status = row.get("avito_status")
    msgs = []
    for m in row.get("messages") or []:
        if isinstance(m, dict):
            t = m.get("title") or m.get("description") or m.get("message")
            if t:
                msgs.append(str(t))
        elif m:
            msgs.append(str(m))
    return file_ad_id, avito_id_int, status, msgs


def parse_items_report(data: Dict[str, Any]) -> Dict[str, Tuple[Optional[int], Optional[str], list]]:
    """Словарь file_ad_id → (avito_id, status, messages)."""
    out: Dict[str, Tuple[Optional[int], Optional[str], list]] = {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return out
    for row in items:
        if not isinstance(row, dict):
            continue
        file_ad_id, avito_id_int, status, msgs = parse_item_report_row(row)
        key = file_ad_id or (str(avito_id_int) if avito_id_int else None)
        if key:
            out[key] = (avito_id_int, status, msgs)
    return out


def parse_item_report(data: Dict[str, Any]) -> Tuple[Optional[int], Optional[str], list]:
    """
    Возвращает (avito_id, avito_status, messages).
  """
    by_id = parse_items_report(data if isinstance(data, dict) else {})
    if not by_id:
        return None, None, []
    _, avito_id_int, status, msgs = next(iter(by_id.values()))
    return avito_id_int, status, msgs


async def wait_for_avito_ids_after_upload(
    ad_ids: list,
    *,
    attempts: int = 24,
    delay_sec: float = 15.0,
) -> Dict[str, Tuple[Optional[int], Optional[str], list]]:
    """Ожидание avito_id для нескольких Id из файла (один query через |)."""
    from app.integrations.avito.autoload_xml import sanitize_ad_id

    keys = [sanitize_ad_id(a) for a in ad_ids if a]
    pending = set(keys)
    result: Dict[str, Tuple[Optional[int], Optional[str], list]] = {k: (None, None, []) for k in keys}
    if not keys:
        return result

    query = "|".join(keys)
    last: Dict[str, Any] = {}
    for i in range(max(1, attempts)):
        if i > 0:
            await asyncio.sleep(delay_sec)
        if not pending:
            break
        try:
            last = await fetch_autoload_item_report(query)
            by_id = parse_items_report(last)
            for ad_key in list(pending):
                if ad_key not in by_id:
                    continue
                avito_id, status, msgs = by_id[ad_key]
                result[ad_key] = (avito_id, status, msgs)
                if avito_id:
                    pending.discard(ad_key)
                elif status in ("rejected", "blocked", "removed"):
                    pending.discard(ad_key)
        except avito_http.AvitoApiError as e:
            logger.warning("Avito report poll batch: %s", e)
            if e.status == 404:
                continue
    return result


async def wait_for_avito_id_after_upload(
    ad_id: str,
    *,
    attempts: int = 18,
    delay_sec: float = 10.0,
) -> Tuple[Optional[int], Optional[str], Dict[str, Any]]:
    """Ожидание завершения выгрузки и появления avito_id в отчёте (одно объявление)."""
    sid = sanitize_ad_id(ad_id)
    batch = await wait_for_avito_ids_after_upload([sid], attempts=attempts, delay_sec=delay_sec)
    avito_id, status, msgs = batch.get(sid, (None, None, []))
    if avito_id:
        return avito_id, status, {"items": [{"ad_id": sid, "avito_id": avito_id, "avito_status": status}]}
    if status in ("rejected", "blocked", "removed"):
        logger.warning("Avito autoload ad_id=%s status=%s msgs=%s", ad_id, status, msgs[:3])
    return None, status, {"items": [{"ad_id": sid, "avito_status": status, "messages": msgs}]}
