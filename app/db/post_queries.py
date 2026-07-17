"""Быстрые raw-SQL чтения постов для воркеров (без ORM в asyncio)."""
import json
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def _normalize_json_fields(data: dict, fields: tuple[str, ...] = ("photos", "videos")) -> dict:
    for field in fields:
        value = data.get(field)
        if isinstance(value, str):
            try:
                data[field] = json.loads(value)
            except Exception:
                data[field] = []
        elif value is None:
            data[field] = []
    return data


def fetch_post_row(db: Session, post_id: str) -> Optional[dict]:
    row = (
        db.execute(text("SELECT * FROM posts WHERE id = :id LIMIT 1"), {"id": post_id})
        .mappings()
        .first()
    )
    if not row:
        return None
    return _normalize_json_fields(dict(row))


def fetch_post(db: Session, post_id: str) -> Optional[SimpleNamespace]:
    data = fetch_post_row(db, post_id)
    return SimpleNamespace(**data) if data else None


def fetch_product_row_by_post_id(db: Session, post_id: str) -> Optional[dict]:
    """Узкий SELECT товара (SELECT * по products рвёт app↔PG в Docker)."""
    row = (
        db.execute(
            text(
                "SELECT id, post_id, vk_product_id, vk_product_link, name, price, "
                "telegram_link, status, collection_name, category_name "
                "FROM products WHERE post_id = :post_id LIMIT 1"
            ),
            {"post_id": post_id},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def insert_publication_log(
    db: Session,
    post_id: str,
    platform: str,
    status: str,
    message: str,
) -> None:
    db.execute(
        text(
            "INSERT INTO publication_logs (post_id, platform, status, message, timestamp) "
            "VALUES (:post_id, :platform, :status, :message, NOW())"
        ),
        {
            "post_id": post_id,
            "platform": platform,
            "status": status,
            "message": (message or "")[:2000],
        },
    )


def mark_post_published_vk(
    db: Session,
    post_id: str,
    *,
    vk_post_id: Optional[str],
    vk_post_link: Optional[str],
) -> None:
    """Узкий UPDATE флагов VK wall (без ORM)."""
    db.execute(
        text(
            "UPDATE posts SET is_published_vk = true, published_vk_at = NOW(), "
            "vk_post_id = COALESCE(:vk_post_id, vk_post_id), "
            "vk_post_link = COALESCE(:vk_post_link, vk_post_link), "
            "updated_at = NOW() WHERE id = :id"
        ),
        {
            "id": post_id,
            "vk_post_id": vk_post_id,
            "vk_post_link": vk_post_link,
        },
    )


def mark_post_published_instagram(
    db: Session,
    post_id: str,
    *,
    media_id: str,
    link: Optional[str],
) -> None:
    """Узкий UPDATE флагов Instagram (без ORM)."""
    db.execute(
        text(
            "UPDATE posts SET is_published_instagram = true, published_instagram_at = NOW(), "
            "instagram_media_id = :media_id, "
            "instagram_link = COALESCE(:link, instagram_link), updated_at = NOW() "
            "WHERE id = :id"
        ),
        {
            "id": post_id,
            "media_id": media_id,
            "link": link,
        },
    )


def sync_instagram_fields_to_products(
    db: Session,
    post_id: str,
    *,
    media_id: Optional[str],
    link: Optional[str],
) -> None:
    db.execute(
        text(
            "UPDATE products SET instagram_link = COALESCE(:link, instagram_link), "
            "instagram_media_id = COALESCE(:media_id, instagram_media_id) "
            "WHERE post_id = :post_id"
        ),
        {
            "post_id": post_id,
            "link": link,
            "media_id": media_id,
        },
    )


def update_product_vk_market_links(
    db: Session,
    post_id: str,
    *,
    vk_product_id: int,
    vk_product_link: str,
) -> None:
    """Проставляет VK Market ссылки на уже существующий product row."""
    db.execute(
        text(
            "UPDATE products SET vk_product_id = :vk_product_id, "
            "vk_product_link = :vk_product_link, updated_at = NOW() "
            "WHERE post_id = :post_id"
        ),
        {
            "post_id": post_id,
            "vk_product_id": vk_product_id,
            "vk_product_link": vk_product_link,
        },
    )
