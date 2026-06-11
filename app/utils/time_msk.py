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


def format_dashboard_ts_msk(dt: datetime) -> str:
    """Время для дашборда: сегодня — только HH:MM, иначе DD.MM HH:MM (МСК)."""
    local = to_msk(dt)
    if local.date() == datetime.now(MSK).date():
        return local.strftime("%H:%M")
    return local.strftime("%d.%m %H:%M")
