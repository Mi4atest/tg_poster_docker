"""CRUD для вечерних отчётов (один отчёт на календарный день)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

from app.api.models.evening_report import EveningReportRecord
from app.db.database import SessionLocal


def _record_to_draft(record: EveningReportRecord) -> dict[str, Any]:
    return {
        "report_id": record.id,
        "report_date": record.report_date.isoformat(),
        "notes_text": record.notes_text,
        "morning_cash": record.morning_cash,
        "day_cash": record.day_cash,
        "bn": record.bn,
        "new_advance": record.new_advance,
        "old_advance": record.old_advance,
        "surrendered": record.surrendered,
        "buybacks": record.buybacks,
        "wholesale": record.wholesale,
        "credit": record.credit,
        "nf_primary": record.nf_primary,
        "nf_secondary": record.nf_secondary,
        "extra_items": list(record.extra_items or []),
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


def get_report_by_date(report_date: date) -> Optional[EveningReportRecord]:
    db = SessionLocal()
    try:
        return (
            db.query(EveningReportRecord)
            .filter(EveningReportRecord.report_date == report_date)
            .first()
        )
    finally:
        db.close()


def get_yesterday_final_cash(for_date: date) -> Optional[float]:
    yesterday = for_date - timedelta(days=1)
    record = get_report_by_date(yesterday)
    if record and record.final_cash is not None:
        return float(record.final_cash)
    return None


def load_or_create_draft(for_date: date) -> dict[str, Any]:
    record = get_report_by_date(for_date)
    if record:
        return _record_to_draft(record)

    draft = empty_draft(for_date)
    yesterday_cash = get_yesterday_final_cash(for_date)
    if yesterday_cash is not None:
        draft["morning_cash"] = yesterday_cash
    return draft


def save_report(
    draft: dict[str, Any],
    *,
    report_text: str,
    final_cash: float,
) -> EveningReportRecord:
    report_date = date.fromisoformat(draft["report_date"])
    db = SessionLocal()
    try:
        record = (
            db.query(EveningReportRecord)
            .filter(EveningReportRecord.report_date == report_date)
            .first()
        )
        if not record:
            record = EveningReportRecord(report_date=report_date)
            db.add(record)

        record.notes_text = draft.get("notes_text")
        record.morning_cash = draft.get("morning_cash")
        record.day_cash = draft.get("day_cash")
        record.bn = draft.get("bn")
        record.new_advance = draft.get("new_advance")
        record.old_advance = draft.get("old_advance")
        record.surrendered = draft.get("surrendered")
        record.buybacks = draft.get("buybacks")
        record.wholesale = draft.get("wholesale")
        record.credit = draft.get("credit")
        record.nf_primary = draft.get("nf_primary")
        record.nf_secondary = draft.get("nf_secondary")
        record.extra_items = list(draft.get("extra_items") or [])
        record.final_cash = final_cash
        record.report_text = report_text
        record.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(record)
        return record
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
