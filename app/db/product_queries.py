"""Быстрые raw-SQL операции с товарами (без ORM в asyncio)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

USED_EXCLUDED_COLLECTIONS = ("iPhone новые", "Airpods", "Apple Watch", "iPad", "custom")


def sync_telegram_links_to_products(db: Session) -> tuple[int, int, int]:
    """
    Копирует telegram_link из posts в products.
    Returns: (posts_with_link_count, posts_with_products_count, updated_products_count).
    """
    posts_with_link = db.execute(
        text(
            "SELECT COUNT(*) FROM posts "
            "WHERE telegram_link IS NOT NULL AND telegram_link != ''"
        )
    ).scalar() or 0
    posts_with_products = db.execute(
        text(
            "SELECT COUNT(DISTINCT po.id) FROM posts po "
            "INNER JOIN products p ON p.post_id = po.id "
            "WHERE po.telegram_link IS NOT NULL AND po.telegram_link != ''"
        )
    ).scalar() or 0
    updated = db.execute(
        text(
            "UPDATE products p SET telegram_link = po.telegram_link, updated_at = NOW() "
            "FROM posts po "
            "WHERE p.post_id = po.id "
            "AND po.telegram_link IS NOT NULL AND po.telegram_link != '' "
            "AND (p.telegram_link IS DISTINCT FROM po.telegram_link)"
        )
    ).rowcount
    db.commit()
    return int(posts_with_link), int(posts_with_products), int(updated or 0)


def fetch_used_products_for_list(db: Session) -> list[dict[str, Any]]:
    """Активные б/у товары со ссылками и датой публикации TG из поста."""
    rows = db.execute(
        text(
            "SELECT p.id, p.name, p.price, p.telegram_link, p.vk_product_link, "
            "po.published_telegram_at "
            "FROM products p "
            "LEFT JOIN posts po ON po.id = p.post_id "
            "WHERE p.status = 'active' "
            "AND (p.collection_name IS NULL OR p.collection_name NOT IN "
            "(:c1, :c2, :c3, :c4, :c5)) "
            "ORDER BY p.id"
        ),
        {
            "c1": USED_EXCLUDED_COLLECTIONS[0],
            "c2": USED_EXCLUDED_COLLECTIONS[1],
            "c3": USED_EXCLUDED_COLLECTIONS[2],
            "c4": USED_EXCLUDED_COLLECTIONS[3],
            "c5": USED_EXCLUDED_COLLECTIONS[4],
        },
    ).mappings().all()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "price": r["price"] or "Цена не указана",
            "telegram_link": r["telegram_link"],
            "vk_product_link": r["vk_product_link"],
            "published_telegram_at": r["published_telegram_at"],
        }
        for r in rows
    ]


_PRODUCT_DETAIL_SELECT = """
SELECT
    p.id, p.post_id, p.vk_product_id, p.vk_product_link, p.telegram_link,
    p.name, p.price, p.payment_method, p.final_price, p.category_id, p.category_name,
    p.collection_id, p.collection_name, p.status, p.created_at, p.updated_at, p.archived_at,
    p.availability_status, p.channel_message_id, p.availability_message_ids,
    p.max_link, p.custom_button_id,
    COALESCE(p.max_share_url, po.max_share_url) AS max_share_url,
    p.avito_item_id, p.avito_url, p.instagram_link, p.instagram_media_id, p.display_label,
    po.vk_post_id AS vk_post_id,
    po.vk_post_link AS vk_post_link
FROM products p
LEFT JOIN posts po ON po.id = p.post_id
WHERE p.id = :id
LIMIT 1
"""


def fetch_product_detail_row(db: Session, product_id: int) -> Optional[dict[str, Any]]:
    """Товар + поля поста для карточки (один JOIN, без ORM)."""
    row = (
        db.execute(text(_PRODUCT_DETAIL_SELECT), {"id": product_id})
        .mappings()
        .first()
    )
    return _normalize_product_detail_row(row)


def fetch_product_detail_row_by_id(product_id: int) -> Optional[dict[str, Any]]:
    """Товар для карточки через engine.connect (не Session — стабильнее при exhausted pool)."""
    from app.db.database import engine

    with engine.connect() as conn:
        row = (
            conn.execute(text(_PRODUCT_DETAIL_SELECT), {"id": product_id})
            .mappings()
            .first()
        )
    return _normalize_product_detail_row(row)


def _normalize_product_detail_row(row) -> Optional[dict[str, Any]]:
    if not row:
        return None
    return dict(row)


def product_detail_row_to_api_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Словарь для бота/API (JSON-serializable)."""
    from app.api.schemas.product import Product as ProductSchema

    out = ProductSchema.model_validate(row).model_dump(mode="json")
    for key, val in out.items():
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    return out
