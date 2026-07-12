"""Запись истории смены цены товара."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.price_change import price_string_to_int_rub

logger = logging.getLogger(__name__)

PRICE_SOURCES = frozenset({"publication", "manual", "bulk"})


def prices_equal_rub(a: Optional[str], b: Optional[str]) -> bool:
    """Сравнение цен по числовому значению в рублях."""
    ra = price_string_to_int_rub(a) if a else None
    rb = price_string_to_int_rub(b) if b else None
    if ra is None and rb is None:
        return (a or "").strip() == (b or "").strip()
    if ra is None or rb is None:
        return False
    return ra == rb


def record_price_change(
    db: Session,
    product_id: int,
    old_price: Optional[str],
    new_price: str,
    *,
    source: str = "manual",
    changed_at: Optional[datetime] = None,
    update_product_price: bool = True,
) -> bool:
    """Записать смену цены в историю и обновить products.price / price_changed_at.

    Возвращает True, если запись создана (цена реально изменилась или publication).
    Для publication всегда пишет, если new_price непустой.
    """
    source = source if source in PRICE_SOURCES else "manual"
    new_price = (new_price or "").strip()
    if not new_price:
        return False

    if source != "publication" and prices_equal_rub(old_price, new_price):
        return False

    now = changed_at or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)

    if update_product_price:
        db.execute(
            text(
                "UPDATE products SET price = :price, price_changed_at = :changed_at, "
                "updated_at = :changed_at WHERE id = :id"
            ),
            {"price": new_price, "changed_at": now, "id": product_id},
        )
    else:
        db.execute(
            text(
                "UPDATE products SET price_changed_at = :changed_at, updated_at = :changed_at "
                "WHERE id = :id"
            ),
            {"changed_at": now, "id": product_id},
        )

    db.execute(
        text(
            "INSERT INTO product_price_history "
            "(product_id, old_price, new_price, changed_at, source) "
            "VALUES (:pid, :old_price, :new_price, :changed_at, :source)"
        ),
        {
            "pid": product_id,
            "old_price": old_price,
            "new_price": new_price,
            "changed_at": now,
            "source": source,
        },
    )
    return True


def record_publication_price(
    db: Session,
    product_id: int,
    price: str,
    *,
    changed_at: Optional[datetime] = None,
) -> bool:
    """Первая цена при публикации товара."""
    return record_price_change(
        db,
        product_id,
        None,
        price,
        source="publication",
        changed_at=changed_at,
        update_product_price=False,
    )
