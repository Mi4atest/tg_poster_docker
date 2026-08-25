"""Сохранение медиагруппы поста на диск (persist-on-create).

Telegram file_id остаётся в БД. Байты кладутся в MEDIA_DIR/{storage_path}/
как photo_{i}.jpg / video_{i}.mp4 — Graph API забирает их по публичному HTTPS
без токена бота.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, List, Optional

from app.config.settings import MEDIA_DIR, TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60

_path_locks_guard = threading.Lock()
_path_locks: dict[str, threading.Lock] = {}


def _lock_for_storage_path(storage_path: str) -> threading.Lock:
    with _path_locks_guard:
        lock = _path_locks.get(storage_path)
        if lock is None:
            lock = threading.Lock()
            _path_locks[storage_path] = lock
        return lock


def is_telegram_bot_file_url(url: str) -> bool:
    """URL вида api.telegram.org/file/bot<TOKEN>/... — нельзя отдавать в Graph API."""
    return "/file/bot" in (url or "")


def photo_path(post_dir: Path, index: int) -> Path:
    return post_dir / f"photo_{index}.jpg"


def video_path(post_dir: Path, index: int) -> Path:
    for name in (f"video_{index}.mp4", f"video_{index}.mov"):
        candidate = post_dir / name
        if candidate.exists():
            return candidate
    return post_dir / f"video_{index}.mp4"


def resolve_bot_token() -> str:
    try:
        from app.services.settings_service import get_settings_service

        secret = (get_settings_service().get_secret("telegram_bot_token") or "").strip()
        if secret:
            return secret
    except Exception:
        pass
    return (TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()


def download_telegram_file(
    file_id: str,
    save_path: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    token: Optional[str] = None,
) -> bool:
    """Скачивает файл Telegram на диск. Токен не логируется."""
    file_id = (file_id or "").strip()
    if not file_id:
        return False
    bot_token = (token or resolve_bot_token()).strip()
    if not bot_token:
        logger.error("Нет TELEGRAM_BOT_TOKEN — не могу сохранить медиа на диск")
        return False

    ctx = ssl.create_default_context()
    info_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    try:
        with urllib.request.urlopen(info_url, timeout=timeout, context=ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        logger.error("Telegram getFile failed file_id=%s: %s", file_id[:24], exc)
        return False

    if not payload.get("ok"):
        logger.error(
            "Telegram getFile not ok file_id=%s desc=%s",
            file_id[:24],
            payload.get("description"),
        )
        return False
    remote_path = (payload.get("result") or {}).get("file_path")
    if not remote_path:
        logger.error("Telegram getFile missing file_path file_id=%s", file_id[:24])
        return False

    download_url = f"https://api.telegram.org/file/bot{bot_token}/{remote_path}"
    try:
        with urllib.request.urlopen(download_url, timeout=timeout, context=ctx) as media_resp:
            if getattr(media_resp, "status", 200) != 200:
                logger.error("Telegram download HTTP %s file_id=%s", media_resp.status, file_id[:24])
                return False
            data = media_resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.error("Telegram download failed file_id=%s: %s", file_id[:24], exc)
        return False

    if not data:
        logger.error("Telegram download empty file_id=%s", file_id[:24])
        return False

    save_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = save_path.with_name(save_path.name + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(save_path)
    return True


def persist_post_media(
    storage_path: str,
    photos: Optional[Iterable[str]] = None,
    videos: Optional[Iterable[str]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Качает недостающие фото/видео в MEDIA_DIR/{storage_path}/.

    Уже существующие ненулевые файлы не перекачиваются.
    """
    photos_list: List[str] = [str(x).strip() for x in (photos or []) if str(x).strip()]
    videos_list: List[str] = [str(x).strip() for x in (videos or []) if str(x).strip()]
    result: dict[str, Any] = {
        "photos_ok": 0,
        "videos_ok": 0,
        "photos_skipped": 0,
        "videos_skipped": 0,
        "errors": [],
    }
    if not storage_path:
        result["errors"].append("empty_storage_path")
        return result

    with _lock_for_storage_path(storage_path):
        return _persist_post_media_unlocked(
            storage_path,
            photos_list,
            videos_list,
            result,
            timeout=timeout,
        )


def _persist_post_media_unlocked(
    storage_path: str,
    photos_list: List[str],
    videos_list: List[str],
    result: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:

    post_dir = MEDIA_DIR / storage_path
    post_dir.mkdir(parents=True, exist_ok=True)
    token = resolve_bot_token()

    for index, file_id in enumerate(photos_list):
        dest = photo_path(post_dir, index)
        if dest.exists() and dest.stat().st_size > 0:
            result["photos_ok"] += 1
            result["photos_skipped"] += 1
            continue
        if download_telegram_file(file_id, dest, timeout=timeout, token=token):
            result["photos_ok"] += 1
        else:
            result["errors"].append(f"photo_{index}")

    for index, file_id in enumerate(videos_list):
        dest = video_path(post_dir, index)
        if dest.exists() and dest.stat().st_size > 0:
            result["videos_ok"] += 1
            result["videos_skipped"] += 1
            continue
        dest = post_dir / f"video_{index}.mp4"
        if download_telegram_file(file_id, dest, timeout=timeout, token=token):
            result["videos_ok"] += 1
        else:
            result["errors"].append(f"video_{index}")

    logger.info(
        "media persist path=%s photos=%s/%s videos=%s/%s errors=%s",
        storage_path,
        result["photos_ok"],
        len(photos_list),
        result["videos_ok"],
        len(videos_list),
        result["errors"] or "none",
    )
    return result


def write_post_sidecar(
    storage_path: str,
    text: str,
    photos: Optional[list] = None,
    videos: Optional[list] = None,
    persist_result: Optional[dict] = None,
) -> Path:
    """Пишет text.txt и media.json рядом с байтами медиа."""
    post_dir = MEDIA_DIR / storage_path
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "text.txt").write_text(text or "", encoding="utf-8")
    payload: dict[str, Any] = {
        "photos": list(photos or []),
        "videos": list(videos or []),
    }
    if persist_result is not None:
        payload["persisted"] = persist_result
    (post_dir / "media.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return post_dir
