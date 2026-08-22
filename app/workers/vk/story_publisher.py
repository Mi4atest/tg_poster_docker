"""Публикация историй VK от имени сообщества (коллаж в бабле + ссылка на пост)."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
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
from app.utils.vk_client import community_token, resolved_vk_group_id_int
from app.utils.vk_urls import story_url, wall_post_url
from app.workers.vk.story_composer import build_story_image

logger = logging.getLogger(__name__)


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
        return build_story_image(
            blobs,
            title=model_name,
            price=price,
            brand_name=brand,
            logo_blob=logo_blob,
        )

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

    def _upload_photo_story(self, image_path: str, *, link_url: Optional[str] = None) -> dict:
        params = {
            "add_to_news": 1,
            "group_id": self.group_id,
        }
        if link_url:
            params["link_url"] = link_url
            params["link_text"] = "more"

        logger.info("Getting VK story upload server group_id=%s link=%s", self.group_id, bool(link_url))
        upload_server = self.vk.stories.getPhotoUploadServer(**params)
        if not upload_server or "upload_url" not in upload_server:
            raise RuntimeError(f"Failed to get upload server: {upload_server}")

        with open(image_path, "rb") as file:
            response = requests.post(
                upload_server["upload_url"],
                files={"file": file},
                timeout=120,
            )

        if response.status_code != 200:
            raise RuntimeError(f"Upload failed: HTTP {response.status_code} {response.text[:300]}")

        upload_data = response.json()
        upload_result = self._extract_upload_result(upload_data)
        if not upload_result:
            raise RuntimeError(f"Invalid upload response: {upload_data}")

        logger.info("Saving VK story…")
        save_result = self.vk.stories.save(
            upload_results=upload_result,
            group_id=self.group_id,
        )
        story_data = self._extract_saved_story(save_result)
        if not story_data:
            raise RuntimeError(f"Failed to save story: {save_result}")
        return story_data

    async def publish_story(self, story_id: str) -> bool:
        """Основной путь: коллаж из фото поста + ссылка на опубликованный пост ВК."""
        db = SessionLocal()
        temp_path = None
        try:
            story = db.query(Story).filter(Story.id == story_id).first()
            if not story:
                logger.error("Story %s not found", story_id)
                return False
            # Повторная публикация разрешена (истории эфемерны; в VK могли удалить вручную)
            if story.is_published:
                logger.info("Story %s was published before — publishing a new frame", story_id)
                story.is_published = False
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
                return False

            photo_ids = list(post.photos or [])
            if not photo_ids and story.media_file_id:
                photo_ids = [story.media_file_id]
            if not photo_ids:
                logger.error("No photos for story %s", story_id)
                self._log(db, story.id, "error", "No photos to build story")
                return False

            model_name, price = self._resolve_title_price(post, story)
            post_link = self._post_link(post)

            logger.info(
                "Building VK story collage: post=%s photos=%s model=%s price=%s",
                post.vk_post_id,
                len(photo_ids),
                model_name,
                price,
            )
            blobs = await self._download_photos(photo_ids, limit=6)
            if not blobs:
                logger.error("Failed to download photos for story %s", story_id)
                self._log(db, story.id, "error", "Failed to download photos")
                return False

            brand, logo_blob = await asyncio.to_thread(self._fetch_brand_assets)
            image_bytes = build_story_image(
                blobs,
                title=model_name,
                price=price,
                brand_name=brand,
                logo_blob=logo_blob,
            )
            if not image_bytes:
                self._log(db, story.id, "error", "Failed to compose story image")
                return False

            try:
                preview_path = self.preview_path_for_post(post.id)
                with open(preview_path, "wb") as pf:
                    pf.write(image_bytes)
            except Exception as e:
                logger.warning("Failed to save story preview file: %s", e)

            fd, temp_path = tempfile.mkstemp(prefix=f"vk_story_{story_id}_", suffix=".jpg")
            os.close(fd)
            with open(temp_path, "wb") as f:
                f.write(image_bytes)

            story_data = await asyncio.to_thread(
                self._upload_photo_story,
                temp_path,
                link_url=post_link,
            )

            owner_id = int(story_data.get("owner_id", -self.group_id))
            vk_story_id = int(story_data.get("id"))
            link = story_url(owner_id, vk_story_id)

            story.is_published = True
            story.published_at = datetime.now(timezone.utc)
            story.post_link = link
            story.model_name = model_name or story.model_name
            story.price = price or story.price
            self._log(db, story.id, "success", f"Published to VK: {link}")
            db.commit()
            logger.info("Story %s published: %s", story_id, link)
            return True

        except Exception as e:
            logger.error("Error publishing story %s: %s", story_id, e)
            try:
                self._log(db, story_id, "error", str(e))
                db.commit()
            except Exception:
                db.rollback()
            return False
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
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
    Если включён тумблер «Сторис ВК (авто)» — опубликовать сторис после стены.
    Не зависит от «Товары ВК». Ошибки не пробрасываются наружу (лента уже ушла).
    """
    try:
        from app.services.settings_service import get_settings_service

        if not get_settings_service().is_vk_stories_auto_enabled():
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
        ok = await publish_story_to_vk(story_id)
        if ok:
            logger.info("Auto VK story published for post %s (story=%s)", post_id, story_id)
        else:
            logger.error("Auto VK story failed for post %s (story=%s)", post_id, story_id)
        return bool(ok)
    except Exception as e:
        logger.error("Auto VK story error for post %s: %s", post_id, e)
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
