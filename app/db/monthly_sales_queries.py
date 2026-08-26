"""Товары, снятые в архив за календарный месяц (МСК)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.utils.time_msk import msk_month_bounds_naive_utc


def fetch_archived_products_in_range(
    db: Session,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    """unavailable с archived_at в [start, end). Без фильтра archive_kind."""
    rows = (
        db.execute(
            text(
                "SELECT id, name, collection_name, archived_at "
                "FROM products "
                "WHERE status = 'unavailable' "
                "AND archived_at IS NOT NULL "
                "AND archived_at >= :start_utc "
                "AND archived_at < :end_utc "
                "ORDER BY archived_at ASC, id ASC"
            ),
            {"start_utc": start_utc, "end_utc": end_utc},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def load_current_month_archived() -> tuple[list[dict[str, Any]], str]:
    start_utc, end_utc, month_name = msk_month_bounds_naive_utc()
    with SessionLocal() as db:
        products = fetch_archived_products_in_range(db, start_utc, end_utc)
    return products, month_name
