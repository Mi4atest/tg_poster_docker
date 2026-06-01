"""Публикация поста на Авито: привязка (фаза A) или автозагрузка XML (фаза B, батч)."""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.config.settings import (
    AVITO_AUTOLOAD_ADDRESS,
    AVITO_AUTOLOAD_CONTACT_PHONE,
    AVITO_AUTOLOAD_MAX_ADS_PER_BATCH,
    AVITO_FEED_PUBLIC_BASE_URL,
)
from app.db.database import SessionLocal
from app.api.models.post import Post
from app.api.models.product import Product
from app.integrations.avito import actions as avito_actions
from app.integrations.avito.autoload_actions import (
    parse_items_report,
    trigger_autoload_upload,
    wait_for_avito_ids_after_upload,
)
from app.integrations.avito.autoload_coordinator import get_coordinator
from app.integrations.avito.autoload_xml import (
    build_feed_xml_for_posts,
    sanitize_ad_id,
)
from app.integrations.avito.feed_store import save_feed
from app.integrations.avito import http_client as avito_http
from app.integrations.avito.errors import AvitoAutoCreateUnavailableError
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


def _avito_error_user_message(exc: avito_http.AvitoApiError) -> str:
    body = getattr(exc, "body", "") or ""
    if exc.status == 429:
        coord = get_coordinator()
        mins = (coord.seconds_until_next_upload() + 59) // 60
        return (
            "Авито: лимит автозагрузки — не чаще одного запуска в час. "
            f"Повторите через ~{mins} мин. Объявления остаются в очереди и уйдут следующей выгрузкой."
        )
    if exc.status == 403:
        return (
            "Авито отклонил запуск автозагрузки (HTTP 403). Проверьте профиль автозагрузки и URL фида "
            f"{AVITO_FEED_PUBLIC_BASE_URL}/feeds/avito.xml в кабинете."
        )
    return f"Ошибка API Авито (HTTP {exc.status}): {body[:400]}"


def _draft_from_post(post: Post) -> Optional[dict]:
    d = post.avito_draft
    return d if isinstance(d, dict) else None


def _photos_list(post: Post) -> List[str]:
    raw = post.photos
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    return []


async def _apply_avito_success(
    post: Post,
    product: Optional[Product],
    avito_id: int,
    db,
) -> None:
    sid = str(avito_id)
    url = f"https://www.avito.ru/{avito_id}"
    try:
        info = await avito_actions.fetch_item_info(avito_id)
        if isinstance(info, dict) and info.get("url"):
            url = str(info["url"])
    except Exception:
        pass
    post.avito_item_id = sid
    post.is_published_avito = True
    post.published_avito_at = datetime.now(timezone.utc)
    post.avito_url = url
    if product:
        product.avito_item_id = sid
        product.avito_url = url


def _keep_alive_avito_post_ids(db, new_post_ids: List[str]) -> List[str]:
    """
    Уже опубликованные объявления, которые нужно оставить в фиде,
    чтобы автозагрузка не убрала их в «Архив» (нет в файле = снятие).
    Исключаем товары со статусом unavailable.
    """
    new_set = set(new_post_ids)
    rows = (
        db.query(Post)
        .filter(Post.is_published_avito.is_(True), Post.avito_item_id.isnot(None))
        .all()
    )
    keep: List[str] = []
    for post in rows:
        pid = str(post.id)
        if pid in new_set:
            continue
        product = db.query(Product).filter(Product.post_id == post.id).first()
        if product and str(product.status or "") == "unavailable":
            continue
        keep.append(pid)
    return keep


