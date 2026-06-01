"""Пакетное снятие объявлений с Авито (один upload на батч из очереди)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Set, Tuple

from app.api.models.post import Post
from app.api.models.product import Product
from app.integrations.avito import autoload_actions
from app.integrations.avito import http_client as avito_http
from app.integrations.avito.actions import fetch_item_info
from app.integrations.avito.archive_queue import mark_completed, mark_failed, mark_processing
from app.integrations.avito.autoload_archive import (
    _report_errors,
    _was_managed_by_autoload,
    build_date_end_feed_xml,
    build_omit_multiple_feed_xml,
    is_manual_avito_link_only,
)
from app.integrations.avito.autoload_coordinator import get_coordinator
from app.integrations.avito.autoload_xml import sanitize_ad_id
from app.integrations.avito.feed_store import save_feed
from app.workers.avito.publisher import _keep_alive_avito_post_ids

logger = logging.getLogger(__name__)

_VERIFY_WAIT_SEC = 30.0


async def process_archive_batch(db, queue_items: List[dict]) -> Dict[int, bool]:
    """
    Один XML + один upload для всех pending записей очереди.
    Returns: {product_id: success}
    """
    results: Dict[int, bool] = {}
    if not queue_items:
        return results

    jobs: List[Tuple[dict, Product, Post, int]] = []
    for row in queue_items:
        pid = int(row.get("product_id") or 0)
        iid = int(row.get("avito_item_id") or 0)
        if not pid or not iid:
            mark_failed(pid, "некорректная запись очереди")
            results[pid] = False
            continue
        product = db.query(Product).filter(Product.id == pid).first()
        if not product:
            mark_failed(pid, "товар не найден")
            results[pid] = False
            continue
        post = None
        if product.post_id:
            post = db.query(Post).filter(Post.id == product.post_id).first()
        if not post:
            mark_failed(pid, "нет поста для автозагрузки — привяжите пост или снимите в ЛК Авито")
            results[pid] = False
            continue
        jobs.append((row, product, post, iid))

    if not jobs:
        return results

    mark_processing([j[0]["product_id"] for j in jobs])

    date_end_jobs: List[Tuple[Product, Post, int]] = []
    all_exclude: Set[str] = set()

    for _row, product, post, item_id in jobs:
        all_exclude.add(str(post.id))
        managed = _was_managed_by_autoload(post)
        logger.info(
            "Avito archive batch: product_id=%s post_id=%s item_id=%s strategy=%s",
            product.id,
            post.id,
            item_id,
            "omit_from_feed" if managed else "date_end_avito_id",
        )
        if not managed:
            date_end_jobs.append((product, post, item_id))

    keep = _keep_alive_avito_post_ids(db, list(all_exclude))

    try:
        if date_end_jobs and not keep:
            _product, post, item_id = date_end_jobs[0]
            xml = build_date_end_feed_xml(
                post=post,
                item_id=item_id,
                keep_alive_post_ids=[],
                db=db,
            )
            if len(date_end_jobs) > 1:
                logger.warning(
                    "Avito archive batch: %s manual items without keep-alive, first only this upload",
                    len(date_end_jobs),
                )
        else:
            base_xml = build_omit_multiple_feed_xml(
                exclude_post_ids=all_exclude,
                keep_alive_post_ids=keep,
                db=db,
            )
            if not date_end_jobs:
                xml = base_xml
            else:
                closing = "</Ads>\n"
                header_and_body = base_xml[: -len(closing)] if base_xml.endswith(closing) else base_xml
                xml = header_and_body
                for _product, post, item_id in date_end_jobs:
                    block = build_date_end_feed_xml(
                        post=post,
                        item_id=item_id,
                        keep_alive_post_ids=[],
                        db=db,
                    )
                    ad_start = block.find("<Ad>")
                    ad_end = block.rfind("</Ad>")
                    if ad_start >= 0 and ad_end > ad_start:
                        xml += block[ad_start : ad_end + 5] + "\n"
                xml += closing
    except Exception as e:
        err = str(e)[:500]
        logger.error("Avito archive batch: build feed failed: %s", e)
        for row, product, _post, _iid in jobs:
            mark_failed(int(product.id), err)
            results[int(product.id)] = False
        return results

    meta_post_ids = [p for p in keep if p not in all_exclude]
    save_feed(
        xml,
        meta_post_ids[0] if meta_post_ids else str(jobs[0][2].id),
        sanitize_ad_id(meta_post_ids[0] if meta_post_ids else str(jobs[0][2].id)),
        post_ids=meta_post_ids,
        ad_ids=[sanitize_ad_id(p) for p in meta_post_ids],
    )

    try:
        await autoload_actions.trigger_autoload_upload()
        get_coordinator().record_upload_success()
    except avito_http.AvitoApiError as e:
        if e.status == 429:
            err = "Автозагрузка: лимит 1 выгрузка в час. Повторите позже."
        else:
            err = str(e)[:500]
        for row, product, _post, _iid in jobs:
            mark_failed(int(product.id), err)
            results[int(product.id)] = False
        return results

    await asyncio.sleep(_VERIFY_WAIT_SEC)

    for _row, product, post, item_id in jobs:
        pid = int(product.id)
        try:
            info = await fetch_item_info(item_id)
            status = str(info.get("status") or "")
            if status in ("old", "removed"):
                mark_completed(pid, product_name=product.name)
                results[pid] = True
                continue
            ad_id = sanitize_ad_id(str(post.id))
            report = await autoload_actions.fetch_autoload_item_report(ad_id)
            errs = _report_errors(report, ad_id)
            extra = f" Отчёт: {'; '.join(errs[:2])}" if errs else ""
            hint = ""
            if is_manual_avito_link_only(post):
                hint = (
                    " Объявление привязано вручную (не через автозагрузку) — "
                    "снимите в ЛК Авито или сначала опубликуйте через бота, затем снова «недоступен»."
                )
            mark_failed(
                pid,
                f"После выгрузки объявление всё ещё «{status}».{extra}{hint}"[:500],
            )
            results[pid] = False
        except Exception as ex:
            mark_failed(pid, str(ex)[:500])
            results[pid] = False

    logger.info(
        "Avito archive batch done: ok=%s fail=%s",
        sum(1 for v in results.values() if v),
        sum(1 for v in results.values() if not v),
    )
    return results
