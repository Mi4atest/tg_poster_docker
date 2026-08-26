"""Время для пользовательских сообщений (часовой пояс проекта — Москва)."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

MSK_MONTH_NAMES = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


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


def format_status_date_msk(dt: datetime) -> str:
    """Дата и время для подписи под статусом товара (МСК)."""
    return to_msk(dt).strftime("%d.%m.%Y, %H:%M")


def msk_month_bounds_naive_utc(
    when: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """Начало и конец текущего календарного месяца (МСК) как naive UTC + имя месяца.

    archived_at в БД пишется через datetime.utcnow() — сравниваем naive UTC.
    """
    local = to_msk(when) if when is not None else datetime.now(MSK)
    start_msk = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_msk.month == 12:
        end_msk = start_msk.replace(year=start_msk.year + 1, month=1)
    else:
        end_msk = start_msk.replace(month=start_msk.month + 1)
    start_utc = start_msk.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_msk.astimezone(timezone.utc).replace(tzinfo=None)
    month_name = MSK_MONTH_NAMES[start_msk.month]
    return start_utc, end_utc, month_name
