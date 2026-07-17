"""CRUD для вечерних отчётов (один отчёт на календарный день)."""
from __future__ import annotations

import calendar
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text

from app.db.database import SessionLocal

logger = logging.getLogger(__name__)

ARCHIVE_DB_TIMEOUT_SEC = 5.0

_DRAFT_SELECT = """
SELECT id, report_date, notes_text, morning_cash, day_cash, bn, new_advance,
       old_advance, surrendered, buybacks, wholesale, credit, nf_primary,
       nf_secondary, extra_items, final_cash, report_text
FROM evening_reports
WHERE report_date = :report_date
LIMIT 1
"""

_REPORT_TEXT_SELECT = """
SELECT report_text
FROM evening_reports
WHERE report_date = :report_date
LIMIT 1
"""

_FINAL_CASH_SELECT = """
SELECT final_cash
FROM evening_reports
WHERE report_date = :report_date
LIMIT 1
"""


def _row_to_draft(row: dict[str, Any]) -> dict[str, Any]:
    report_date = row["report_date"]
    if hasattr(report_date, "isoformat"):
        report_date = report_date.isoformat()
    extra = row.get("extra_items")
    if isinstance(extra, str):
        extra = json.loads(extra)
    return {
        "report_id": row["id"],
        "report_date": report_date,
        "notes_text": row.get("notes_text"),
        "morning_cash": row.get("morning_cash"),
        "day_cash": row.get("day_cash"),
        "bn": row.get("bn"),
        "new_advance": row.get("new_advance"),
        "old_advance": row.get("old_advance"),
        "surrendered": row.get("surrendered"),
        "buybacks": row.get("buybacks"),
        "wholesale": row.get("wholesale"),
        "credit": row.get("credit"),
        "nf_primary": row.get("nf_primary"),
        "nf_secondary": row.get("nf_secondary"),
        "extra_items": list(extra or []),
    }


def empty_draft(report_date: date) -> dict[str, Any]:
    return {
        "report_id": None,
        "report_date": report_date.isoformat(),
        "notes_text": None,
        "morning_cash": None,
        "day_cash": None,
        "bn": None,
        "new_advance": None,
        "old_advance": None,
        "surrendered": None,
        "buybacks": None,
        "wholesale": None,
        "credit": None,
        "nf_primary": None,
        "nf_secondary": None,
        "extra_items": [],
    }


def _fetch_draft_row(for_date: date) -> Optional[dict[str, Any]]:
    """Чтение черновика raw SQL (ORM .first() зависает на некоторых записях)."""
    db = SessionLocal()
    try:
        row = db.execute(
            text(_DRAFT_SELECT),
            {"report_date": for_date},
        ).mappings().first()
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("evening_report _fetch_draft_row failed for %s: %s", for_date, exc)
        raise
    finally:
        db.close()


def _query_report_dates_between(start: date, end: date) -> list[date]:
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT report_date FROM evening_reports "
                "WHERE report_date >= :start AND report_date <= :end"
            ),
            {"start": start, "end": end},
        ).all()
        return [row[0] for row in rows]
    finally:
        db.close()


def get_saved_report_years() -> set[int]:
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT report_date FROM evening_reports")).all()
        return {row[0].year for row in rows}
    except Exception as exc:
        logger.warning("evening_report get_saved_report_years failed: %s", exc)
        return set()
    finally:
        db.close()


def get_saved_report_months_for_year(year: int) -> set[int]:
    try:
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        dates = _query_report_dates_between(start, end)
        return {d.month for d in dates}
    except Exception as exc:
        logger.warning("evening_report get_saved_report_months_for_year failed: %s", exc)
        return set()


def get_saved_report_days_for_month(year: int, month: int) -> set[int]:
    try:
        last_day = calendar.monthrange(year, month)[1]
        start = date(year, month, 1)
        end = date(year, month, last_day)
        dates = _query_report_dates_between(start, end)
        return {d.day for d in dates}
    except Exception as exc:
        logger.warning("evening_report get_saved_report_days_for_month failed: %s", exc)
        return set()


def _load_yesterday_final_cash(for_date: date) -> Optional[float]:
    yesterday = for_date - timedelta(days=1)
    db = SessionLocal()
    try:
        return db.execute(
            text(_FINAL_CASH_SELECT),
            {"report_date": yesterday},
        ).scalar()
    except Exception as exc:
        logger.warning(
            "evening_report yesterday final_cash skipped for %s: %s",
            yesterday,
            exc,
        )
        return None
    finally:
        db.close()


