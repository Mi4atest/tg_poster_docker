"""Запросы для экрана «Застой по цене» (б/у)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.stale_price_utils import STALE_SORT_PRICE, STALE_SORT_SALE
from app.db.product_queries import USED_EXCLUDED_COLLECTIONS

StaleSortMode = Literal["price", "sale"]

_STALE_USED_WHERE = """
    p.status = 'active'
    AND (p.collection_name IS NULL OR p.collection_name NOT IN (:c1, :c2, :c3, :c4, :c5))
"""

_STALE_PARAMS = {
    "c1": USED_EXCLUDED_COLLECTIONS[0],
    "c2": USED_EXCLUDED_COLLECTIONS[1],
    "c3": USED_EXCLUDED_COLLECTIONS[2],
    "c4": USED_EXCLUDED_COLLECTIONS[3],
    "c5": USED_EXCLUDED_COLLECTIONS[4],
}

_STALE_SELECT = """
    SELECT p.id, p.name, p.price, p.collection_name, p.price_changed_at, p.created_at,
           po.published_telegram_at,
           EXISTS (
               SELECT 1 FROM product_price_history h
               WHERE h.product_id = p.id AND h.source != 'publication'
           ) AS price_repriced
    FROM products p
    LEFT JOIN posts po ON po.id = p.post_id
    WHERE {where_clause}
"""

_SORT_ORDERS: dict[str, str] = {
    STALE_SORT_PRICE: "COALESCE(p.price_changed_at, p.created_at) ASC, p.id ASC",
    STALE_SORT_SALE: "COALESCE(po.published_telegram_at, p.created_at) ASC, p.id ASC",
}


def _row_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif k == "price_repriced" and v is not None:
            d[k] = bool(v)
    return d


def fetch_stale_used_products(
    db: Session,
    *,
    sort_mode: StaleSortMode = STALE_SORT_PRICE,
) -> list[dict[str, Any]]:
    """Активные б/у-товары для экрана застоя."""
    order = _SORT_ORDERS.get(sort_mode, _SORT_ORDERS[STALE_SORT_PRICE])
    rows = (
        db.execute(
            text(
                _STALE_SELECT.format(where_clause=_STALE_USED_WHERE)
                + f" ORDER BY {order}"
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
                SELECT COUNT(*) FROM products p
                WHERE p.status = 'active'
                  AND (p.collection_name IS NULL OR p.collection_name NOT IN (:c1, :c2, :c3, :c4, :c5))
                  AND COALESCE(p.price_changed_at, p.created_at)
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
                _STALE_SELECT.format(where_clause=_STALE_USED_WHERE + " AND p.id = :id")
            ),
            {**_STALE_PARAMS, "id": product_id},
        )
        .mappings()
        .first()
    )
    return _row_to_dict(row) if row else None