async def publish_autoload_batch(post_ids: List[str]) -> Dict[str, bool]:
    """
    Одна автозагрузка для нескольких постов (один XML, один POST /upload).
    Возвращает {post_id: успех}.
    """
    results: Dict[str, bool] = {pid: False for pid in post_ids}
    if not post_ids:
        return results
    if not AVITO_AUTOLOAD_CONTACT_PHONE:
        logger.error("Avito autoload: не задан AVITO_AUTOLOAD_CONTACT_PHONE")
        return results

    db = SessionLocal()
    try:
        new_ids = list(dict.fromkeys(post_ids))
        keep_alive = _keep_alive_avito_post_ids(db, new_ids)
        unique_ids = list(dict.fromkeys(new_ids + keep_alive))[
            : max(1, AVITO_AUTOLOAD_MAX_ADS_PER_BATCH)
        ]
        if keep_alive:
            logger.info(
                "Avito batch: keep-alive %s published ads in feed (anti-archive)",
                len(keep_alive),
            )

        posts_payload = []
        post_rows: List[Tuple[str, Post, Optional[Product], bool]] = []
        for pid in unique_ids:
            post = db.query(Post).filter(Post.id == pid).first()
            if not post:
                logger.warning("Avito batch: post %s not found", pid)
                continue
            product = db.query(Product).filter(Product.post_id == pid).first()
            had_avito = bool(post.avito_item_id or (product and product.avito_item_id))
            posts_payload.append(
                {
                    "post_id": str(post.id),
                    "post_text": post.text or "",
                    "post_name": post.name,
                    "photos": _photos_list(post),
                    "avito_draft": _draft_from_post(post)
                    or {"screen_level": 1, "body_level": 1},
                }
            )
            post_rows.append((pid, post, product, had_avito))

        if not posts_payload:
            db.commit()
            return results

        ad_ids = [sanitize_ad_id(p["post_id"]) for p in posts_payload]
        xml = build_feed_xml_for_posts(
            posts_payload,
            public_base_url=AVITO_FEED_PUBLIC_BASE_URL,
            contact_phone=AVITO_AUTOLOAD_CONTACT_PHONE,
            address=AVITO_AUTOLOAD_ADDRESS,
        )
        save_feed(
            xml,
            posts_payload[0]["post_id"],
            ad_ids[0],
            post_ids=[p["post_id"] for p in posts_payload],
            ad_ids=ad_ids,
        )
        logger.info(
            "Avito autoload batch: %s ads, post_ids=%s",
            len(posts_payload),
            [p["post_id"] for p in posts_payload],
        )

        try:
            await trigger_autoload_upload()
        except avito_http.AvitoApiError as e:
            logger.error("Avito autoload upload failed: %s", e)
            raise AvitoAutoCreateUnavailableError(_avito_error_user_message(e)) from e

        get_coordinator().record_upload_success()

        reports = await wait_for_avito_ids_after_upload(ad_ids)
        for pid, post, product, had_avito in post_rows:
            if had_avito and post.avito_item_id:
                results[pid] = True
                continue
            ad_key = sanitize_ad_id(pid)
            avito_id, status, msgs = reports.get(ad_key, (None, None, []))
            if avito_id:
                await _apply_avito_success(post, product, avito_id, db)
                results[pid] = True
                logger.info("Avito autoload: post %s → item %s", pid, avito_id)
            else:
                detail = "; ".join(msgs[:3]) if msgs else (status or "нет avito_id")
                logger.error("Avito autoload: post %s ad_id=%s — %s", pid, ad_key, detail)

        db.commit()
        return results
    except AvitoAutoCreateUnavailableError:
        db.rollback()
        raise
    except Exception as e:
        logger.error("Avito batch failed: %s", e, exc_info=True)
        db.rollback()
        return results
    finally:
        db.close()


async def publish_post_to_avito(post_id: str, signature_enabled: bool = True) -> bool:
    del signature_enabled

    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            logger.error("Post %s not found for Avito", post_id)
            return False

        product = db.query(Product).filter(Product.post_id == post_id).first()
        item_id_raw = None
        if product and product.avito_item_id:
            item_id_raw = product.avito_item_id
        elif post.avito_item_id:
            item_id_raw = post.avito_item_id

        if not item_id_raw and get_settings_service().can_enqueue_avito_without_linked_item(
            require_vk_market_for_pipeline=False,
            post_text=post.text or "",
        ):
            return enqueue_post_for_avito_autoload(post_id, priority=100)

        if not item_id_raw:
            logger.warning("Avito: нет привязанного avito_item_id для поста %s", post_id)
            return False

        try:
            item_id = int(str(item_id_raw).strip())
        except ValueError:
            logger.error("Avito: неверный avito_item_id %r", item_id_raw)
            return False

        info = await avito_actions.fetch_item_info(item_id)
        url = None
        if isinstance(info, dict):
            url = info.get("url") or info.get("avito_url")

        post.is_published_avito = True
        post.published_avito_at = datetime.now(timezone.utc)
        if url:
            post.avito_url = url
        if product and url:
            product.avito_url = url

        db.commit()
        logger.info("Avito: пост %s связан с объявлением %s, статус обновлён", post_id, item_id)
        return True
    except AvitoAutoCreateUnavailableError:
        db.rollback()
        raise
    except Exception as e:
        logger.error("Avito publish failed for %s: %s", post_id, e, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def enqueue_post_for_avito_autoload(post_id: str, priority: int = 50) -> bool:
    """Поставить пост в очередь автозагрузки (без немедленного upload)."""
    from app.scheduler.queue_manager import QueueManager

    qm = QueueManager()
    try:
        items = qm.add_post_to_queue(post_id, ["avito"], priority=priority)
        if items:
            get_coordinator().touch_enqueue()
            return True
        bumped = qm.bump_queue_priority_for_platforms(post_id, ["avito"], priority)
        if bumped:
            get_coordinator().touch_enqueue()
        return bumped > 0
    finally:
        qm.close()
