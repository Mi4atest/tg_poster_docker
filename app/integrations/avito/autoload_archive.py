"""Снятие объявления с публикации через автозагрузку (fallback для б/у без API archive)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.api.models.post import Post
from app.integrations.avito import autoload_actions
from app.integrations.avito import http_client as avito_http
from app.integrations.avito.actions import AvitoArchiveNotAvailableError, fetch_item_info
from app.integrations.avito.autoload_xml import (
    build_ad_xml_body,
    build_feed_xml_for_posts,
    sanitize_ad_id,
)
from app.integrations.avito.feed_store import load_feed_meta, save_feed
from app.integrations.avito.publish_autoload import _address, _contact_phone, _public_base_url
from app.workers.avito.publisher import _keep_alive_avito_post_ids, _photos_list

logger = logging.getLogger(__name__)

_DEBUG_LOG_PATH = "/app/.cursor/debug-f37f41.log"


def _agent_debug_log(location: str, message: str, data: dict, hypothesis_id: str) -> None:
    # #region agent log
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "f37f41",
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                        "hypothesisId": hypothesis_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # #endregion


def _post_payload(post) -> Dict[str, Any]:
    return {
        "post_id": str(post.id),
        "post_text": post.text or "",
        "post_name": post.name,
        "photos": _photos_list(post),
        "avito_draft": post.avito_draft,
    }


def _was_managed_by_autoload(post) -> bool:
    """
    Объявление реально выгружалось автозагрузкой — снимаем omit (убрать из фида).
    Ручная привязка Avito ID в карточке товара не считается (там только avito_item_id на посте).
    """
    if bool(getattr(post, "is_published_avito", False)):
        return True
    if getattr(post, "published_avito_at", None):
        return True
    meta = load_feed_meta()
    post_ids = meta.get("post_ids") or []
    return str(post.id) in [str(x) for x in post_ids]


def is_manual_avito_link_only(post) -> bool:
    """ID привязан вручную, через автозагрузку объявление не публиковалось."""
    if _was_managed_by_autoload(post):
        return False
    aid = getattr(post, "avito_item_id", None)
    return aid is not None and str(aid).strip() not in ("", "0", "None")


def build_omit_multiple_feed_xml(
    *,
    exclude_post_ids: set,
    keep_alive_post_ids: List[str],
    db,
) -> str:
    """
    Фид без объявлений из exclude_post_ids (официальный способ снятия через автозагрузку).
    """
    exclude = {str(x) for x in exclude_post_ids}
    posts_payload: List[Dict[str, Any]] = []
    for pid in keep_alive_post_ids:
        if str(pid) in exclude:
            continue
        row = db.query(Post).filter(Post.id == pid).first()
        if not row:
            continue
        posts_payload.append(_post_payload(row))

    if not posts_payload:
        raise AvitoArchiveNotAvailableError(
            "Автозагрузка: нельзя снять объявление без других активных в фиде. "
            "Снимите вручную в ЛК Авито."
        )

    return build_feed_xml_for_posts(
        posts_payload,
        public_base_url=_public_base_url(),
        contact_phone=_contact_phone(),
        address=_address(),
    )


def build_omit_from_feed_xml(
    *,
    post,
    keep_alive_post_ids: List[str],
    db,
) -> str:
    """Одно объявление: исключить post.id из фида."""
    return build_omit_multiple_feed_xml(
        exclude_post_ids={str(post.id)},
        keep_alive_post_ids=keep_alive_post_ids,
        db=db,
    )


def build_date_end_feed_xml(
    *,
    post,
    item_id: int,
    keep_alive_post_ids: List[str],
    db,
) -> str:
    """
    Для объявлений, привязанных вручную (не было в фиде): AvitoId + DateEnd в прошлом.
  """
    post_id = str(post.id)
    ad_id = sanitize_ad_id(post_id)
    date_end = (datetime.now() - timedelta(days=1)).strftime("%d.%m.%Y")

    archive_ad = build_ad_xml_body(
        post_id=post_id,
        post_text=post.text or "",
        post_name=post.name,
        photo_file_ids=_photos_list(post),
        avito_draft=post.avito_draft,
        public_base_url=_public_base_url(),
        contact_phone=_contact_phone(),
        address=_address(),
    )
    id_line = f"<Id>{ad_id}</Id>\n"
    inject = (
        f"{id_line}"
        f"<AvitoId>{int(item_id)}</AvitoId>\n"
        f"<DateEnd>{date_end}</DateEnd>\n"
    )
    if id_line not in archive_ad:
        raise ValueError(f"Id {ad_id!r} not found in archive ad XML")
    archive_ad = archive_ad.replace(id_line, inject, 1)

    posts_payload: List[Dict[str, Any]] = []
    for pid in keep_alive_post_ids:
        if pid == post_id:
            continue
        row = db.query(Post).filter(Post.id == pid).first()
        if not row:
            continue
        posts_payload.append(_post_payload(row))

    if posts_payload:
        base = build_feed_xml_for_posts(
            posts_payload,
            public_base_url=_public_base_url(),
            contact_phone=_contact_phone(),
            address=_address(),
        )
        closing = "</Ads>\n"
        if not base.endswith(closing):
            raise ValueError("unexpected feed structure")
        return base[: -len(closing)] + archive_ad + closing

    header = '<?xml version="1.0" encoding="UTF-8"?>\n<Ads formatVersion="3" target="Avito.ru">\n'
    return header + archive_ad + "</Ads>\n"


def _report_errors(report: Dict[str, Any], ad_id: str) -> List[str]:
    msgs: List[str] = []
    for row in report.get("items") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("ad_id") or "") != ad_id:
            continue
        for m in row.get("messages") or []:
            if isinstance(m, dict):
                t = m.get("title") or m.get("description") or ""
                if t:
                    msgs.append(str(t))
    return msgs


async def archive_item_via_autoload(
    item_id: int,
    post,
    *,
    db,
    wait_sec: float = 30.0,
) -> Dict[str, Any]:
    """Снятие через upload XML-фида (см. документацию API «Автозагрузка»)."""
    keep = _keep_alive_avito_post_ids(db, [str(post.id)])
    use_omit = _was_managed_by_autoload(post)
    strategy = "omit_from_feed" if use_omit else "date_end_avito_id"

    if use_omit:
        xml = build_omit_from_feed_xml(post=post, keep_alive_post_ids=keep, db=db)
    else:
        xml = build_date_end_feed_xml(
            post=post,
            item_id=item_id,
            keep_alive_post_ids=keep,
            db=db,
        )

    ad_id = sanitize_ad_id(str(post.id))
    # #region agent log
    _agent_debug_log(
        "autoload_archive.py:archive_item_via_autoload",
        "feed built",
        {
            "item_id": item_id,
            "strategy": strategy,
            "ads_in_feed": xml.count("<Ad>"),
            "keep_alive": len(keep),
        },
        "H3",
    )
    # #endregion

    save_feed(
        xml,
        str(post.id),
        ad_id,
        post_ids=[p for p in keep if p != str(post.id)],
        ad_ids=[sanitize_ad_id(p) for p in keep if p != str(post.id)],
    )
    logger.info(
        "Avito autoload archive: strategy=%s item_id=%s post_id=%s keep_alive=%s",
        strategy,
        item_id,
        post.id,
        len(keep),
    )
    try:
        await autoload_actions.trigger_autoload_upload()
    except avito_http.AvitoApiError as e:
        if e.status == 429:
            raise AvitoArchiveNotAvailableError(
                "Автозагрузка: лимит 1 выгрузка в час. Повторите позже или снимите объявление вручную в ЛК Авито."
            ) from e
        raise

    if wait_sec > 0:
        await asyncio.sleep(wait_sec)

    report = await autoload_actions.fetch_autoload_item_report(ad_id)
    report_errs = _report_errors(report, ad_id)
    # #region agent log
    _agent_debug_log(
        "autoload_archive.py:archive_item_via_autoload",
        "autoload report",
        {"item_id": item_id, "strategy": strategy, "report_errors": report_errs[:5]},
        "H3",
    )
    # #endregion

    info = await fetch_item_info(item_id)
    status = str(info.get("status") or "")
    if status in ("old", "removed"):
        return info

    extra = ""
    if report_errs:
        extra = f" Отчёт автозагрузки: {'; '.join(report_errs[:3])}."
    raise AvitoArchiveNotAvailableError(
        f"Автозагрузка ({strategy}) выполнена, но объявление {item_id} всё ещё «{status}».{extra} "
        "Для объявлений с ручной привязкой id может потребоваться снятие в ЛК Авито "
        "(«Снять с публикации» → «Продал где-то ещё»). "
        "Документация: https://developers.avito.ru/api-catalog/autoload/documentation"
    )
