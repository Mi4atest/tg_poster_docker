"""Публикация Instagram Stories через официальный Graph API.

Кадр тот же, что для VK (1080x1920 JPEG). Нативный бабл «Публикация»
(шаринг поста из приложения) Graph API не поддерживает — стикеры нельзя.
Сторис публикуется как тизер: пост в ленте IG должен уже существовать.

Источники image_url (по порядку, с ретраями):
1) локальный кадр через INSTAGRAM_GRAPH_MEDIA_BASE_URL / AVITO public host
2) публичный JPEG уже опубликованной VK-сторис (userapi CDN)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

from app.api.models.post import Post
from app.api.models.story import Story, StoryPublicationLog
from app.config.settings import MEDIA_DIR
from app.db.database import SessionLocal
from app.utils.text_extractor import extract_model_and_price
from app.workers.instagram.graph_client import InstagramGraphClient
from app.workers.instagram.graph_publisher import InstagramGraphPublisher
from app.workers.vk.story_publisher import (
    VKStoryPublisher,
    resolve_vk_story_photo_url_for_post,
)

logger = logging.getLogger(__name__)

_LAST_ERROR = ""
StoryUrlCandidate = Tuple[str, str]  # (source, url)


def last_instagram_story_error() -> str:
    return _LAST_ERROR


def _set_last_error(message: str) -> None:
    global _LAST_ERROR
    _LAST_ERROR = (message or "").strip()


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def ig_story_block_reason(post: Any) -> Optional[str]:
    """Почему нельзя публиковать IG-сторис. None — можно."""
    if not post:
        return "Пост не найден"
    if not _attr(post, "is_published_instagram"):
        return "Сначала опубликуйте пост в ленту Instagram"
    photos = _attr(post, "photos") or []
    if not photos:
        return "Нет фотографий для сторис"
    return None


def _redact_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{(parsed.path or '')[:80]}"
    except Exception:
        return "<bad-url>"


class InstagramStoryPublisher:
    """Собирает кадр и публикует его как Graph Story (local media → VK CDN fallback)."""

    def __init__(self) -> None:
        self.graph = InstagramGraphPublisher()
        self.publish_retries = max(
            1, int(os.getenv("INSTAGRAM_STORY_PUBLISH_RETRIES", "3") or 3)
        )
        self.publish_retry_delay_seconds = max(
            1, int(os.getenv("INSTAGRAM_STORY_RETRY_DELAY_SECONDS", "2") or 2)
        )

    @staticmethod
    def preview_dir() -> Path:
        path = MEDIA_DIR / "story_previews"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def preview_path_for_post(cls, post_id: str, *, stamp: Optional[str] = None) -> Path:
        suffix = stamp or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return cls.preview_dir() / f"{post_id}_ig_{suffix}.jpg"

    @classmethod
    def _cleanup_old_previews(cls, post_id: str, keep: Path) -> None:
        keep_resolved = keep.resolve()
        for path in cls.preview_dir().glob(f"{post_id}_ig_*.jpg"):
            try:
                if path.resolve() != keep_resolved:
                    path.unlink()
            except OSError:
                continue

    def _local_public_url(self, file_path: Path) -> Tuple[Optional[str], Optional[str]]:
        # Подтянуть актуальный base (env / Avito fallback), не кэш из __init__.
        self.graph.media_base_url = self.graph.token_manager.get_media_base_url()
        if not self.graph.media_base_url:
            return None, (
                "Не задан INSTAGRAM_GRAPH_MEDIA_BASE_URL — Meta не сможет скачать кадр сторис"
            )
        try:
            return self.graph.public_url_for_media_path(file_path), None
        except ValueError as exc:
            return None, f"Кадр сторис вне MEDIA_DIR: {exc}"

    async def _collect_url_candidates(
        self,
        post: Post,
        *,
        preview_path: Optional[Path],
    ) -> List[StoryUrlCandidate]:
        candidates: List[StoryUrlCandidate] = []
        seen = set()

        if preview_path and preview_path.exists():
            local_url, _err = self._local_public_url(preview_path)
            if local_url and local_url not in seen:
                candidates.append(("local_media", local_url))
                seen.add(local_url)

        vk_url = await resolve_vk_story_photo_url_for_post(post.id)
        if vk_url and vk_url not in seen:
            candidates.append(("vk_story_cdn", vk_url))
            seen.add(vk_url)

        return candidates

    async def _publish_with_fallbacks(
        self,
        post: Post,
        candidates: List[StoryUrlCandidate],
    ) -> Optional[str]:
        if not candidates:
            return None

        last_error = ""
        for source, image_url in candidates:
            for attempt in range(1, self.publish_retries + 1):
                logger.info(
                    "IG story try post_id=%s source=%s attempt=%s/%s url=%s",
                    post.id,
                    source,
                    attempt,
                    self.publish_retries,
                    _redact_url(image_url),
                )
                media_id = await self.graph.publish_story_image(image_url)
                if media_id:
                    logger.info(
                        "IG story published post_id=%s source=%s media_id=%s",
                        post.id,
                        source,
                        media_id,
                    )
                    return media_id

                last_error = self.graph.last_error or "Graph API не опубликовал сторис"
                logger.warning(
                    "IG story fail post_id=%s source=%s attempt=%s err=%s code=%s sub=%s",
                    post.id,
                    source,
                    attempt,
                    last_error,
                    self.graph._last_graph_error_code,
                    self.graph._last_graph_error_subcode,
                )
                if self.graph._is_uri_reject_error():
                    # Meta не берёт этот хост — сразу следующий источник.
                    break
                if attempt < self.publish_retries:
                    await asyncio.sleep(self.publish_retry_delay_seconds * attempt)

        _set_last_error(last_error or "Не удалось опубликовать IG сторис ни с одного источника")
        return None

    async def publish_story(self, story_id: str) -> bool:
        _set_last_error("")
        post = None
        instagram_link = None
        db = SessionLocal()
        try:
            story = db.query(Story).filter(Story.id == story_id).first()
            if not story:
                _set_last_error(f"История {story_id} не найдена")
                logger.error(_LAST_ERROR)
                return False
            if not story.post_id:
                _set_last_error("У сторис нет post_id")
                self._log(db, story.id, "error", _LAST_ERROR)
                db.commit()
                return False

            post = db.query(Post).filter(Post.id == story.post_id).first()
            blocked = ig_story_block_reason(post)
            if blocked:
                _set_last_error(blocked)
                self._log(db, story.id, "error", blocked)
                db.commit()
                return False

            if not self.graph.enabled:
                reason = (
                    "Graph API не настроен. Нужны INSTAGRAM_GRAPH_ACCESS_TOKEN и INSTAGRAM_GRAPH_USER_ID"
                )
                _set_last_error(reason)
                self._log(db, story.id, "error", reason)
                db.commit()
                return False

            for name in (
                "id",
                "text",
                "photos",
                "videos",
                "instagram_link",
                "vk_post_id",
                "vk_post_link",
                "name",
            ):
                getattr(post, name, None)
            instagram_link = (post.instagram_link or "").strip() or None
            db.expunge(post)
            db.rollback()
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _set_last_error(reason)
            logger.error("IG story publish error story_id=%s: %s", story_id, reason, exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
            self._persist_story_log(story_id, "error", reason)
            return False
        finally:
            try:
                db.close()
            except Exception:
                pass

        try:
            preview_path: Optional[Path] = None
            vk_publisher = VKStoryPublisher()
            image_bytes = await vk_publisher.compose_frame_for_post(post)
            if image_bytes:
                preview_path = self.preview_path_for_post(post.id)
                preview_path.write_bytes(image_bytes)
                self._cleanup_old_previews(post.id, preview_path)
            else:
                logger.warning(
                    "IG story compose failed for post %s — will try VK CDN fallback only",
                    post.id,
                )

            candidates = await self._collect_url_candidates(post, preview_path=preview_path)
            if not candidates:
                reason = (
                    "Нет публичного URL кадра: задайте INSTAGRAM_GRAPH_MEDIA_BASE_URL "
                    "или сначала опубликуйте живую сторис ВК (для CDN fallback)"
                )
                _set_last_error(reason)
                self._persist_story_log(story_id, "error", reason)
                return False

            media_id = await self._publish_with_fallbacks(post, candidates)
            if not media_id:
                reason = last_instagram_story_error() or "Graph API не опубликовал сторис"
                self._persist_story_log(story_id, "error", reason)
                return False

            permalink = None
            try:
                graph_client = InstagramGraphClient()
                permalink, _shortcode = await graph_client.fetch_media_permalink(media_id)
            except Exception as exc:
                logger.warning("IG story permalink fetch failed media_id=%s: %s", media_id, exc)

            extra = (
                f"Published IG story media_id={media_id}"
                + (f" permalink={permalink}" if permalink else "")
                + (f" feed={instagram_link}" if instagram_link else "")
                + f" sources={[s for s, _ in candidates]}"
            )
            ok = self._mark_story_published(
                story_id,
                permalink or instagram_link,
                extra,
            )
            if ok:
                logger.info("IG story %s published media_id=%s", story_id, media_id)
            else:
                _set_last_error("Сторис ушла в Instagram, но не записалась в БД")
            return ok
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            _set_last_error(reason)
            logger.error("IG story publish error story_id=%s: %s", story_id, reason, exc_info=True)
            self._persist_story_log(story_id, "error", reason)
            return False

    def _persist_story_log(self, story_id: str, status: str, message: str) -> None:
        db = SessionLocal()
        try:
            self._log(db, story_id, status, message)
            db.commit()
        except Exception:
            logger.exception("Failed to persist IG story log story_id=%s", story_id)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()

    def _mark_story_published(
        self, story_id: str, post_link: Optional[str], extra: str
    ) -> bool:
        """Свежая сессия: Graph API длится дольше idle_in_transaction_session_timeout."""
        db = SessionLocal()
        try:
            story = db.query(Story).filter(Story.id == story_id).first()
            if not story:
                logger.error("IG story %s disappeared before DB update", story_id)
                return False
            story.is_published = True
            story.published_at = datetime.now(timezone.utc)
            if post_link:
                story.post_link = post_link
            self._log(db, story.id, "success", extra)
            db.commit()
            return True
        except Exception:
            logger.exception("Failed to mark IG story published story_id=%s", story_id)
            try:
                db.rollback()
            except Exception:
                pass
            return False
        finally:
            db.close()

    @staticmethod
    def _log(db, story_id: str, status: str, message: str) -> None:
        db.add(
            StoryPublicationLog(
                story_id=story_id,
                status=status,
                message=message,
            )
        )


async def publish_story_to_instagram(story_id: str) -> bool:
    publisher = InstagramStoryPublisher()
    return await publisher.publish_story(story_id)


async def ensure_instagram_story_record(post_id: str) -> Optional[str]:
    """Создать или вернуть Story(platform=instagram) для поста."""
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            return None
        existing = (
            db.query(Story)
            .filter(Story.post_id == post_id, Story.platform == "instagram")
            .first()
        )
        if existing:
            return existing.id
        model_name, price = extract_model_and_price(post.text or "")
        media_file_id = (post.photos or [None])[0] if post.photos else None
        story = Story(
            post_id=post_id,
            platform="instagram",
            model_name=model_name,
            price=price,
            media_file_id=media_file_id,
        )
        db.add(story)
        db.commit()
        db.refresh(story)
        return story.id
    finally:
        db.close()


async def maybe_auto_publish_instagram_story(post_id: str) -> bool:
    """
    Если включён тумблер «Сторис (авто)» — опубликовать сторис IG после ленты.
    Ошибки не пробрасываются (пост в ленте уже ушёл).
    """
    try:
        from app.services.settings_service import get_settings_service

        if not get_settings_service().is_stories_auto_enabled():
            return False
        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.id == post_id).first()
            blocked = ig_story_block_reason(post)
            if blocked:
                logger.info("Auto IG story skipped for %s: %s", post_id, blocked)
                return False
        finally:
            db.close()

        story_id = await ensure_instagram_story_record(post_id)
        if not story_id:
            return False
        ok = await publish_story_to_instagram(story_id)
        if ok:
            logger.info("Auto IG story published for post %s (story=%s)", post_id, story_id)
        else:
            logger.error(
                "Auto IG story failed for post %s (story=%s): %s",
                post_id,
                story_id,
                last_instagram_story_error(),
            )
        return bool(ok)
    except Exception as exc:
        logger.error("Auto IG story error for post %s: %s", post_id, exc)
        return False
