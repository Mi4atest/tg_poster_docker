"""CRUD напоминалок главного экрана (raw SQL)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from app.db.database import SessionLocal

logger = logging.getLogger(__name__)

MAX_ACTIVE_NOTES = 5
NOTE_BODY_MAX = 200

CATEGORY_STATIONERY = "stationery"
CATEGORY_ASSORTMENT = "assortment"
CATEGORY_SERVICE = "service"
VALID_CATEGORIES = frozenset(
    {CATEGORY_STATIONERY, CATEGORY_ASSORTMENT, CATEGORY_SERVICE}
)

CATEGORY_EMOJI = {
    CATEGORY_STATIONERY: "📎",
    CATEGORY_ASSORTMENT: "📦",
    CATEGORY_SERVICE: "🔧",
}


class NoteLimitError(Exception):
    """Уже MAX_ACTIVE_NOTES активных заметок."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row_to_note(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key in ("created_at", "done_at"):
        val = d.get(key)
        if isinstance(val, datetime):
            d[key] = val.isoformat()
    return d


def list_active_notes() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = (
            db.execute(
                text(
                    "SELECT id, body, category, is_done, created_at, done_at "
                    "FROM shop_notes WHERE is_done = FALSE "
                    "ORDER BY id ASC"
                )
            )
            .mappings()
            .all()
        )
    return [_row_to_note(r) for r in rows]


def count_active_notes() -> int:
    with SessionLocal() as db:
        n = db.execute(
            text("SELECT COUNT(*) FROM shop_notes WHERE is_done = FALSE")
        ).scalar()
    return int(n or 0)


def create_note(body: str, category: Optional[str] = None) -> dict[str, Any]:
    text_body = " ".join((body or "").split()).strip()
    if not text_body:
        raise ValueError("empty note")
    text_body = text_body[:NOTE_BODY_MAX]
    cat = (category or "").strip() or None
    if cat not in VALID_CATEGORIES:
        cat = None
    with SessionLocal() as db:
        n = db.execute(
            text("SELECT COUNT(*) FROM shop_notes WHERE is_done = FALSE")
        ).scalar() or 0
        if int(n) >= MAX_ACTIVE_NOTES:
            raise NoteLimitError()
        row = (
            db.execute(
                text(
                    "INSERT INTO shop_notes (body, category, is_done, created_at) "
                    "VALUES (:body, :category, FALSE, :created_at) "
                    "RETURNING id, body, category, is_done, created_at, done_at"
                ),
                {"body": text_body, "category": cat, "created_at": _now()},
            )
            .mappings()
            .one()
        )
        db.commit()
        return _row_to_note(row)


def mark_note_done(note_id: int) -> bool:
    with SessionLocal() as db:
        n = db.execute(
            text(
                "UPDATE shop_notes SET is_done = TRUE, done_at = :done_at "
                "WHERE id = :id AND is_done = FALSE"
            ),
            {"id": int(note_id), "done_at": _now()},
        ).rowcount
        db.commit()
    return bool(n)
