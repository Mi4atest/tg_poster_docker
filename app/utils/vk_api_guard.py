"""Единый локальный guard перед VK API: flood-gate + минимальный интервал + метрики."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from app.config import settings as env_settings
from app.utils.vk_flood_gate import note_exception as note_vk_flood
from app.utils.vk_flood_gate import raise_if_blocked as raise_if_vk_flood_blocked

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_call_at = 0.0
_call_count = 0


def _min_interval() -> float:
    try:
        return max(0.0, float(getattr(env_settings, "VK_API_MIN_INTERVAL_SEC", 0.35) or 0.0))
    except (TypeError, ValueError):
        return 0.35


def before_vk_call(label: str = "vk") -> None:
    """Стоп-кран flood + сериализация вызовов внутри процесса."""
    global _last_call_at, _call_count
    raise_if_vk_flood_blocked(label)
    interval = _min_interval()
    with _lock:
        now = time.monotonic()
        wait = (_last_call_at + interval) - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_call_at = now
        _call_count += 1
        count = _call_count
    logger.debug(
        "VK API slot acquired label=%s call_n=%s interval=%.2fs",
        label,
        count,
        interval,
    )


def after_vk_error(exc: BaseException, label: str = "vk") -> bool:
    """Зафиксировать flood/quota и при необходимости алертнуть."""
    armed = note_vk_flood(exc, label)
    if armed and getattr(env_settings, "VK_QUOTA_ALERT_ENABLED", True):
        _maybe_alert_quota(exc, label)
    return armed


def vk_call_count() -> int:
    with _lock:
        return _call_count


def _maybe_alert_quota(exc: BaseException, label: str) -> None:
    try:
        from app.utils.vk_client import vk_api_error_code, vk_app_id
        from app.utils.vk_client import resolved_vk_group_id_int

        code = vk_api_error_code(exc)
        if code not in (9, 29) and "flood" not in str(exc).lower() and "limit" not in str(exc).lower():
            return
        try:
            gid = resolved_vk_group_id_int()
        except Exception:
            gid = None
        text = (
            f"⚠️ VK API pause/quota\n"
            f"app_id={vk_app_id()} group={gid} label={label}\n"
            f"code={code} msg={str(exc)[:180]}"
        )
        _send_alert_best_effort(text)
    except Exception:
        logger.debug("VK quota alert skipped", exc_info=True)


def _send_alert_best_effort(text: str) -> None:
    """Sync alert без зависимости от running event loop."""
    try:
        import json
        from urllib.request import Request, urlopen

        token = (getattr(env_settings, "TELEGRAM_BOT_TOKEN", None) or "").strip()
        if not token:
            return
        from app.services.admin_alert_service import _resolve_alert_chat_ids

        for chat_id in _resolve_alert_chat_ids()[:3]:
            payload = json.dumps(
                {"chat_id": chat_id, "text": text},
                ensure_ascii=False,
            ).encode("utf-8")
            req = Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urlopen(req, timeout=5).read()
            except Exception:
                continue
    except Exception:
        logger.warning("VK admin alert failed", exc_info=True)


def log_vk_metric(
    *,
    method: str,
    code: Optional[int] = None,
    latency_ms: Optional[float] = None,
    ok: bool = True,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Операционный лог без токенов."""
    try:
        from app.utils.vk_client import resolved_vk_group_id_int, vk_app_id

        try:
            gid = resolved_vk_group_id_int()
        except Exception:
            gid = None
        payload = {
            "app_id": vk_app_id(),
            "group_id": gid,
            "method": method,
            "ok": ok,
            "code": code,
            "latency_ms": latency_ms,
            "call_n": vk_call_count(),
        }
        if extra:
            payload.update(extra)
        logger.info("vk_metric %s", payload)
    except Exception:
        logger.debug("vk_metric skipped", exc_info=True)
