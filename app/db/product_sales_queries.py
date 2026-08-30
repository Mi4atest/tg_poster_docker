"""Журнал продаж новых товаров (не архив каталога)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def insert_product_sale(
    db: Session,
    *,
    product_id: int,
    name: str,
    collection_name: Optional[str],
    price: Optional[str],
    sold_at: Optional[datetime] = None,
) -> dict[str, Any]:
    when = sold_at or datetime.utcnow()
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    row = db.execute(
        text(
            """
            INSERT INTO product_sales (product_id, name, collection_name, price, sold_at)
            VALUES (:product_id, :name, :collection_name, :price, :sold_at)
            RETURNING id, product_id, name, collection_name, price, sold_at
            """
        ),
        {
            "product_id": product_id,
            "name": (name or "").strip() or "Без названия",
            "collection_name": collection_name,
            "price": price,
            "sold_at": when,
        },
    ).mappings().first()
    return dict(row) if row else {}


def fetch_product_sales_in_range(
    db: Session,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict[str, Any]]:
    rows = (
        db.execute(
            text(
                """
                SELECT id, product_id, name, collection_name, price, sold_at
                FROM product_sales
                WHERE sold_at >= :start_utc AND sold_at < :end_utc
                ORDER BY sold_at ASC, id ASC
                """
            ),
            {"start_utc": start_utc, "end_utc": end_utc},
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
