"""Archive queries shared by API and Telegram bot."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, NamedTuple, Optional

from sqlalchemy import Date, cast, extract, func, or_
from sqlalchemy.orm import Session

from app.api.models.post import Post
from app.db.database import SessionLocal

POST_SEARCH_LIMIT_DEFAULT = 50
POST_SEARCH_LIMIT_MAX = 100

_SEARCH_COLUMNS = (
    Post.id,
    Post.name,
    Post.text,
    Post.created_at,
    Post.photos,
    Post.videos,
)


class SearchDateParts(NamedTuple):
    is_date_search: bool
    year: Optional[int]
    month: Optional[int]
    day: Optional[int]


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


def parse_search_date(search: str) -> SearchDateParts:
    """Detect date-like archive search patterns."""
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None

    if re.match(r"^\d{4}$", search):
        year_value = int(search)
        if 1900 <= year_value <= 2100:
            return SearchDateParts(True, year_value, None, None)
        month, year_suffix = int(search[:2]), int(search[2:])
        if month <= 12 and year_suffix < 100:
            return SearchDateParts(True, year_suffix + 2000, month, None)
        return SearchDateParts(False, None, None, None)

    match = re.match(r"^(\d{4})[.\/-]?(\d{2})[.\/-]?(\d{2})$", search)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if 1900 <= year <= 2100:
            return SearchDateParts(True, year, month, day)

    match = re.match(r"^(\d{4})[.\/-]?(\d{2})$", search)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1900 <= year <= 2100:
            return SearchDateParts(True, year, month, None)

    match = re.match(r"^(\d{2})[.\/-](\d{2})[.\/-](\d{2})$", search)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if year < 100:
            year += 2000
        return SearchDateParts(True, year, month, day)

    if re.match(r"^\d{6}$", search):
        day, month, year_suffix = int(search[:2]), int(search[2:4]), int(search[4:])
        if day <= 31 and month <= 12:
            return SearchDateParts(True, year_suffix + 2000, month, day)

    match = re.match(r"^(\d{2})[.\/-]?(\d{2})$", search)
    if match:
        month, year_suffix = int(match.group(1)), int(match.group(2))
        if year_suffix < 100 and month <= 12:
            return SearchDateParts(True, year_suffix + 2000, month, None)

    return SearchDateParts(False, None, None, None)


def _search_row_to_dict(row) -> Dict[str, Any]:
    if hasattr(row, "_mapping"):
        data = row._mapping
        created_at = data["created_at"]
        return {
            "id": data["id"],
            "name": data.get("name"),
            "text": data.get("text") or "",
            "created_at": _iso(created_at),
            "photos": data.get("photos") or [],
            "videos": data.get("videos") or [],
        }

    created_at = row.created_at
    return {
        "id": row.id,
        "name": row.name,
        "text": row.text or "",
        "created_at": _iso(created_at),
        "photos": row.photos or [],
        "videos": row.videos or [],
    }


def query_posts_search(
    db: Session,
    search: str,
    skip: int = 0,
    limit: int = POST_SEARCH_LIMIT_DEFAULT,
) -> List[Dict[str, Any]]:
    """Lightweight text/date search over posts (no logs, capped result set)."""
    search = (search or "").strip()
    if not search:
        return []

    limit = min(max(1, limit), POST_SEARCH_LIMIT_MAX)
    skip = max(0, skip)

    date_parts = parse_search_date(search)
    search_term = f"%{search}%"
    text_query = db.query(*_SEARCH_COLUMNS).filter(
        or_(Post.text.ilike(search_term), Post.name.ilike(search_term))
    )

    if date_parts.is_date_search:
        date_query = db.query(*_SEARCH_COLUMNS)
        if date_parts.year is not None:
            date_query = date_query.filter(extract("year", Post.created_at) == date_parts.year)
        if date_parts.month is not None:
            date_query = date_query.filter(extract("month", Post.created_at) == date_parts.month)
        if date_parts.day is not None:
            date_query = date_query.filter(extract("day", Post.created_at) == date_parts.day)

        combined = text_query.union(date_query).subquery()
        rows = (
            db.query(combined)
            .order_by(combined.c.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
    else:
        rows = (
            text_query.order_by(Post.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    return [_search_row_to_dict(row) for row in rows]


def fetch_posts_search(
    search: str,
    skip: int = 0,
    limit: int = POST_SEARCH_LIMIT_DEFAULT,
) -> List[Dict[str, Any]]:
    db = SessionLocal()
    try:
        return query_posts_search(db, search, skip=skip, limit=limit)
    finally:
        db.close()
