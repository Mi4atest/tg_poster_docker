"""Публикация историй VK от имени сообщества (коллаж в бабле + ссылка на пост)."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import aiohttp
import requests
import vk_api

from app.api.models.post import Post
from app.api.models.story import Story, StoryPublicationLog
from app.config.settings import API_HOST, API_PORT, MEDIA_DIR
from app.db.database import SessionLocal
from app.utils.text_extractor import extract_model_and_price, extract_model_name_without_code
from app.utils.vk_api_guard import after_vk_error, before_vk_call
from app.utils.vk_client import community_token, resolved_vk_group_id_int
from app.utils.vk_debug_log import vk_dbg, vk_exc_debug_meta
from app.utils.vk_urls import api_method_url, story_url, wall_post_url
from app.workers.vk.story_composer import build_story_image
from app.workers.vk.upload_retry import (
    VK_STORY_UPLOAD_ATTEMPTS,
    is_vk_flood_error,
    vk_upload_backoff_seconds,
)

logger = logging.getLogger(__name__)

_VK_STORY_REF_RE = re.compile(r"stories(-?\d+)_(\d+)", re.IGNORECASE)


def parse_vk_story_ref(link: Optional[str]) -> Optional[str]:
    """Из ссылки вида https://vk.ru/stories-123_456 → '-123_456'."""
    if not link:
        return None
    match = _VK_STORY_REF_RE.search(str(link))
    if not match:
        return None
    return f"{match.group(1)}_{match.group(2)}"


def pick_largest_photo_url(sizes: List[dict]) -> Optional[str]:
    best_url = None
    best_area = -1
    for size in sizes or []:
        url = (size.get("url") or "").strip()
        if not url:
            continue
        area = int(size.get("width") or 0) * int(size.get("height") or 0)
        if area > best_area:
            best_area = area
            best_url = url
    return best_url


def fetch_vk_story_photo_url(story_ref: str) -> Optional[str]:
    """Публичный JPEG URL кадра опубликованной сторис (stories.getById)."""
    ref = (story_ref or "").strip()
    if not ref:
        return None
    token = community_token()
    if not token:
        logger.warning("VK story photo URL: no community token")
        return None
    try:
        response = requests.get(
            api_method_url("stories.getById"),
            params={"stories": ref, "access_token": token, "v": "5.199"},
            timeout=30,
        )
        payload = response.json()
    except Exception as exc:
        logger.warning("VK stories.getById failed for %s: %s", ref, exc)
        return None
    if payload.get("error"):
        logger.warning("VK stories.getById error for %s: %s", ref, payload.get("error"))
        return None
    items = (payload.get("response") or {}).get("items") or []
    if not items:
        return None
    story_item = items[0] if isinstance(items[0], dict) else None
    if not story_item:
        return None
    if story_item.get("is_expired") or story_item.get("is_deleted"):
        logger.info("VK story %s expired/deleted", ref)
        return None
    photo = story_item.get("photo") or {}
    return pick_largest_photo_url(photo.get("sizes") or [])


async def resolve_vk_story_photo_url_for_post(post_id: str) -> Optional[str]:
    """URL кадра живой VK-сторис для поста (из Story.post_link)."""
    db = SessionLocal()
    try:
        story = (
            db.query(Story)
            .filter(
                Story.post_id == post_id,
                Story.platform == "vk",
                Story.is_published.is_(True),
            )
            .order_by(Story.published_at.desc(), Story.created_at.desc())
            .first()
        )
        if not story:
            return None
        ref = parse_vk_story_ref(story.post_link)
        if not ref:
            logger.info("VK story for post %s has no parseable post_link=%s", post_id, story.post_link)
            return None
        return await asyncio.to_thread(fetch_vk_story_photo_url, ref)
    finally:
        db.close()


