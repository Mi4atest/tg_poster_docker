"""Единый диспетчер выгрузки фида Авито: публикация + снятие, лимит 1 upload/час."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from app.api.models.post import Post
from app.api.models.product import Product
from app.config.settings import (
    AVITO_AUTOLOAD_ADDRESS,
    AVITO_AUTOLOAD_CONTACT_PHONE,
    AVITO_AUTOLOAD_MAX_ADS_PER_BATCH,
    AVITO_FEED_PUBLIC_BASE_URL,
    AVITO_MANUAL_FEED_UPLOAD,
)
from app.integrations.avito import autoload_actions
from app.integrations.avito.archive_batch import process_archive_batch
from app.integrations.avito.archive_queue import (
    get_last_failed_error,
    list_pending as list_archive_pending,
    mark_completed,
    mark_failed,
    mark_processing,
)
from app.integrations.avito.autoload_archive import (
    _was_managed_by_autoload,
    build_date_end_feed_xml,
    build_omit_multiple_feed_xml,
)
from app.integrations.avito.autoload_coordinator import get_coordinator
from app.integrations.avito.autoload_xml import build_feed_xml_for_posts, sanitize_ad_id
from app.integrations.avito.feed_store import save_feed
from app.integrations.avito import http_client as avito_http
from app.integrations.avito.autoload_actions import trigger_autoload_upload, wait_for_avito_ids_after_upload
from app.integrations.avito.actions import fetch_item_info
from app.integrations.avito.archive_batch import _report_errors
from app.integrations.avito.errors import AvitoAutoCreateUnavailableError
from app.scheduler.queue_manager import QueueManager
from app.services.settings_service import get_settings_service
from app.utils.time_msk import format_hm_msk
from app.workers.avito.publisher import (
    _apply_avito_success,
    _draft_from_post,
    _keep_alive_avito_post_ids,
    _photos_list,
    publish_autoload_batch,
)

logger = logging.getLogger(__name__)

_VERIFY_WAIT_SEC = 30.0


@dataclass
class AvitoFeedQueueSummary:
    publish_pending: int
    archive_pending: int
    upload_wait_sec: int
    batch_wait_sec: int
    can_upload_now: bool
    manual_mode: bool
    next_upload_local_hm: str


def is_manual_feed_upload() -> bool:
    return bool(AVITO_MANUAL_FEED_UPLOAD)


def get_queue_summary(queue_manager: Optional[QueueManager] = None) -> AvitoFeedQueueSummary:
    coord = get_coordinator()
    pub = 0
    if queue_manager:
        pub = len(queue_manager.get_pending_items("avito"))
    arch = len(list_archive_pending())
    upload_wait = coord.seconds_until_next_upload()
    batch_wait = coord.seconds_until_batch_ready()
    if is_manual_feed_upload():
        can = upload_wait <= 0 and (pub > 0 or arch > 0)
    else:
        can = upload_wait <= 0 and batch_wait <= 0 and (pub > 0 or arch > 0)
    eta = coord.next_upload_at()
    return AvitoFeedQueueSummary(
        publish_pending=pub,
        archive_pending=arch,
        upload_wait_sec=upload_wait,
        batch_wait_sec=batch_wait,
        can_upload_now=can and (pub > 0 or arch > 0),
        manual_mode=is_manual_feed_upload(),
        next_upload_local_hm=format_hm_msk(eta),
    )


def format_avito_queue_header(summary: AvitoFeedQueueSummary, *, compact: bool = False) -> str:
    """
    compact=True — одна строка для главного меню «В очереди» (ожидание ~N мин в той же строке).
    compact=False — полный блок для экрана «В очереди → Авито».
    """
    if summary.manual_mode:
        line = (
            f"🛒 <b>Авито</b>: {summary.publish_pending} ждут выгрузки · "
            f"{summary.archive_pending} на снятие"
        )
        if compact:
            if summary.upload_wait_sec > 0 and not summary.can_upload_now:
                mins = (summary.upload_wait_sec + 59) // 60
                line += f" ~{mins} мин"
            return line
        lines = [line]
        if summary.can_upload_now:
            lines.append("📤 Можно отправить файл на Авито (кнопка ниже).")
        elif summary.upload_wait_sec > 0:
            mins = (summary.upload_wait_sec + 59) // 60
            lines.append(
                f"⏳ Следующая отправка файла не раньше {summary.next_upload_local_hm} (~{mins} мин)."
            )
        else:
            lines.append(
                "⏳ Собирается файл — подождите пару минут или нажмите «Отправить файл»."
            )
        return "\n".join(lines)

    line = (
        f"🛒 Авито: {summary.publish_pending} в очереди · "
        f"{summary.archive_pending} на снятие"
    )
    if compact:
        if summary.upload_wait_sec > 0:
            mins = (summary.upload_wait_sec + 59) // 60
            line += f" ~{mins} мин"
        return line
    lines = [line]
    if summary.upload_wait_sec > 0:
        mins = (summary.upload_wait_sec + 59) // 60
        lines.append(f"⏳ Выгрузка не раньше {summary.next_upload_local_hm} (~{mins} мин).")
    return "\n".join(lines)


async def execute_feed_upload(
    db,
    queue_manager: QueueManager,
    *,
    manual: bool = False,
) -> Tuple[bool, str]:
    """
    Одна выгрузка на час: сначала снятия (если есть), затем публикации (если есть).
    Returns: (success, user_message)
    """
    try:
        if get_settings_service().is_publishing_paused("avito"):
            return (
                False,
                "Публикации на паузе. Нажмите «Возобновить» в меню «В очереди».",
            )
    except Exception:
        pass

    coord = get_coordinator()
    archive_items = list_archive_pending()
    publish_items = queue_manager.get_pending_items("avito")

    if not archive_items and not publish_items:
        return False, "Нечего отправлять: очереди публикации и снятия пусты."

    upload_wait = coord.seconds_until_next_upload()
    if upload_wait > 0:
        mins = (upload_wait + 59) // 60
        return (
            False,
            f"Лимит Авито: 1 отправка файла в час. Повторите через ~{mins} мин "
            f"(не раньше {format_hm_msk(coord.next_upload_at())}).",
        )

    if not manual and is_manual_feed_upload():
        return False, "Включён ручной режим — нажмите «Отправить файл на Авито» в меню очереди."

    if not manual and not is_manual_feed_upload():
        batch_wait = coord.seconds_until_batch_ready()
        if batch_wait > 0:
            return False, f"Файл ещё собирается (~{batch_wait} с)."

    messages: List[str] = []
    ok_any = False

    try:
        if archive_items and publish_items:
            arch_ok, pub_ok, extra = await _execute_combined_upload(
                db, queue_manager, archive_items, publish_items
            )
            if arch_ok:
                ok_any = True
                messages.append(f"снято: {arch_ok}")
            if pub_ok:
                ok_any = True
                messages.append(f"выгружено: {pub_ok}")
            if extra:
                messages.append(extra)
        elif archive_items:
            arch_results = await process_archive_batch(db, archive_items)
            ok_arch = sum(1 for v in arch_results.values() if v)
            if ok_arch:
                ok_any = True
                messages.append(f"снято с Авито: {ok_arch}")
            fail_arch = len(arch_results) - ok_arch
            if fail_arch:
                messages.append(f"не снято: {fail_arch}")
                for pid, success in arch_results.items():
                    if not success:
                        detail = get_last_failed_error(int(pid))
                        if detail:
                            messages.append(detail[:220])
                        break
        elif publish_items:
            queue_ids = [item.id for item in publish_items]
            post_ids = [item.post_id for item in publish_items]
            for item in publish_items:
                queue_manager.mark_as_publishing(item.id)
            pub_results = await publish_autoload_batch(post_ids)
            ok_pub = sum(1 for item in publish_items if pub_results.get(item.post_id))
            for item in publish_items:
                if pub_results.get(item.post_id):
                    queue_manager.mark_as_completed(item.id)
                else:
                    queue_manager.mark_as_failed(
                        item.id,
                        "Автозагрузка: объявление не принято (см. отчёт Авито)",
                    )
            if ok_pub:
                ok_any = True
                messages.append(f"выгружено на Авито: {ok_pub}")
    except AvitoAutoCreateUnavailableError as e:
        return False, str(e)[:400]
    except Exception as e:
        logger.exception("Avito feed upload failed")
        return False, str(e)[:400]

    if ok_any:
        return True, "Файл отправлен на Авито. " + "; ".join(messages)
    return False, "Файл отправлен, но без успешных операций. " + "; ".join(messages)


async def _execute_combined_upload(db, queue_manager, archive_items, publish_items) -> Tuple[int, int, str]:
    """Один XML, один upload: снятия + новые объявления."""
    mark_processing([int(x.get("product_id") or 0) for x in archive_items])

    archive_exclude: Set[str] = set()
    date_end_jobs: List[Tuple[Product, Post, int]] = []
    archive_jobs: List[Tuple[Product, Post, int]] = []

    for row in archive_items:
        pid = int(row.get("product_id") or 0)
        product = db.query(Product).filter(Product.id == pid).first()
        if not product or not product.post_id:
            mark_failed(pid, "нет поста")
            continue
        post = db.query(Post).filter(Post.id == product.post_id).first()
        if not post:
            mark_failed(pid, "пост не найден")
            continue
        iid = int(row.get("avito_item_id") or product.avito_item_id or 0)
        archive_exclude.add(str(post.id))
        archive_jobs.append((product, post, iid))
        if not _was_managed_by_autoload(post):
            date_end_jobs.append((product, post, iid))

    publish_ids = list(dict.fromkeys(item.post_id for item in publish_items))
    reserve = list(archive_exclude) + publish_ids
    keep = _keep_alive_avito_post_ids(db, reserve)
    unique_publish = list(dict.fromkeys(publish_ids + keep))[: max(1, AVITO_AUTOLOAD_MAX_ADS_PER_BATCH)]

    posts_payload = []
    for pid in unique_publish:
        if pid in archive_exclude:
            continue
        post = db.query(Post).filter(Post.id == pid).first()
        if not post:
            continue
        posts_payload.append(
            {
                "post_id": str(post.id),
                "post_text": post.text or "",
                "post_name": post.name,
                "photos": _photos_list(post),
                "avito_draft": _draft_from_post(post) or {"screen_level": 1, "body_level": 1},
            }
        )

    if archive_exclude and keep:
        xml = build_omit_multiple_feed_xml(
            exclude_post_ids=archive_exclude,
            keep_alive_post_ids=keep,
            db=db,
        )
        if date_end_jobs:
            closing = "</Ads>\n"
            body = xml[: -len(closing)] if xml.endswith(closing) else xml
            xml = body
            for _product, post, item_id in date_end_jobs:
                block = build_date_end_feed_xml(
                    post=post, item_id=item_id, keep_alive_post_ids=[], db=db
                )
                a, b = block.find("<Ad>"), block.rfind("</Ad>")
                if a >= 0 and b > a:
                    xml += block[a : b + 5] + "\n"
            xml += closing
    elif posts_payload:
        xml = build_feed_xml_for_posts(
            posts_payload,
            public_base_url=AVITO_FEED_PUBLIC_BASE_URL,
            contact_phone=AVITO_AUTOLOAD_CONTACT_PHONE,
            address=AVITO_AUTOLOAD_ADDRESS,
        )
        if date_end_jobs:
            closing = "</Ads>\n"
            body = xml[: -len(closing)] if xml.endswith(closing) else xml
            xml = body
            for _product, post, item_id in date_end_jobs:
                block = build_date_end_feed_xml(
                    post=post, item_id=item_id, keep_alive_post_ids=[], db=db
                )
                a, b = block.find("<Ad>"), block.rfind("</Ad>")
                if a >= 0 and b > a:
                    xml += block[a : b + 5] + "\n"
            xml += closing
    else:
        return 0, 0, "не удалось собрать файл"

    meta_ids = [p["post_id"] for p in posts_payload] or [str(keep[0]) if keep else "batch"]
    save_feed(
        xml,
        meta_ids[0],
        sanitize_ad_id(meta_ids[0]),
        post_ids=meta_ids,
        ad_ids=[sanitize_ad_id(x) for x in meta_ids],
    )

    await trigger_autoload_upload()
    get_coordinator().record_upload_success()
    await asyncio.sleep(_VERIFY_WAIT_SEC)

    arch_ok = 0
    for product, post, item_id in archive_jobs:
        info = await fetch_item_info(item_id)
        if str(info.get("status") or "") in ("old", "removed"):
            mark_completed(product.id, product_name=product.name)
            arch_ok += 1
        else:
            mark_failed(product.id, f"статус «{info.get('status')}»")

    pub_ok = 0
    ad_ids = [sanitize_ad_id(pid) for pid in publish_ids]
    reports = await wait_for_avito_ids_after_upload(ad_ids) if ad_ids else {}
    for item in publish_items:
        queue_manager.mark_as_publishing(item.id)
        pid = item.post_id
        post = db.query(Post).filter(Post.id == pid).first()
        product = db.query(Product).filter(Product.post_id == pid).first()
        had = post and post.avito_item_id
        if had:
            queue_manager.mark_as_completed(item.id)
            pub_ok += 1
            continue
        avito_id, _st, msgs = reports.get(sanitize_ad_id(pid), (None, None, []))
        if avito_id and post:
            await _apply_avito_success(post, product, avito_id, db)
            queue_manager.mark_as_completed(item.id)
            pub_ok += 1
        else:
            queue_manager.mark_as_failed(
                item.id, "; ".join(msgs[:2]) if msgs else "нет avito_id"
            )

    db.commit()
    return arch_ok, pub_ok, ""


async def try_auto_upload(db, queue_manager: QueueManager) -> None:
    """Фоновая попытка (авто-режим): одна выгрузка при готовности."""
    if is_manual_feed_upload():
        return
    summary = get_queue_summary(queue_manager)
    if not summary.publish_pending and not summary.archive_pending:
        return
    if summary.upload_wait_sec > 0 or summary.batch_wait_sec > 0:
        return
    try:
        await execute_feed_upload(db, queue_manager, manual=False)
    except Exception as e:
        logger.warning("Avito auto feed upload failed: %s", e)
