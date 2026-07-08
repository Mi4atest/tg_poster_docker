"""Archive queries shared by API and Telegram bot."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import cast, Date, func
from sqlalchemy.orm import Session

from app.api.models.post import Post
from app.db.database import SessionLocal


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def query_archive_summary(db: Session) -> List[Dict[str, int]]:
    """Counts per UTC day without loading post bodies."""
    rows = (
        db.query(
            cast(Post.created_at, Date).label("d"),
            func.count(Post.id).label("cnt"),
        )
        .group_by("d")
        .all()
    )
    buckets: List[Dict[str, int]] = []
    for row in rows:
        day_value = row.d
        if day_value is None:
            continue
        if not hasattr(day_value, "year"):
            day_value = datetime.fromisoformat(str(day_value)).date()
        buckets.append(
            {
                "year": int(day_value.year),
                "month": int(day_value.month),
                "day": int(day_value.day),
                "count": int(row.cnt),
            }
        )
    return buckets


def query_archive_day(db: Session, year: int, month: int, day: int) -> List[Dict[str, Any]]:
    """Minimal post list for one UTC day (index-friendly range filter)."""
    start = datetime(year, month, day)
    end = start + timedelta(days=1)
    rows = (
        db.query(
            Post.id,
            Post.name,
            Post.created_at,
            Post.photos,
            Post.videos,
            Post.published_vk_at,
            Post.published_telegram_at,
            Post.published_instagram_at,
            Post.published_max_at,
        )
        .filter(Post.created_at >= start, Post.created_at < end)
        .order_by(Post.created_at.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "name": row.name,
            "created_at": _iso(row.created_at),
            "photos": row.photos or [],
            "videos": row.videos or [],
            "published_vk_at": _iso(row.published_vk_at),
            "published_telegram_at": _iso(row.published_telegram_at),
            "published_instagram_at": _iso(row.published_instagram_at),
            "published_max_at": _iso(row.published_max_at),
        }
        for row in rows
    ]


def fetch_archive_summary() -> List[Dict[str, int]]:
    db = SessionLocal()
    try:
        return query_archive_summary(db)
    finally:
        db.close()


def fetch_archive_day(year: int, month: int, day: int) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        return query_archive_day(db, year, month, day)
    finally:
        db.close()