class VKStoryPublisher:
    """Публикует историю сообщества: кадр 9:16 + кнопка-ссылка на пост стены."""

    def __init__(self):
        self.group_id = resolved_vk_group_id_int()
        token = community_token()
        self.vk_session = vk_api.VkApi(token=token, api_version="5.131")
        self.vk = self.vk_session.get_api()

    async def download_telegram_file(self, file_id: str) -> Optional[bytes]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://{API_HOST}:{API_PORT}/api/telegram/file/{file_id}"
                logger.info("Downloading file from: %s", url)
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    logger.error("Failed to download file %s: %s", file_id, response.status)
                    return None
        except Exception as e:
            logger.error("Error downloading file %s: %s", file_id, e)
            return None

    async def _download_photos(self, file_ids: List[str], *, limit: int = 6) -> List[bytes]:
        blobs: List[bytes] = []
        for file_id in file_ids[:limit]:
            data = await self.download_telegram_file(file_id)
            if data:
                blobs.append(data)
        return blobs

    def _fetch_brand_assets(self) -> Tuple[str, Optional[bytes]]:
        """Имя сообщества и логотип (photo_200)."""
        brand = "appleshop43"
        logo_blob = None
        try:
            info = self.vk.groups.getById(group_id=self.group_id, fields="photo_200,name,screen_name")
            item = info[0] if isinstance(info, list) and info else info
            if isinstance(item, dict):
                brand = (item.get("screen_name") or item.get("name") or brand).strip()
                photo_url = item.get("photo_200") or item.get("photo_100")
                if photo_url:
                    resp = requests.get(photo_url, timeout=30)
                    if resp.status_code == 200 and resp.content:
                        logo_blob = resp.content
        except Exception as e:
            logger.warning("Failed to fetch VK group brand assets: %s", e)
        return brand, logo_blob

    async def compose_frame_for_post(self, post: Post) -> Optional[bytes]:
        """Собрать JPEG-кадр без публикации (для превью)."""
        photo_ids = list(post.photos or [])
        if not photo_ids:
            return None
        blobs = await self._download_photos(photo_ids, limit=6)
        if not blobs:
            return None
        model_name = extract_model_name_without_code(post.text or "") if post.text else None
        _, price = extract_model_and_price(post.text or "") if post.text else (None, None)
        brand, logo_blob = await asyncio.to_thread(self._fetch_brand_assets)
        style = self._story_style()
        return build_story_image(
            blobs,
            title=model_name,
            price=price,
            brand_name=brand,
            logo_blob=logo_blob,
            style=style,
        )

    @staticmethod
    def _story_style() -> str:
        try:
            from app.services.settings_service import get_settings_service

            return get_settings_service().get_vk_stories_style()
        except Exception:
            return "bubble"

    @staticmethod
    def preview_path_for_post(post_id: str) -> str:
        preview_dir = MEDIA_DIR / "story_previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        return str(preview_dir / f"{post_id}_vk.jpg")

    @staticmethod
    def _resolve_title_price(post: Post, story: Story) -> Tuple[Optional[str], Optional[str]]:
        model_name = None
        price = None
        if post.text:
            model_name = extract_model_name_without_code(post.text)
            _, price = extract_model_and_price(post.text)
        if not model_name:
            model_name = story.model_name
        if not price:
            price = story.price
        return model_name, price

    @staticmethod
    def _post_link(post: Post) -> Optional[str]:
        if post.vk_post_link:
            return post.vk_post_link
        if not post.vk_post_id:
            return None
        try:
            owner_id, post_id = post.vk_post_id.split("_", 1)
            return wall_post_url(int(owner_id), int(post_id))
        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _extract_upload_result(upload_data: dict) -> Optional[str]:
        if not isinstance(upload_data, dict):
            return None
        if upload_data.get("upload_result"):
            return upload_data["upload_result"]
        response = upload_data.get("response")
        if isinstance(response, dict) and response.get("upload_result"):
            return response["upload_result"]
        return None

    @staticmethod
    def _extract_saved_story(save_result) -> Optional[dict]:
        if isinstance(save_result, list) and save_result:
            return save_result[0] if isinstance(save_result[0], dict) else None
        if isinstance(save_result, dict):
            items = save_result.get("items")
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return items[0]
        return None

    @staticmethod
    def _is_retryable_story_upload_error(exc: BaseException) -> bool:
        """Транзиентные сбои upload CDN / пустой save — повторяем. Flood control — нет."""
        code = getattr(exc, "code", None)
        message = str(exc).lower()
        # Как у wall: flood control (9) не крутим — стоп-кран vk_api_guard.
        if code == 9 or "flood control" in message:
            return False
        if isinstance(exc, (requests.RequestException, TimeoutError, OSError)):
            return True
        markers = (
            "go upload error",
            "upload failed: http 405",
            "upload failed: http 500",
            "upload failed: http 502",
            "upload failed: http 503",
            "upload failed: http 504",
            "temporar",
            "timeout",
            "connection",
            "invalid upload response",
            "failed to save story",
            "failed to get upload server",
        )
        if any(m in message for m in markers):
            return True
        return code in {1, 6, 8, 10, 29} or is_vk_flood_error(exc)

    def _upload_photo_story_once(self, image_path: str, *, link_url: Optional[str] = None) -> dict:
        params = {
            "add_to_news": 1,
            "group_id": self.group_id,
        }
        if link_url:
            params["link_url"] = link_url
            params["link_text"] = "more"

        logger.info("Getting VK story upload server group_id=%s link=%s", self.group_id, bool(link_url))
        before_vk_call("stories.getPhotoUploadServer")
        try:
            upload_server = self.vk.stories.getPhotoUploadServer(**params)
        except Exception as exc:
            after_vk_error(exc, "stories.getPhotoUploadServer")
            raise
        if not upload_server or "upload_url" not in upload_server:
            raise RuntimeError(f"Failed to get upload server: {upload_server}")

        # #region agent log
        vk_dbg(
            "A",
            "story_publisher.py:_upload_photo_story_once",
            "got_upload_server",
            {"group_id": self.group_id, "has_link": bool(link_url)},
        )
        # #endregion

        with open(image_path, "rb") as file:
            response = requests.post(
                upload_server["upload_url"],
                files={"file": file},
                timeout=120,
            )

        if response.status_code != 200:
            # #region agent log
            vk_dbg(
                "A",
                "story_publisher.py:_upload_photo_story_once",
                "upload_http_error",
                {"status": response.status_code, "body": (response.text or "")[:200]},
            )
            # #endregion
            raise RuntimeError(f"Upload failed: HTTP {response.status_code} {response.text[:300]}")

        upload_data = response.json()
        upload_result = self._extract_upload_result(upload_data)
        if not upload_result:
            # #region agent log
            vk_dbg(
                "A",
                "story_publisher.py:_upload_photo_story_once",
                "upload_payload_error",
                {"payload_keys": list(upload_data.keys()) if isinstance(upload_data, dict) else type(upload_data).__name__},
            )
            # #endregion
            raise RuntimeError(f"Invalid upload response: {upload_data}")

        logger.info("Saving VK story…")
        before_vk_call("stories.save")
        try:
            save_result = self.vk.stories.save(
                upload_results=upload_result,
                group_id=self.group_id,
            )
        except Exception as exc:
            after_vk_error(exc, "stories.save")
            raise
        story_data = self._extract_saved_story(save_result)
        if not story_data:
            # #region agent log
            vk_dbg(
                "B",
                "story_publisher.py:_upload_photo_story_once",
                "save_empty_items",
                {"save_type": type(save_result).__name__},
            )
            # #endregion
            raise RuntimeError(f"Failed to save story: {save_result}")
        return story_data

    def _upload_photo_story(self, image_path: str, *, link_url: Optional[str] = None) -> dict:
        """Загрузка кадра сторис с ретраями (как wall/market upload)."""
        attempts = VK_STORY_UPLOAD_ATTEMPTS
        last_exc: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                # #region agent log
                vk_dbg(
                    "C",
                    "story_publisher.py:_upload_photo_story",
                    "attempt_start",
                    {"attempt": attempt, "attempts": attempts, "has_link": bool(link_url)},
                )
                # #endregion
                result = self._upload_photo_story_once(image_path, link_url=link_url)
                # #region agent log
                vk_dbg(
                    "C",
                    "story_publisher.py:_upload_photo_story",
                    "attempt_ok",
                    {"attempt": attempt, "story_id": result.get("id") if isinstance(result, dict) else None},
                )
                # #endregion
                return result
            except Exception as exc:
                last_exc = exc
                after_vk_error(exc, "stories.upload")
                retryable = self._is_retryable_story_upload_error(exc)
                # #region agent log
                vk_dbg(
                    "C",
                    "story_publisher.py:_upload_photo_story",
                    "attempt_fail",
                    {
                        "attempt": attempt,
                        "attempts": attempts,
                        "retryable": retryable,
                        **vk_exc_debug_meta(exc),
                    },
                )
                # #endregion
                if attempt >= attempts or not retryable:
                    raise
                delay = vk_upload_backoff_seconds(attempt, exc)
                logger.warning(
                    "VK story upload failed (attempt %s/%s): %s; retry in %ss",
                    attempt,
                    attempts,
                    str(exc)[:180],
                    delay,
                )
                time.sleep(delay)
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _log(db, story_id: str, status: str, message: str) -> None:
        db.add(
            StoryPublicationLog(
                story_id=story_id,
                status=status,
                message=message,
            )
        )

    @classmethod
    def _persist_story_outcome(
        cls,
        story_id: str,
        *,
        status: str,
        message: str,
        link: Optional[str] = None,
        model_name: Optional[str] = None,
        price: Optional[str] = None,
        attempts: int = 3,
    ) -> bool:
        """Свежая сессия на запись: idle_in_transaction_timeout=15s убивает старую на ретраях upload."""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            db = SessionLocal()
            try:
                story = db.query(Story).filter(Story.id == story_id).first()
                if not story:
                    logger.error("Story %s not found while persisting %s", story_id, status)
                    return False
                if status == "success":
                    story.is_published = True
                    story.published_at = datetime.now(timezone.utc)
                    if link:
                        story.post_link = link
                    if model_name:
                        story.model_name = model_name
                    if price:
                        story.price = price
                cls._log(db, story_id, status, message)
                db.commit()
                # #region agent log
                vk_dbg(
                    "F",
                    "story_publisher.py:_persist_story_outcome",
                    "persist_ok",
                    {"story_id": story_id, "status": status, "attempt": attempt},
                    run_id="post-fix",
                )
                # #endregion
                return True
            except Exception as exc:
                last_exc = exc
                try:
                    db.rollback()
                except Exception:
                    pass
                # #region agent log
                vk_dbg(
                    "F",
                    "story_publisher.py:_persist_story_outcome",
                    "persist_fail",
                    {
                        "story_id": story_id,
                        "status": status,
                        "attempt": attempt,
                        **vk_exc_debug_meta(exc),
                    },
                )
                # #endregion
                logger.warning(
                    "Persist story %s status=%s failed (attempt %s/%s): %s",
                    story_id,
                    status,
                    attempt,
                    attempts,
                    str(exc)[:180],
                )
                time.sleep(min(2.0 * attempt, 5.0))
            finally:
                db.close()
        if last_exc:
            logger.error("Persist story %s giving up: %s", story_id, last_exc)
        return False

    async def publish_story(self, story_id: str) -> bool:
        """Основной путь: коллаж из фото поста + ссылка на опубликованный пост ВК.

        БД-сессия не держится на download/upload/retry: иначе Postgres рвёт
        idle_in_transaction_session_timeout=15s, сторис уже в VK, а у нас fail.
        """
        before_vk_call("stories.publish")
        temp_path = None
        # #region agent log
        vk_dbg("D", "story_publisher.py:publish_story", "enter", {"story_id": story_id})
        # #endregion

        post_id: Optional[str] = None
        vk_post_id: Optional[str] = None
        photo_ids: List[str] = []
        model_name: Optional[str] = None
        price: Optional[str] = None
        post_link: Optional[str] = None

        # --- короткая сессия: только чтение контекста ---
        db = SessionLocal()
        try:
            story = db.query(Story).filter(Story.id == story_id).first()
            if not story:
                logger.error("Story %s not found", story_id)
                return False
            if story.is_published:
                logger.info("Story %s was published before — publishing a new frame", story_id)
            if not story.post_id:
                logger.error("Story %s has no post_id", story_id)
                return False

            post = db.query(Post).filter(Post.id == story.post_id).first()
            if not post:
                logger.error("Post %s not found for story %s", story.post_id, story_id)
                return False
            if not post.is_published_vk or not post.vk_post_id:
                logger.error("Post %s is not published to VK", story.post_id)
                self._log(db, story.id, "error", "Post is not published to VK")
                db.commit()
                return False

            photo_ids = list(post.photos or [])
            if not photo_ids and story.media_file_id:
                photo_ids = [story.media_file_id]
            if not photo_ids:
                logger.error("No photos for story %s", story_id)
                self._log(db, story.id, "error", "No photos to build story")
                db.commit()
                return False

            model_name, price = self._resolve_title_price(post, story)
            post_link = self._post_link(post)
            post_id = post.id
            vk_post_id = post.vk_post_id
            db.commit()  # закрыть транзакцию до длинной сети
        except Exception as e:
            logger.error("Error loading story context %s: %s", story_id, e)
            try:
                db.rollback()
            except Exception:
                pass
            return False
        finally:
            db.close()

        try:
            logger.info(
                "Building VK story collage: post=%s photos=%s model=%s price=%s",
                vk_post_id,
                len(photo_ids),
                model_name,
                price,
            )
            blobs = await self._download_photos(photo_ids, limit=6)
            if not blobs:
                logger.error("Failed to download photos for story %s", story_id)
                self._persist_story_outcome(
                    story_id, status="error", message="Failed to download photos"
                )
                # #region agent log
                vk_dbg("E", "story_publisher.py:publish_story", "download_fail", {"story_id": story_id})
                # #endregion
                return False

            brand, logo_blob = await asyncio.to_thread(self._fetch_brand_assets)
            image_bytes = build_story_image(
                blobs,
                title=model_name,
                price=price,
                brand_name=brand,
                logo_blob=logo_blob,
                style=self._story_style(),
            )
            if not image_bytes:
                self._persist_story_outcome(
                    story_id, status="error", message="Failed to compose story image"
                )
                return False

            try:
                preview_path = self.preview_path_for_post(post_id or story_id)
                with open(preview_path, "wb") as pf:
                    pf.write(image_bytes)
            except Exception as e:
                logger.warning("Failed to save story preview file: %s", e)

            fd, temp_path = tempfile.mkstemp(prefix=f"vk_story_{story_id}_", suffix=".jpg")
            os.close(fd)
            with open(temp_path, "wb") as f:
                f.write(image_bytes)

            # #region agent log
            vk_dbg(
                "A",
                "story_publisher.py:publish_story",
                "before_upload",
                {
                    "story_id": story_id,
                    "post_id": post_id,
                    "vk_post_id": vk_post_id,
                    "bytes": len(image_bytes),
                    "has_link": bool(post_link),
                },
            )
            # #endregion
            story_data = await asyncio.to_thread(
                self._upload_photo_story,
                temp_path,
                link_url=post_link,
            )

            owner_id = int(story_data.get("owner_id", -self.group_id))
            vk_story_id = int(story_data.get("id"))
            link = story_url(owner_id, vk_story_id)

            persisted = self._persist_story_outcome(
                story_id,
                status="success",
                message=f"Published to VK: {link}",
                link=link,
                model_name=model_name,
                price=price,
            )
            if not persisted:
                # Сторис уже в VK — не врать «fail» без следа; лог ошибки отдельно.
                logger.error(
                    "Story %s uploaded to VK (%s) but DB persist failed",
                    story_id,
                    link,
                )
                # #region agent log
                vk_dbg(
                    "F",
                    "story_publisher.py:publish_story",
                    "uploaded_but_persist_failed",
                    {"story_id": story_id, "link": link},
                )
                # #endregion
                return False

            logger.info("Story %s published: %s", story_id, link)
            # #region agent log
            vk_dbg(
                "D",
                "story_publisher.py:publish_story",
                "success",
                {"story_id": story_id, "link": link},
                run_id="post-fix",
            )
            # #endregion
            return True

        except Exception as e:
            logger.error("Error publishing story %s: %s", story_id, e)
            # #region agent log
            vk_dbg(
                "A",
                "story_publisher.py:publish_story",
                "error",
                {"story_id": story_id, **vk_exc_debug_meta(e)},
            )
            # #endregion
            self._persist_story_outcome(story_id, status="error", message=str(e)[:500])
            return False
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


