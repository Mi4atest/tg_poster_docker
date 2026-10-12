"""Время для пользовательских сообщений (часовой пояс проекта — Москва)."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
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

_DATE_RE = re.compile(
    r"^\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?\s*$"
)
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


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


def format_schedule_msk(dt: datetime) -> str:
    """Подпись запланированного времени: ДД.ММ ЧЧ:ММ (МСК)."""
    return to_msk(dt).strftime("%d.%m %H:%M") + " (МСК)"


def msk_now() -> datetime:
    return datetime.now(MSK)


def combine_msk_naive_utc(day: date, hour: int, minute: int) -> datetime:
    """Собрать naive UTC datetime из календарного дня и времени МСК."""
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=MSK)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def parse_msk_date(text: str, *, now: datetime | None = None) -> date:
    """Распарсить ДД.ММ / ДД.ММ.ГГ / ДД.ММ.ГГГГ относительно текущего года МСК."""
    match = _DATE_RE.match(text or "")
    if not match:
        raise ValueError("Некорректная дата")
    day_n = int(match.group(1))
    month_n = int(match.group(2))
    year_raw = match.group(3)
    ref = to_msk(now) if now is not None else msk_now()
    if year_raw is None:
        year_n = ref.year
    elif len(year_raw) == 2:
        year_n = 2000 + int(year_raw)
    else:
        year_n = int(year_raw)
    try:
        return date(year_n, month_n, day_n)
    except ValueError as exc:
        raise ValueError("Некорректная дата") from exc


def parse_msk_time(text: str) -> time:
    """Распарсить ЧЧ:ММ."""
    match = _TIME_RE.match(text or "")
    if not match:
        raise ValueError("Некорректное время")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("Некорректное время")
    return time(hour, minute)


def parse_schedule_msk_to_naive_utc(
    day: date,
    hour: int,
    minute: int,
    *,
    now: datetime | None = None,
    min_ahead_seconds: int = 60,
) -> datetime:
    """Собрать и проверить scheduled_at (naive UTC) — строго в будущем МСК."""
    naive_utc = combine_msk_naive_utc(day, hour, minute)
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref_aware = ref.replace(tzinfo=timezone.utc)
    else:
        ref_aware = ref.astimezone(timezone.utc)
    target_aware = naive_utc.replace(tzinfo=timezone.utc)
    if target_aware < ref_aware + timedelta(seconds=min_ahead_seconds):
        raise ValueError("Время должно быть в будущем (минимум +1 мин)")
    return naive_utc


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
