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

MSK_MONTH_GENITIVE = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

_DATE_RE = re.compile(
    r"^\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?\s*$"
)
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_DIGITS_RE = re.compile(r"^\d+$")
_WS_RE = re.compile(r"\s+")


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
    """Компактная подпись (кнопки): ДД.ММ ЧЧ:ММ (МСК)."""
    return to_msk(dt).strftime("%d.%m %H:%M") + " (МСК)"


def format_schedule_human_msk(dt: datetime) -> str:
    """Человекочитаемо: 16 октября 2026 в 21:00."""
    local = to_msk(dt)
    month = MSK_MONTH_GENITIVE[local.month]
    return f"{local.day} {month} {local.year} в {local.strftime('%H:%M')}"


def format_schedule_confirm_msk(dt: datetime) -> str:
    """Подпись для подтверждения (как human)."""
    return format_schedule_human_msk(dt)


def msk_now() -> datetime:
    return datetime.now(MSK)


def combine_msk_naive_utc(day: date, hour: int, minute: int) -> datetime:
    """Собрать naive UTC datetime из календарного дня и времени МСК."""
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=MSK)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _year_from_raw(year_raw: str | None, ref: datetime) -> int:
    if year_raw is None:
        return ref.year
    if len(year_raw) == 2:
        return 2000 + int(year_raw)
    return int(year_raw)


def _make_date(day_n: int, month_n: int, year_n: int) -> date:
    try:
        return date(year_n, month_n, day_n)
    except ValueError as exc:
        raise ValueError("Некорректная дата") from exc


def _make_time(hour: int, minute: int) -> time:
    if hour > 23 or minute > 59:
        raise ValueError("Некорректное время")
    return time(hour, minute)


def parse_msk_date(text: str, *, now: datetime | None = None) -> date:
    """Распарсить дату: ДД.ММ[.ГГ[ГГ]] или компактно ДДММ / ДДММГГ / ДДММГГГГ."""
    raw = (text or "").strip()
    match = _DATE_RE.match(raw)
    ref = to_msk(now) if now is not None else msk_now()
    if match:
        return _make_date(
            int(match.group(1)),
            int(match.group(2)),
            _year_from_raw(match.group(3), ref),
        )
    if _DIGITS_RE.match(raw):
        if len(raw) == 4:  # ДДММ
            return _make_date(int(raw[:2]), int(raw[2:4]), ref.year)
        if len(raw) == 6:  # ДДММГГ
            return _make_date(
                int(raw[:2]), int(raw[2:4]), _year_from_raw(raw[4:6], ref)
            )
        if len(raw) == 8:  # ДДММГГГГ
            return _make_date(
                int(raw[:2]), int(raw[2:4]), _year_from_raw(raw[4:8], ref)
            )
    raise ValueError("Некорректная дата")


def parse_msk_time(text: str) -> time:
    """Распарсить время: ЧЧ:ММ или компактно ЧЧММ / ЧММ."""
    raw = (text or "").strip()
    match = _TIME_RE.match(raw)
    if match:
        return _make_time(int(match.group(1)), int(match.group(2)))
    if _DIGITS_RE.match(raw) and len(raw) in (3, 4):
        hhmm = raw.zfill(4)
        return _make_time(int(hhmm[:2]), int(hhmm[2:]))
    raise ValueError("Некорректное время")


def parse_schedule_user_input(
    text: str, *, now: datetime | None = None
) -> tuple[date, time | None]:
    """Гибкий ввод даты/времени для мастера «Запланировать».

    Возвращает (день, время|None). None у времени — нужен выбор кнопками.

    Примеры:
      171026 / 17.10.26          → дата
      1610 171026                → 16:10 + дата
      16:10 17.10.2026           → то же
      1610171026 / 161017102026  → склеено ЧЧММ+ДДММГГ[ГГ]
    """
    raw = _WS_RE.sub(" ", (text or "").strip())
    if not raw:
        raise ValueError("Пустой ввод")

    parts = raw.split(" ")
    if len(parts) == 2:
        a, b = parts[0], parts[1]
        # Сначала «время дата» (как в подсказке: 1610 171026).
        try:
            return parse_msk_date(b, now=now), parse_msk_time(a)
        except ValueError:
            pass
        try:
            return parse_msk_date(a, now=now), parse_msk_time(b)
        except ValueError as exc:
            raise ValueError("Некорректные дата и время") from exc

    if len(parts) != 1:
        raise ValueError("Некорректный формат")

    token = parts[0]
    # Склеенные цифры: ЧЧММ + дата
    if _DIGITS_RE.match(token):
        if len(token) == 10:  # ЧЧММДДММГГ
            t = parse_msk_time(token[:4])
            d = parse_msk_date(token[4:], now=now)
            return d, t
        if len(token) == 12:  # ЧЧММДДММГГГГ
            t = parse_msk_time(token[:4])
            d = parse_msk_date(token[4:], now=now)
            return d, t
        # 4/6/8 — только дата
        if len(token) in (4, 6, 8):
            return parse_msk_date(token, now=now), None

    # С разделителями — только дата (время без даты не сюда)
    return parse_msk_date(token, now=now), None


def parse_schedule_flexible(
    text: str,
    *,
    prefer_time: bool = False,
    now: datetime | None = None,
) -> tuple[date | None, time | None]:
    """Разбор ввода мастера: дата и/или время.

    prefer_time=True — когда день уже выбран кнопкой (1005 → 10:05, не 10.05).
    Возвращает (day|None, time|None); хотя бы одно не None при успехе.
    """
    raw = _WS_RE.sub(" ", (text or "").strip())
    if not raw:
        raise ValueError("Пустой ввод")

    # После «Сегодня/Завтра» 3–4 цифры = время.
    if prefer_time and _DIGITS_RE.match(raw) and len(raw) in (3, 4):
        return None, parse_msk_time(raw)
    if prefer_time and _TIME_RE.match(raw):
        return None, parse_msk_time(raw)

    try:
        return parse_schedule_user_input(raw, now=now)
    except ValueError:
        pass

    # Только время: 940 / 0940 / 09:40
    try:
        return None, parse_msk_time(raw)
    except ValueError as exc:
        raise ValueError("Некорректный ввод") from exc


SCHEDULE_INPUT_HINT_HTML = (
    "Ввод <b>МСК</b>, можно без точек и двоеточий:\n"
    "• <code>171026</code> или <code>17.10.26</code> — только дата, потом время\n"
    "• <code>940</code> / <code>0940</code> — только время, потом день\n"
    "• <code>1610 171026</code> — сразу 16:10 и дата\n"
    "• <code>16:10 17.10.2026</code> — тоже ок"
)


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