async def publish_story_to_vk(story_id: str) -> bool:
    publisher = VKStoryPublisher()
    return await publisher.publish_story(story_id)


async def ensure_vk_story_record(post_id: str) -> Optional[str]:
    """Создать или вернуть Story(platform=vk) для поста. Возвращает story_id."""
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            return None
        existing = (
            db.query(Story)
            .filter(Story.post_id == post_id, Story.platform == "vk")
            .first()
        )
        if existing:
            return existing.id
        model_name, price = extract_model_and_price(post.text or "")
        media_file_id = (post.photos or [None])[0] if post.photos else None
        story = Story(
            post_id=post_id,
            platform="vk",
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


async def maybe_auto_publish_vk_story(post_id: str) -> bool:
    """
    Если включён тумблер «Сторис (авто)» — опубликовать сторис ВК после стены.
    Не зависит от «Товары ВК». Ошибки не пробрасываются наружу (лента уже ушла).
    """
    try:
        from app.services.settings_service import get_settings_service

        if not get_settings_service().is_stories_auto_enabled():
            return False
        db = SessionLocal()
        try:
            post = db.query(Post).filter(Post.id == post_id).first()
            if not post or not post.is_published_vk or not post.vk_post_id:
                return False
            if not post.photos:
                logger.info("Auto VK story skipped for %s: no photos", post_id)
                return False
        finally:
            db.close()

        story_id = await ensure_vk_story_record(post_id)
        if not story_id:
            return False
        # #region agent log
        vk_dbg(
            "D",
            "story_publisher.py:maybe_auto_publish_vk_story",
            "auto_start",
            {"post_id": post_id, "story_id": story_id},
        )
        # #endregion
        ok = await publish_story_to_vk(story_id)
        if ok:
            logger.info("Auto VK story published for post %s (story=%s)", post_id, story_id)
        else:
            logger.error("Auto VK story failed for post %s (story=%s)", post_id, story_id)
        # #region agent log
        vk_dbg(
            "D",
            "story_publisher.py:maybe_auto_publish_vk_story",
            "auto_done",
            {"post_id": post_id, "story_id": story_id, "ok": bool(ok)},
            run_id="post-fix" if ok else "pre",
        )
        # #endregion
        return bool(ok)
    except Exception as e:
        logger.error("Auto VK story error for post %s: %s", post_id, e)
        # #region agent log
        vk_dbg(
            "D",
            "story_publisher.py:maybe_auto_publish_vk_story",
            "auto_exception",
            {"post_id": post_id, **vk_exc_debug_meta(e)},
        )
        # #endregion
        return False


async def compose_vk_story_preview(post_id: str) -> Optional[str]:
    """Собрать превью-кадр и сохранить в media/story_previews/. Возвращает путь или None."""
    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            return None
        if not post.is_published_vk or not post.vk_post_id:
            return None
        if not post.photos:
            return None
        publisher = VKStoryPublisher()
        image_bytes = await publisher.compose_frame_for_post(post)
        if not image_bytes:
            return None
        path = publisher.preview_path_for_post(post_id)
        with open(path, "wb") as f:
            f.write(image_bytes)
        return path
    finally:
        db.close()
