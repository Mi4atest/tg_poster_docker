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
    row = (
        db.execute(
            text("SELECT * FROM products WHERE post_id = :post_id LIMIT 1"),
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
