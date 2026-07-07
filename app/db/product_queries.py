"""Быстрые raw-SQL операции с товарами (без ORM в asyncio)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

USED_EXCLUDED_COLLECTIONS = ("iPhone новые", "Airpods", "Apple Watch", "iPad", "custom")

# Широкие строки (>~26 колонок за раз) рвут app→PG в Docker — грузим частями.
_PRODUCT_DETAIL_COLUMNS_1 = """
    id, post_id, vk_product_id, vk_product_link, telegram_link,
    name, price, payment_method, final_price, category_id, category_name,
    collection_id, collection_name, status
""".strip()

_PRODUCT_DETAIL_COLUMNS_2 = """
    created_at, updated_at, archived_at, availability_status,
    channel_message_id, availability_message_ids,
    max_link, custom_button_id, max_share_url,
    avito_item_id, avito_url, instagram_link, instagram_media_id
""".strip()

_PRODUCT_SYNC_COLUMNS = """
    id, name, price, telegram_link, max_link, max_share_url,
    instagram_link, instagram_media_id, post_id, vk_product_id,
    avito_item_id, collection_name, custom_button_id
""".strip()

_PRODUCT_LIST_COLUMNS = """
    id, name, price, status, collection_name, category_name,
    vk_product_link, telegram_link, created_at, archived_at,
    availability_status, payment_method, final_price, post_id, vk_product_id
""".strip()


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


_POST_DETAIL_SELECT = """
SELECT max_share_url, vk_post_id, vk_post_link
FROM posts
WHERE id = :id
LIMIT 1
"""


def _enrich_product_detail_row(conn, row) -> Optional[dict[str, Any]]:
    """Дополняет строку товара полями поста (два коротких запроса вместо тяжёлого JOIN)."""
    if not row:
        return None
    data = dict(row)
    post_id = data.get("post_id")
    if post_id:
        post_row = (
            conn.execute(text(_POST_DETAIL_SELECT), {"id": post_id})
            .mappings()
            .first()
        )
        if post_row:
            if not data.get("max_share_url") and post_row.get("max_share_url"):
                data["max_share_url"] = post_row["max_share_url"]
            data["vk_post_id"] = post_row.get("vk_post_id")
            data["vk_post_link"] = post_row.get("vk_post_link")
    data.setdefault("vk_post_id", None)
    data.setdefault("vk_post_link", None)
    return data


def _fetch_product_row_core(conn, product_id: int) -> Optional[dict[str, Any]]:
    row1 = (
        conn.execute(
            text(f"SELECT {_PRODUCT_DETAIL_COLUMNS_1} FROM products WHERE id = :id LIMIT 1"),
            {"id": product_id},
        )
        .mappings()
        .first()
    )
    if not row1:
        return None
    row2 = (
        conn.execute(
            text(f"SELECT {_PRODUCT_DETAIL_COLUMNS_2} FROM products WHERE id = :id LIMIT 1"),
            {"id": product_id},
        )
        .mappings()
        .first()
    )
    data = {**dict(row1), **dict(row2 or {})}
    dl = conn.execute(
        text("SELECT display_label FROM products WHERE id = :id LIMIT 1"),
        {"id": product_id},
    ).scalar()
    data["display_label"] = dl
    return data


def fetch_product_detail_row(db: Session, product_id: int) -> Optional[dict[str, Any]]:
    """Товар + поля поста для карточки (без ORM, без SELECT *)."""
    row = _fetch_product_row_core(db.connection(), product_id)
    return _enrich_product_detail_row(db.connection(), row)


def fetch_product_detail_row_by_id(product_id: int) -> Optional[dict[str, Any]]:
    """Товар для карточки через engine.connect (лёгкие запросы, без SELECT *)."""
    from app.db.database import engine

    with engine.connect() as conn:
        row = _fetch_product_row_core(conn, product_id)
        return _enrich_product_detail_row(conn, row)


def product_detail_row_to_api_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Словарь для бота/API (JSON-serializable)."""
    from app.api.schemas.product import Product as ProductSchema

    out = ProductSchema.model_validate(row).model_dump(mode="json")
    for key, val in out.items():
        if isinstance(val, datetime):
            out[key] = val.isoformat()
    return out
