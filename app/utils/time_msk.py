"""Время для пользовательских сообщений (часовой пояс проекта — Москва)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def to_msk(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)


def format_hm_msk(dt: datetime) -> str:
    return to_msk(dt).strftime("%H:%M")
