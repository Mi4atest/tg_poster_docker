"""Сводка месяца: архив б/у + журнал продаж новых."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.product_queries import USED_EXCLUDED_COLLECTIONS
from app.db.product_sales_queries import fetch_product_sales_in_range
from app.utils.time_msk import msk_month_bounds_naive_utc

_NEW_COLLECTIONS = frozenset(
    (c or "").strip() for c in USED_EXCLUDED_COLLECTIONS
)


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


def combine_month_summary_rows(
    archived: Iterable[dict[str, Any]],
    sales: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Б/у-архив + продажи новых. Скрытие новой позиции из каталога в сводку не идёт."""
    used: list[dict[str, Any]] = []
    for p in archived:
        coll = (p.get("collection_name") or "").strip()
        if coll in _NEW_COLLECTIONS:
            continue
        used.append(p)
    sale_rows = [
        {
            "name": s.get("name"),
            "collection_name": s.get("collection_name"),
        }
        for s in sales
    ]
    return used + sale_rows


def load_current_month_archived() -> tuple[list[dict[str, Any]], str]:
    start_utc, end_utc, month_name = msk_month_bounds_naive_utc()
    with SessionLocal() as db:
        archived = fetch_archived_products_in_range(db, start_utc, end_utc)
        try:
            sales = fetch_product_sales_in_range(db, start_utc, end_utc)
        except Exception:
            sales = []
    return combine_month_summary_rows(archived, sales), month_name
