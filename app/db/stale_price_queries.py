"""Запросы для экрана «Застой по цене» (б/у)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.product_queries import USED_EXCLUDED_COLLECTIONS

_STALE_USED_WHERE = """
    status = 'active'
    AND (collection_name IS NULL OR collection_name NOT IN (:c1, :c2, :c3, :c4, :c5))
"""

_STALE_PARAMS = {
    "c1": USED_EXCLUDED_COLLECTIONS[0],
    "c2": USED_EXCLUDED_COLLECTIONS[1],
    "c3": USED_EXCLUDED_COLLECTIONS[2],
    "c4": USED_EXCLUDED_COLLECTIONS[3],
    "c5": USED_EXCLUDED_COLLECTIONS[4],
}


def _row_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def fetch_stale_used_products(db: Session) -> list[dict[str, Any]]:
    """Активные б/у-товары, сортировка от давней смены цены к недавней."""
    rows = (
        db.execute(
            text(
                f"""
                SELECT id, name, price, collection_name, price_changed_at, created_at
                FROM products
                WHERE {_STALE_USED_WHERE}
                ORDER BY COALESCE(price_changed_at, created_at) ASC, id ASC
                """
            ),
            _STALE_PARAMS,
        )
        .mappings()
        .all()
    )
    return [_row_to_dict(r) for r in rows]


def count_stale_badge(db: Session, min_days: int = 60) -> int:
    """Число активных б/у с ≥ min_days без смены цены."""
    return (
        db.execute(
            text(
                f"""
                SELECT COUNT(*) FROM products
                WHERE {_STALE_USED_WHERE}
                  AND COALESCE(price_changed_at, created_at)
                      <= NOW() - (:min_days * INTERVAL '1 day')
                """
            ),
            {**_STALE_PARAMS, "min_days": min_days},
        ).scalar()
        or 0
    )


def fetch_price_history(
    db: Session,
    product_id: int,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """История цен товара (новые сверху в UI — сортируем DESC)."""
    rows = (
        db.execute(
            text(
                """
                SELECT id, product_id, old_price, new_price, changed_at, source
                FROM product_price_history
                WHERE product_id = :pid
                ORDER BY changed_at DESC, id DESC
                LIMIT :lim
                """
            ),
            {"pid": product_id, "lim": limit},
        )
        .mappings()
        .all()
    )
    return [_row_to_dict(r) for r in rows]


def fetch_stale_used_products_by_id(db: Session, product_id: int) -> Optional[dict[str, Any]]:
    """Один активный б/у-товар для экрана истории."""
    row = (
        db.execute(
            text(
                f"""
                SELECT id, name, price, collection_name, price_changed_at, created_at
                FROM products
                WHERE id = :id AND {_STALE_USED_WHERE}
                """
            ),
            {**_STALE_PARAMS, "id": product_id},
        )
        .mappings()
        .first()
    )
    return _row_to_dict(row) if row else None
