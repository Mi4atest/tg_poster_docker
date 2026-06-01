"""Shared helpers for collecting Telegram photos/videos (including albums)."""

from __future__ import annotations

from typing import List, Tuple

from aiogram.types import Message

PHOTO_LIMIT = 10
VIDEO_LIMIT = 5


def format_media_summary(photos: list, videos: list) -> str:
    """Short status string like '📷 Фото: 3/10 · 📹 Видео: 1'."""
    parts = []
    if photos is not None:
        parts.append(f"📷 Фото: {len(photos)}/{PHOTO_LIMIT}")
    if videos:
        parts.append(f"📹 Видео: {len(videos)}/{VIDEO_LIMIT}")
    return " · ".join(parts) if parts else "медиа пока нет"


def collect_album_media(
    messages: List[Message],
    photos: list,
    videos: list,
    *,
    photo_limit: int = PHOTO_LIMIT,
) -> Tuple[int, int, bool]:
    """Append file_ids from messages into photos/videos lists.

    Returns (photos_added, videos_added, photo_limit_hit).
    """
    photos_added = 0
    videos_added = 0
    photo_limit_hit = False
    for msg in messages:
        if msg.photo:
            file_id = msg.photo[-1].file_id
            if file_id in photos:
                continue
            if len(photos) >= photo_limit:
                photo_limit_hit = True
                continue
            photos.append(file_id)
            photos_added += 1
        elif msg.video:
            file_id = msg.video.file_id
            if file_id in videos:
                continue
            if len(videos) >= VIDEO_LIMIT:
                continue
            videos.append(file_id)
            videos_added += 1
    return photos_added, videos_added, photo_limit_hit
