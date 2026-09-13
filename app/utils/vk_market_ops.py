"""Очередь VK Market: один запрос, при flood — повтор не раньше чем через 3 часа.

Короткие ретраи (секунды/15 минут) при многочасовом flood продлевают окно.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from app.db.database import SessionLocal
from app.utils.vk_client import (
    get_market_vk_session,
    resolved_vk_group_id_int,
    vk_api_error_code,
    vk_user_error_detail,
)
from app.utils.vk_flood_gate import VkFloodBlocked
from app.utils.vk_flood_gate import flood_until as vk_flood_until
from app.utils.vk_flood_gate import note_exception as note_vk_flood
from app.utils.vk_flood_gate import raise_if_blocked as raise_if_vk_flood_blocked
from app.workers.vk.upload_retry import is_vk_flood_error

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(hours=3)
MAX_ATTEMPTS = 16
_TRANSIENT_CODES = {6, 8, 9, 10, 29}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, VkFloodBlocked):
        return True
    code = vk_api_error_code(exc)
    return code in _TRANSIENT_CODES or is_vk_flood_error(exc)


def _retry_delay() -> timedelta:
    """Повтор — когда истечёт стоп-кран, иначе стандартные 3 часа."""
    until = vk_flood_until()
    if not until:
        return RETRY_AFTER
    left = until.replace(tzinfo=None) - _now() + timedelta(minutes=2)
    return left if left > timedelta(minutes=2) else timedelta(minutes=2)


def enqueue(
    *,
    product_id: int,
    vk_product_id: int,
    action: str,
    payload: Optional[dict[str, Any]] = None,
    error: str = "",
    delay: timedelta = RETRY_AFTER,
) -> None:
    """Поставить/обновить pending-операцию. Не сбрасывает next_retry_at в прошлое."""
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    now = _now()
    due = now + delay
    with SessionLocal() as db:
        row = (
            db.execute(
                text(
                    "SELECT id, next_retry_at FROM vk_market_ops "
                    "WHERE product_id = :pid AND action = :action AND status = 'pending' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"pid": int(product_id), "action": action},
            )
            .mappings()
            .first()
        )
        if row:
            next_at = row.get("next_retry_at") or due
            if next_at < due:
                next_at = due
            db.execute(
                text(
                    "UPDATE vk_market_ops SET vk_product_id = :vid, payload = :payload, "
                    "last_error = :err, next_retry_at = :due, updated_at = :now "
                    "WHERE id = :id"
                ),
                {
                    "vid": int(vk_product_id),
                    "payload": payload_json,
                    "err": (error or "")[:500],
                    "due": next_at,
                    "now": now,
                    "id": row["id"],
                },
            )
        else:
            db.execute(
                text(
                    "INSERT INTO vk_market_ops "
                    "(product_id, vk_product_id, action, payload, status, attempts, "
                    "next_retry_at, last_error, created_at, updated_at) "
                    "VALUES (:pid, :vid, :action, :payload, 'pending', 0, :due, :err, :now, :now)"
                ),
                {
                    "pid": int(product_id),
                    "vid": int(vk_product_id),
                    "action": action,
                    "payload": payload_json,
                    "due": due,
                    "err": (error or "")[:500],
                    "now": now,
                },
            )
        db.commit()
    logger.info(
        "VK market op queued product_id=%s action=%s retry_in=%ss",
        product_id,
        action,
        int(delay.total_seconds()),
    )


def _execute(action: str, vk_product_id: int, payload: dict[str, Any]) -> None:
    raise_if_vk_flood_blocked(f"market.{action}")
    owner_id = -resolved_vk_group_id_int()
    vk = get_market_vk_session().get_api()
    if action == "hide":
        vk.market.edit(owner_id=owner_id, item_id=int(vk_product_id), deleted=1)
    elif action == "show":
        vk.market.edit(owner_id=owner_id, item_id=int(vk_product_id), deleted=0)
    elif action == "delete":
        vk.market.delete(owner_id=owner_id, item_id=int(vk_product_id))
    elif action == "price":
        vk.market.edit(
            owner_id=owner_id,
            item_id=int(vk_product_id),
            price=int(payload.get("price") or 0),
        )
    else:
        raise ValueError(f"unknown vk market action {action!r}")


def apply_or_enqueue(
    *,
    product_id: int,
    vk_product_id: int,
    action: str,
    payload: Optional[dict[str, Any]] = None,
) -> tuple[bool, str]:
    """Один вызов API. При flood/временной ошибке — в очередь, без серии ретраев."""
    payload = payload or {}
    try:
        _execute(action, vk_product_id, payload)
        return True, "ok"
    except Exception as exc:
        note_vk_flood(exc, f"market.{action}")
        detail = vk_user_error_detail(exc)
        # #region agent log
        try:
            from app.utils.vk_debug_log import vk_dbg, vk_exc_debug_meta, vk_token_debug_meta

            err = getattr(exc, "error", None)
            extra = {}
            if isinstance(err, dict):
                extra = {
                    "error_keys": sorted(str(k) for k in err.keys()),
                    "captcha": bool(err.get("captcha_sid")),
                }
            vk_dbg(
                "F",
                "vk_market_ops.py:apply_or_enqueue",
                "VK market call failed",
                {
                    "product_id": product_id,
                    "action": action,
                    **vk_exc_debug_meta(exc),
                    "token": vk_token_debug_meta(),
                    **extra,
                },
                run_id="post-fix",
            )
        except Exception:
            pass
        # #endregion
        if _is_transient(exc):
            delay = _retry_delay()
            enqueue(
                product_id=product_id,
                vk_product_id=vk_product_id,
                action=action,
                payload=payload,
                error=str(exc)[:500],
                delay=delay,
            )
            from app.utils.time_msk import format_dashboard_ts_msk

            due_msk = format_dashboard_ts_msk(datetime.now(timezone.utc) + delay)
            return False, f"{detail} — повтор сам в {due_msk} МСК"
        return False, detail


def process_due_operation() -> Optional[str]:
    """Обработать одну просроченную операцию. Возвращает итог для логов."""
    if vk_flood_until():
        return None
    now = _now()
    with SessionLocal() as db:
        row = (
            db.execute(
                text(
                    "SELECT id, product_id, vk_product_id, action, payload, attempts "
                    "FROM vk_market_ops "
                    "WHERE status = 'pending' AND (next_retry_at IS NULL OR next_retry_at <= :now) "
                    "ORDER BY next_retry_at NULLS FIRST, id ASC LIMIT 1"
                ),
                {"now": now},
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        op_id = int(row["id"])
        db.execute(
            text(
                "UPDATE vk_market_ops SET status = 'processing', updated_at = :now WHERE id = :id"
            ),
            {"now": now, "id": op_id},
        )
        db.commit()
        data = dict(row)

    payload: dict[str, Any] = {}
    raw = data.get("payload") or ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}

    attempts = int(data.get("attempts") or 0) + 1
    action = str(data.get("action") or "")
    vk_product_id = int(data["vk_product_id"])
    product_id = int(data["product_id"])
    try:
        _execute(action, vk_product_id, payload)
        with SessionLocal() as db:
            db.execute(
                text(
                    "UPDATE vk_market_ops SET status = 'done', attempts = :att, "
                    "last_error = NULL, updated_at = :now WHERE id = :id"
                ),
                {"att": attempts, "now": _now(), "id": op_id},
            )
            db.commit()
        # #region agent log
        try:
            from app.utils.vk_debug_log import vk_dbg

            vk_dbg(
                "F",
                "vk_market_ops.py:process_due_operation",
                "VK delayed op succeeded",
                {"product_id": product_id, "action": action, "attempts": attempts},
                run_id="post-fix",
            )
        except Exception:
            pass
        # #endregion
        logger.info(
            "VK market delayed op done id=%s product_id=%s action=%s attempts=%s",
            op_id,
            product_id,
            action,
            attempts,
        )
        return f"done {action} product={product_id}"
    except Exception as exc:
        note_vk_flood(exc, f"market.{action}")
        transient = _is_transient(exc)
        status = "pending" if transient and attempts < MAX_ATTEMPTS else "failed"
        due = _now() + _retry_delay() if status == "pending" else None
        with SessionLocal() as db:
            db.execute(
                text(
                    "UPDATE vk_market_ops SET status = :st, attempts = :att, "
                    "last_error = :err, next_retry_at = :due, updated_at = :now WHERE id = :id"
                ),
                {
                    "st": status,
                    "att": attempts,
                    "err": str(exc)[:500],
                    "due": due,
                    "now": _now(),
                    "id": op_id,
                },
            )
            db.commit()
        # #region agent log
        try:
            from app.utils.vk_debug_log import vk_dbg, vk_exc_debug_meta

            vk_dbg(
                "F",
                "vk_market_ops.py:process_due_operation",
                "VK delayed op failed",
                {
                    "product_id": product_id,
                    "action": action,
                    "attempts": attempts,
                    "status": status,
                    **vk_exc_debug_meta(exc),
                },
                run_id="post-fix",
            )
        except Exception:
            pass
        # #endregion
        logger.warning(
            "VK market delayed op %s id=%s product_id=%s action=%s: %s",
            status,
            op_id,
            product_id,
            action,
            exc,
        )
        return f"{status} {action} product={product_id}"
