"""Учёт лимита автозагрузки Авито (1 upload/час) и паузы накопления батча."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config.settings import (
    AVITO_AUTOLOAD_BATCH_QUIET_SEC,
    AVITO_AUTOLOAD_MIN_UPLOAD_INTERVAL_SEC,
)
from app.integrations.avito.feed_store import FEED_DIR

STATE_PATH = FEED_DIR / "upload_state.json"


def _ensure_dir() -> None:
    FEED_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if not STATE_PATH.is_file():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(data: dict) -> None:
    _ensure_dir()
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


class AvitoAutoloadCoordinator:
    """Один запуск upload в час; батч собирается после «тишины» в очереди."""

    def __init__(self) -> None:
        self.batch_quiet_sec = max(30, int(AVITO_AUTOLOAD_BATCH_QUIET_SEC))
        self.min_upload_interval_sec = max(60, int(AVITO_AUTOLOAD_MIN_UPLOAD_INTERVAL_SEC))

    def touch_enqueue(self) -> None:
        state = _load_state()
        state["last_enqueue_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(state)

    def record_upload_success(self) -> None:
        state = _load_state()
        now = datetime.now(timezone.utc).isoformat()
        state["last_upload_at"] = now
        state["last_enqueue_at"] = state.get("last_enqueue_at") or now
        _save_state(state)

    def last_upload_at(self) -> Optional[datetime]:
        return _parse_dt(_load_state().get("last_upload_at"))

    def last_enqueue_at(self) -> Optional[datetime]:
        return _parse_dt(_load_state().get("last_enqueue_at"))

    def seconds_until_next_upload(self) -> int:
        last = self.last_upload_at()
        if not last:
            return 0
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        remain = self.min_upload_interval_sec - elapsed
        return max(0, int(remain))

    def can_trigger_upload(self) -> bool:
        return self.seconds_until_next_upload() <= 0

    def seconds_until_batch_ready(self) -> int:
        """Секунды до окончания окна накопления после последнего enqueue."""
        last = self.last_enqueue_at()
        if not last:
            return 0
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        remain = self.batch_quiet_sec - elapsed
        return max(0, int(remain))

    def is_batch_ready(self) -> bool:
        return self.seconds_until_batch_ready() <= 0

    def next_upload_at(self) -> datetime:
        wait_upload = self.seconds_until_next_upload()
        wait_batch = self.seconds_until_batch_ready()
        sec = max(wait_upload, wait_batch)
        return datetime.now(timezone.utc) + timedelta(seconds=sec)

    def format_eta_hint(self, pending_count: int = 0) -> str:
        if pending_count <= 0:
            return ""
        upload_wait = self.seconds_until_next_upload()
        batch_wait = self.seconds_until_batch_ready()
        total = max(upload_wait, batch_wait)
        if total <= 0:
            return (
                f"В очереди Авито: {pending_count} — выгрузка скоро "
                f"(до {pending_count} объявлений за один запуск API)."
            )
        from app.utils.time_msk import format_hm_msk

        eta = self.next_upload_at()
        mins = (total + 59) // 60
        return (
            f"В очереди Авито: {pending_count}. "
            f"Ориентировочно ~{mins} мин (не раньше {format_hm_msk(eta)}). "
            f"Несколько постов уйдут одной автозагрузкой — лимит API 1 раз в час."
        )


_coordinator: Optional[AvitoAutoloadCoordinator] = None


def get_coordinator() -> AvitoAutoloadCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = AvitoAutoloadCoordinator()
    return _coordinator
