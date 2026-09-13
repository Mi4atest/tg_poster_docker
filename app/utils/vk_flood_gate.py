"""Стоп-кран VK API после ошибки 9 (Flood control).

Логи 8–9 сентября: бот повторял запросы внутрь флуда (загрузка фото 6 попыток,
ретраи очереди, ретраи market.edit) — окно не истекало часами. Пока стоп-кран
активен, в VK не уходит ни один запрос, чтобы лимит успел сброситься.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

FLOOD_COOLDOWN = timedelta(hours=3)
_SETTINGS_KEY = "vk_flood"

# Состояние живёт в БД: воркеры бота, API и ручные запуски — разные процессы.
_CACHE_TTL = timedelta(seconds=20)

_lock = threading.Lock()
_cached_until: Optional[datetime] = None
_cache_read_at: Optional[datetime] = None


class VkFloodBlocked(RuntimeError):
    """VK API временно не вызываем: активен стоп-кран после flood control."""

    code = 9


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _load_from_settings() -> Optional[datetime]:
    try:
        from app.services.settings_service import get_settings_service

        data = get_settings_service().get_all()
        return _parse((data.get(_SETTINGS_KEY) or {}).get("until"))
    except Exception as exc:
        logger.warning("VK flood gate: cannot read state: %s", exc)
        return None


def _store_to_settings(until: datetime) -> None:
    try:
        from app.services.settings_service import get_settings_service

        get_settings_service().update({_SETTINGS_KEY: {"until": until.isoformat()}})
    except Exception as exc:
        logger.warning("VK flood gate: cannot persist state: %s", exc)


def flood_until() -> Optional[datetime]:
    """Момент, до которого VK не трогаем (None — можно работать)."""
    global _cached_until, _cache_read_at
    now = _now()
    with _lock:
        stale = _cache_read_at is None or now - _cache_read_at > _CACHE_TTL
        if stale:
            _cached_until = _load_from_settings()
            _cache_read_at = now
        until = _cached_until
    if until and until <= now:
        return None
    return until


def is_blocked() -> bool:
    return flood_until() is not None


def blocked_detail() -> str:
    until = flood_until()
    if not until:
        return ""
    from app.utils.time_msk import format_dashboard_ts_msk

    return f"ВК: пауза после flood до {format_dashboard_ts_msk(until)} МСК"


def raise_if_blocked(label: str = "vk") -> None:
    until = flood_until()
    if until:
        raise VkFloodBlocked(f"VK flood cooldown active until {until.isoformat()} ({label})")


def mark_flood(source: str, *, cooldown: timedelta = FLOOD_COOLDOWN) -> datetime:
    """Включить стоп-кран: до конца окна в VK не ходим."""
    global _cached_until, _cache_read_at
    until = _now() + cooldown
    with _lock:
        current = _load_from_settings()
        if current and current > until:
            until = current
        _cached_until = until
        _cache_read_at = _now()
    _store_to_settings(until)
    logger.warning("VK flood gate armed by %s until %s", source, until.isoformat())
    # #region agent log
    try:
        from app.utils.vk_debug_log import vk_dbg

        vk_dbg(
            "G",
            "vk_flood_gate.py:mark_flood",
            "VK flood gate armed",
            {"source": source, "until": until.isoformat()},
            run_id="post-fix",
        )
    except Exception:
        pass
    # #endregion
    return until


def clear_flood(reason: str = "manual") -> None:
    """Снять стоп-кран (после смены токена/аккаунта)."""
    global _cached_until, _cache_read_at
    with _lock:
        _cached_until = None
        _cache_read_at = _now()
    _store_to_settings(_now() - timedelta(seconds=1))
    logger.info("VK flood gate cleared (%s)", reason)


def note_exception(exc: BaseException, source: str) -> bool:
    """Если это flood (код 9) — включить стоп-кран. True, когда окно активно."""
    if isinstance(exc, VkFloodBlocked):
        return True
    from app.utils.vk_client import vk_api_error_code

    if vk_api_error_code(exc) == 9 or "flood control" in str(exc).lower():
        mark_flood(source)
        return True
    return False