def load_or_create_draft(for_date: date) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        row = _fetch_draft_row(for_date)
    except Exception:
        row = None
    if row:
        draft = _row_to_draft(row)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info("evening_report loaded draft in %sms", elapsed_ms)
        return draft

    draft = empty_draft(for_date)
    y_cash = _load_yesterday_final_cash(for_date)
    if y_cash is not None:
        draft["morning_cash"] = float(y_cash)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info("evening_report empty draft ready in %sms (y_cash=%s)", elapsed_ms, y_cash)
    return draft


def get_report_text_by_date(report_date: date) -> Optional[str]:
    db = SessionLocal()
    try:
        value = db.execute(
            text(_REPORT_TEXT_SELECT),
            {"report_date": report_date},
        ).scalar()
    except Exception as exc:
        logger.warning("evening_report get_report_text_by_date failed for %s: %s", report_date, exc)
        return None
    finally:
        db.close()

    if value:
        text_value = value.strip()
        return text_value or None
    return None


# psycopg2 (вне пула SQLAlchemy): autocommit — нет окна idle in transaction между execute и commit.
_UPSERT_PG = """
INSERT INTO evening_reports (
    report_date, notes_text, morning_cash, day_cash, bn,
    new_advance, old_advance, surrendered, buybacks, wholesale,
    credit, nf_primary, nf_secondary, extra_items, final_cash,
    report_text, created_at, updated_at
) VALUES (
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s::jsonb, %s,
    %s, %s, %s
)
ON CONFLICT (report_date) DO UPDATE SET
    notes_text = EXCLUDED.notes_text,
    morning_cash = EXCLUDED.morning_cash,
    day_cash = EXCLUDED.day_cash,
    bn = EXCLUDED.bn,
    new_advance = EXCLUDED.new_advance,
    old_advance = EXCLUDED.old_advance,
    surrendered = EXCLUDED.surrendered,
    buybacks = EXCLUDED.buybacks,
    wholesale = EXCLUDED.wholesale,
    credit = EXCLUDED.credit,
    nf_primary = EXCLUDED.nf_primary,
    nf_secondary = EXCLUDED.nf_secondary,
    extra_items = EXCLUDED.extra_items,
    final_cash = EXCLUDED.final_cash,
    report_text = EXCLUDED.report_text,
    updated_at = EXCLUDED.updated_at
RETURNING id
"""


def _er_pg_connect():
    """Отдельное соединение вне пула SQLAlchemy (пул бота бывает «отравлен»)."""
    import psycopg2
    from app.db.database import engine

    u = engine.url
    return psycopg2.connect(
        host=u.host,
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=u.database,
        connect_timeout=5,
        options="-c statement_timeout=10000",
    )


def save_report(
    draft: dict[str, Any],
    *,
    report_text: str,
    final_cash: float,
) -> int:
    """UPSERT через psycopg2 autocommit — обход зависаний Session/пула."""
    report_date = date.fromisoformat(draft["report_date"])
    extra_items = list(draft.get("extra_items") or [])
    now = datetime.utcnow()
    values = (
        report_date,
        draft.get("notes_text"),
        draft.get("morning_cash"),
        draft.get("day_cash"),
        draft.get("bn"),
        draft.get("new_advance"),
        draft.get("old_advance"),
        draft.get("surrendered"),
        draft.get("buybacks"),
        draft.get("wholesale"),
        draft.get("credit"),
        draft.get("nf_primary"),
        draft.get("nf_secondary"),
        json.dumps(extra_items, ensure_ascii=False),
        final_cash,
        report_text,
        now,
        now,
    )
    last_error: Optional[BaseException] = None
    for attempt in range(2):
        t0 = time.monotonic()
        conn = None
        try:
            t_conn = time.monotonic()
            conn = _er_pg_connect()
            conn.autocommit = True
            connect_ms = int((time.monotonic() - t_conn) * 1000)
            t_exec = time.monotonic()
            with conn.cursor() as cur:
                cur.execute(_UPSERT_PG, values)
                row = cur.fetchone()
            exec_ms = int((time.monotonic() - t_exec) * 1000)
            if not row:
                raise RuntimeError("UPSERT returned no id")
            row_id = int(row[0])
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info(
                "evening_report saved id=%s date=%s in %sms (connect=%s exec=%s via=psycopg2)",
                row_id,
                report_date,
                elapsed_ms,
                connect_ms,
                exec_ms,
            )
            return row_id
        except Exception as ec:
            last_error = ec
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "evening_report save attempt %s failed for %s: %s",
                attempt + 1,
                report_date,
                ec,
            )
            if attempt == 0:
                continue
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    if last_error:
        raise last_error
    raise RuntimeError("save_report failed without exception")
