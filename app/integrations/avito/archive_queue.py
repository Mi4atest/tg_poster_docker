"""Очередь снятия объявлений с Авито (БД avito_feed_operations + миграция из JSON)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from app.api.models.avito_feed_operation import AvitoFeedOperation
from app.db.database import SessionLocal
from app.integrations.avito.autoload_coordinator import get_coordinator
from app.integrations.avito.feed_store import FEED_DIR

logger = logging.getLogger(__name__)

QUEUE_PATH = FEED_DIR / "archive_queue.json"
_JSON_MIGRATED = False


def _utcnow():
    return datetime.now(timezone.utc)


def _migrate_json_once() -> None:
    global _JSON_MIGRATED
    if _JSON_MIGRATED or not QUEUE_PATH.is_file():
        _JSON_MIGRATED = True
        return
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        _JSON_MIGRATED = True
        return

    db = SessionLocal()
    try:
        for row in items or []:
            if not isinstance(row, dict):
                continue
            pid = int(row.get("product_id") or 0)
            if not pid:
                continue
            exists = (
                db.query(AvitoFeedOperation)
                .filter(
                    AvitoFeedOperation.product_id == pid,
                    AvitoFeedOperation.operation_type == "archive",
                    AvitoFeedOperation.status.in_(("pending", "processing")),
                )
                .first()
            )
            if exists:
                continue
            op = AvitoFeedOperation(
                operation_type="archive",
                product_id=pid,
                post_id=row.get("post_id"),
                avito_item_id=int(row["avito_item_id"]) if row.get("avito_item_id") else None,
                product_name=(row.get("product_name") or "")[:255] or None,
                status=str(row.get("status") or "pending"),
                enqueued_at=_utcnow(),
                error_message=row.get("error_message"),
            )
            db.add(op)
        db.commit()
        logger.info("Avito archive queue: migrated %s items from JSON", len(items or []))
    except Exception as e:
        db.rollback()
        logger.warning("Avito archive JSON migration failed: %s", e)
    finally:
        db.close()
        _JSON_MIGRATED = True


def _row_to_dict(op: AvitoFeedOperation) -> dict:
    return {
        "id": op.id,
        "product_id": op.product_id,
        "avito_item_id": op.avito_item_id,
        "post_id": op.post_id,
        "product_name": op.product_name,
        "status": op.status,
        "enqueued_at": op.enqueued_at.isoformat() if op.enqueued_at else None,
        "error_message": op.error_message,
    }


async def reconcile_pending_with_avito() -> int:
    """
    Снимает из очереди записи, уже архивные на Авито (ручное снятие или прошлая выгрузка).
    """
    from app.integrations.avito.actions import fetch_item_info

    items = list_pending()
    if not items:
        return 0
    done = 0
    for row in items:
        iid = int(row.get("avito_item_id") or 0)
        pid = int(row.get("product_id") or 0)
        if not iid or not pid:
            continue
        try:
            info = await fetch_item_info(iid)
            status = str(info.get("status") or "")
            if status in ("old", "removed"):
                mark_completed(pid, product_name=row.get("product_name"))
                done += 1
        except Exception as e:
            logger.debug(
                "Avito reconcile skip product_id=%s item_id=%s: %s", pid, iid, e
            )
    if done:
        logger.info("Avito archive queue: reconciled %s pending → completed", done)
    return done


def list_pending() -> List[dict]:
    _migrate_json_once()
    db = SessionLocal()
    try:
        rows = (
            db.query(AvitoFeedOperation)
            .filter(
                AvitoFeedOperation.operation_type == "archive",
                AvitoFeedOperation.status == "pending",
            )
            .order_by(AvitoFeedOperation.enqueued_at.asc())
            .all()
        )
        return [_row_to_dict(r) for r in rows]
    finally:
        db.close()


def count_pending() -> int:
    return len(list_pending())


def get_last_failed_error(product_id: int) -> Optional[str]:
    db = SessionLocal()
    try:
        op = (
            db.query(AvitoFeedOperation)
            .filter(
                AvitoFeedOperation.product_id == int(product_id),
                AvitoFeedOperation.operation_type == "archive",
                AvitoFeedOperation.status == "failed",
            )
            .order_by(AvitoFeedOperation.failed_at.desc())
            .first()
        )
        return (op.error_message or "").strip() or None if op else None
    finally:
        db.close()


def find_pending_product(product_id: int) -> Optional[dict]:
    for item in list_pending():
        if int(item.get("product_id") or 0) == int(product_id):
            return item
    return None


def format_pending_detail() -> str:
    coord = get_coordinator()
    n = count_pending()
    upload_wait = coord.seconds_until_next_upload()
    if upload_wait <= 0 and n <= 1:
        return "В очереди на снятие — отправьте файл в меню «В очереди → Авито»."
    from app.utils.time_msk import format_hm_msk

    eta = coord.next_upload_at()
    hm = format_hm_msk(eta)
    mins = max(1, (upload_wait + 59) // 60) if upload_wait > 0 else 1
    suffix = f" (~{mins} мин)" if upload_wait > 0 else ""
    queue_note = f" В очереди на снятие: {n}." if n > 1 else ""
    return (
        f"В очереди на снятие. Файл на Авито не раньше {hm}{suffix}.{queue_note} "
        "Меню «В очереди → Авито»."
    )


def enqueue(
    *,
    product_id: int,
    avito_item_id: int,
    post_id: Optional[str] = None,
    product_name: Optional[str] = None,
) -> Tuple[bool, str]:
    _migrate_json_once()
    db = SessionLocal()
    try:
        existing = (
            db.query(AvitoFeedOperation)
            .filter(
                AvitoFeedOperation.product_id == int(product_id),
                AvitoFeedOperation.operation_type == "archive",
                AvitoFeedOperation.status == "pending",
            )
            .first()
        )
        if existing:
            return False, format_pending_detail()

        failed = (
            db.query(AvitoFeedOperation)
            .filter(
                AvitoFeedOperation.product_id == int(product_id),
                AvitoFeedOperation.operation_type == "archive",
                AvitoFeedOperation.status == "failed",
            )
            .order_by(AvitoFeedOperation.id.desc())
            .first()
        )
        if failed:
            failed.status = "pending"
            failed.failed_at = None
            failed.error_message = None
            failed.enqueued_at = _utcnow()
            failed.avito_item_id = int(avito_item_id)
            if post_id:
                failed.post_id = str(post_id)
            if product_name:
                failed.product_name = (product_name or "")[:255] or None
            db.commit()
            get_coordinator().touch_enqueue()
            logger.info(
                "Avito archive queue: requeued failed product_id=%s avito_item_id=%s",
                product_id,
                avito_item_id,
            )
            return True, format_pending_detail()

        op = AvitoFeedOperation(
            operation_type="archive",
            product_id=int(product_id),
            avito_item_id=int(avito_item_id),
            post_id=str(post_id) if post_id else None,
            product_name=(product_name or "")[:255] or None,
            status="pending",
            enqueued_at=_utcnow(),
        )
        db.add(op)
        db.commit()
        get_coordinator().touch_enqueue()
        logger.info(
            "Avito archive queue: enqueued product_id=%s avito_item_id=%s",
            product_id,
            avito_item_id,
        )
        return True, format_pending_detail()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_processing(product_ids: List[int]) -> None:
    if not product_ids:
        return
    want = {int(x) for x in product_ids}
    db = SessionLocal()
    try:
        now = _utcnow()
        for op in (
            db.query(AvitoFeedOperation)
            .filter(
                AvitoFeedOperation.operation_type == "archive",
                AvitoFeedOperation.status == "pending",
                AvitoFeedOperation.product_id.in_(want),
            )
            .all()
        ):
            op.status = "processing"
            op.started_at = now
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_completed(product_id: int, *, product_name: Optional[str] = None) -> None:
    db = SessionLocal()
    try:
        now = _utcnow()
        q = db.query(AvitoFeedOperation).filter(
            AvitoFeedOperation.product_id == int(product_id),
            AvitoFeedOperation.operation_type == "archive",
            AvitoFeedOperation.status.in_(("pending", "processing")),
        )
        for op in q.all():
            op.status = "completed"
            op.completed_at = now
            if product_name and not op.product_name:
                op.product_name = product_name[:255]
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_failed(product_id: int, error: str) -> None:
    db = SessionLocal()
    try:
        now = _utcnow()
        for op in (
            db.query(AvitoFeedOperation)
            .filter(
                AvitoFeedOperation.product_id == int(product_id),
                AvitoFeedOperation.operation_type == "archive",
                AvitoFeedOperation.status.in_(("pending", "processing")),
            )
            .all()
        ):
            op.status = "failed"
            op.failed_at = now
            op.error_message = (error or "")[:2000]
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cancel_pending_product(product_id: int) -> None:
    db = SessionLocal()
    try:
        (
            db.query(AvitoFeedOperation)
            .filter(
                AvitoFeedOperation.product_id == int(product_id),
                AvitoFeedOperation.operation_type == "archive",
                AvitoFeedOperation.status == "pending",
            )
            .delete(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_recent_completed(*, hours: int = 48, limit: Optional[int] = None) -> List[dict]:
    """Для блока уведомлений в меню «В очереди» (без push в Telegram)."""
    _migrate_json_once()
    since = datetime.utcnow() - timedelta(hours=hours)
    db = SessionLocal()
    try:
        q = (
            db.query(AvitoFeedOperation)
            .filter(
                AvitoFeedOperation.operation_type == "archive",
                AvitoFeedOperation.status == "completed",
                AvitoFeedOperation.completed_at.isnot(None),
                AvitoFeedOperation.completed_at >= since,
            )
            .order_by(AvitoFeedOperation.completed_at.desc())
        )
        if limit is not None:
            q = q.limit(limit)
        rows = q.all()
        out = []
        for op in rows:
            name = (op.product_name or f"Товар #{op.product_id}").strip()
            ts = op.completed_at
            if ts:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                from app.utils.time_msk import format_hm_msk

                time_str = format_hm_msk(ts)
            else:
                time_str = "—"
            out.append(
                {
                    "product_id": op.product_id,
                    "product_name": name,
                    "completed_at": ts,
                    "time_str": time_str,
                }
            )
        return out
    finally:
        db.close()
