import logging
import os
import ssl
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import aiohttp
from sqlalchemy import text

from app.api.models.post import PublicationLog
from app.config.settings import MEDIA_DIR
from app.db.database import SessionLocal
from app.db.post_queries import fetch_post, insert_publication_log
from app.services.admin_alert_service import send_admin_alert
from app.utils.text_formatter import format_for_instagram
from app.utils.vk_client import community_token
from app.workers.instagram.token_manager import InstagramGraphTokenManager
from app.workers.instagram.graph_client import InstagramGraphClient

logger = logging.getLogger(__name__)


class InstagramGraphPublisher:
    """Публикация постов через официальный Instagram Graph API."""

    def __init__(self) -> None:
        self.token_manager = InstagramGraphTokenManager()
        self.access_token = self.token_manager.get_access_token()
        self.ig_user_id = self.token_manager.get_ig_user_id()
        self.api_version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v19.0").strip()
        self.media_base_url = self.token_manager.get_media_base_url()
        self.timeout_seconds = int(os.getenv("INSTAGRAM_GRAPH_TIMEOUT_SECONDS", "60"))
        self.vk_api_version = os.getenv("VK_API_VERSION", "5.199")
        self._last_graph_error: Optional[str] = None
        self._last_graph_error_code: Optional[int] = None
        self._last_published_media_id: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.access_token and self.ig_user_id)

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}"

    async def publish_post(self, post_id: str) -> bool:
        db = SessionLocal()
        self._last_graph_error = None
        try:
            post = fetch_post(db, post_id)
            if not post:
                logger.error(f"Пост с ID {post_id} не найден")
                return False

            if not self.enabled:
                reason = (
                    "Graph API не настроен. Нужны INSTAGRAM_GRAPH_ACCESS_TOKEN, "
                    "INSTAGRAM_GRAPH_USER_ID и INSTAGRAM_GRAPH_MEDIA_BASE_URL"
                )
                self._log_error(db, post_id, reason)
                logger.error(reason)
                return False

            preflight_ok, preflight_error = await self.token_manager.preflight_or_error()
            if not preflight_ok:
                reason = f"Preflight Graph token failed: {preflight_error}"
                self._log_error(db, post_id, reason)
                await send_admin_alert(f"Instagram публикация остановлена: {preflight_error}")
                logger.error(reason)
                return False

            self.access_token = self.token_manager.get_access_token()

            media_urls = await self._prepare_media_urls(post)
            if not media_urls:
                self._log_error(db, post_id, "Нет доступных медиафайлов для Graph API")
                return False

            caption = format_for_instagram(post.text)
            creation_id = await self._create_media_container(media_urls, caption)
            if not creation_id:
                details = self._last_graph_error or "Не удалось создать media container в Graph API"
                if self._last_graph_error_code == 190:
                    details = f"OAuthException code 190: {details}"
                    await send_admin_alert(f"Instagram OAuthException (code 190) при создании media: {details}")
                self._log_error(db, post_id, f"Graph API ошибка: {details}")
                return False

            if not await self._publish_creation(creation_id):
                details = self._last_graph_error or "Не удалось опубликовать media container в Graph API"
                if self._last_graph_error_code == 190:
                    details = f"OAuthException code 190: {details}"
                    await send_admin_alert(f"Instagram OAuthException (code 190) при публикации media: {details}")
                self._log_error(db, post_id, f"Graph API ошибка: {details}")
                return False

            published_media_id = self._last_published_media_id
            if not published_media_id:
                self._log_error(db, post_id, "Graph API не вернул ID опубликованного медиа")
                return False

            graph_client = InstagramGraphClient()
            permalink, _shortcode = await graph_client.fetch_media_permalink(published_media_id)

            now = datetime.now(timezone.utc)
            db.execute(
                text(
                    "UPDATE posts SET is_published_instagram = true, published_instagram_at = :now, "
                    "instagram_media_id = :media_id, "
                    "instagram_link = COALESCE(:link, instagram_link), updated_at = NOW() "
                    "WHERE id = :id"
                ),
                {
                    "id": post_id,
                    "now": now,
                    "media_id": published_media_id,
                    "link": permalink,
                },
            )
            db.commit()

            if permalink or published_media_id:
                try:
                    db.execute(
                        text(
                            "UPDATE products SET instagram_link = COALESCE(:link, instagram_link), "
                            "instagram_media_id = COALESCE(:media_id, instagram_media_id) "
                            "WHERE post_id = :post_id"
                        ),
                        {
                            "post_id": post_id,
                            "link": permalink,
                            "media_id": published_media_id,
                        },
                    )
                    db.commit()
                except Exception as sync_err:
                    logger.warning(
                        "Failed to sync instagram fields to products for post %s: %s",
                        post_id,
                        sync_err,
                    )
                    db.rollback()
            insert_publication_log(
                db, post_id, "instagram", "success", "Пост опубликован через Instagram Graph API"
            )
            db.commit()
            logger.info(f"Пост {post_id} успешно опубликован через Graph API")
            return True
        except Exception as exc:
            logger.error(f"Ошибка Graph API при публикации поста {post_id}: {exc}")
            self._log_error(db, post_id, f"Ошибка Graph API: {exc}")
            return False
        finally:
            db.close()

    def _log_error(self, db, post_id: str, message: str) -> None:
        db.add(PublicationLog(post_id=post_id, platform="instagram", status="error", message=message))
        db.commit()

    async def _prepare_media_urls(self, post: Any) -> List[str]:
        # Приоритетный источник: уже опубликованный VK-пост.
        vk_photo_urls = await self._get_vk_wall_photo_urls(post)
        if vk_photo_urls:
            return vk_photo_urls[:6]

        # Второй источник: прямые URL файлов Telegram по file_id (если есть токен бота).
        telegram_photo_urls = await self._get_telegram_photo_urls(post)
        if telegram_photo_urls:
            return telegram_photo_urls[:6]

        # Fallback: локальные фото, если есть публичная раздача.
        if not self.media_base_url:
            logger.warning("MEDIA_BASE_URL не задан и VK-фото недоступны")
            return []

        post_dir = MEDIA_DIR / post.storage_path
        photos = post.photos or []
        media_urls: List[str] = []

        for i, _ in enumerate(photos):
            local_path = post_dir / f"photo_{i}.jpg"
            if not local_path.exists() and i < len(photos):
                await self._download_telegram_file(photos[i], local_path)
            if local_path.exists():
                media_urls.append(self._to_public_url(local_path))
        return media_urls[:6]

    async def _get_telegram_photo_urls(self, post: Any) -> List[str]:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not bot_token:
            return []
        photos = post.photos or []
        if not photos:
            return []
        urls: List[str] = []
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for file_id in photos[:6]:
                file_info_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
                try:
                    async with session.get(file_info_url) as response:
                        if response.status != 200:
                            continue
                        payload = await response.json(content_type=None)
                except Exception:
                    continue
                file_path = payload.get("result", {}).get("file_path")
                if payload.get("ok") and file_path:
                    urls.append(f"https://api.telegram.org/file/bot{bot_token}/{file_path}")
        return urls

    def _to_public_url(self, file_path: Path) -> str:
        relative = file_path.relative_to(MEDIA_DIR).as_posix()
        return f"{self.media_base_url}/{relative}"

    async def _create_media_container(self, media_urls: List[str], caption: str) -> Optional[str]:
        photos = [url for url in media_urls if not url.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm"))]
        videos: List[str] = []

        # Компромиссный режим: публикуем только фото (одиночное или карусель).
        if len(photos) > 1:
            children = []
            valid_photo_urls = []
            for idx, photo_url in enumerate(photos[:6]):
                child_id = await self._create_container(
                    {
                        "image_url": photo_url,
                        "is_carousel_item": "true",
                    }
                )
                if not child_id:
                    # Частый кейс Graph API: часть фото не проходит по aspect ratio (code 36003).
                    # Пропускаем только проблемный кадр и продолжаем собирать карусель.
                    if str(self._last_graph_error_code) == "36003":
                        continue
                    return None
                children.append(child_id)
                valid_photo_urls.append(photo_url)

            if len(children) >= 2:
                return await self._create_container(
                    {
                        "media_type": "CAROUSEL",
                        "children": ",".join(children),
                        "caption": caption,
                    }
                )

            if len(children) == 1:
                # Если после отбраковки остался один валидный кадр, публикуем одиночным постом.
                return await self._create_container({"image_url": valid_photo_urls[0], "caption": caption})

            return None

        if photos and videos:
            logger.warning("Смешанный набор фото+видео: публикуем только фото через Graph API")
            return await self._create_container({"image_url": photos[0], "caption": caption})

        if photos:
            return await self._create_container({"image_url": photos[0], "caption": caption})

        return None

    async def _get_vk_wall_photo_urls(self, post: Any) -> List[str]:
        if not post.vk_post_id:
            return []
        token = community_token()
        if not token:
            logger.warning("VK community token не задан, не можем получить фото из VK")
            return []

        endpoint = "https://api.vk.com/method/wall.getById"
        params = {
            "posts": post.vk_post_id,
            "access_token": token,
            "v": self.vk_api_version,
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(endpoint, params=params) as response:
                if response.status >= 400:
                    logger.warning(f"VK wall.getById error status={response.status}")
                    return []
                payload = await response.json(content_type=None)

        if payload.get("error"):
            logger.warning(f"VK wall.getById error: {payload.get('error')}")
            return []

        items = payload.get("response", {}).get("items", [])
        if not items:
            return []

        post_item = items[0]
        attachments = post_item.get("attachments", [])
        copy_history = post_item.get("copy_history", []) or []
        copy_attachments_total = 0
        copy_attachment_types: List[str] = []
        if copy_history:
            first_copy = copy_history[0] or {}
            copy_attachments = first_copy.get("attachments", []) or []
            copy_attachments_total = len(copy_attachments)
            copy_attachment_types = [a.get("type") for a in copy_attachments[:10]]
        photo_urls: List[str] = []
        for att in attachments:
            if att.get("type") != "photo":
                continue
            photo = att.get("photo", {})
            sizes = photo.get("sizes", [])
            best_url = self._pick_largest_vk_photo_url(sizes)
            if best_url:
                photo_urls.append(best_url)

        return photo_urls

    def _pick_largest_vk_photo_url(self, sizes: List[dict]) -> Optional[str]:
        if not sizes:
            return None
        best = None
        best_area = -1
        for size in sizes:
            url = size.get("url")
            if not url:
                continue
            width = int(size.get("width", 0) or 0)
            height = int(size.get("height", 0) or 0)
            area = width * height
            if area > best_area:
                best_area = area
                best = url
        return best

    async def _create_container(self, params: dict) -> Optional[str]:
        endpoint = f"{self.base_url}/{self.ig_user_id}/media"
        payload = {**params, "access_token": self.access_token}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, data=payload) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    graph_error = (data or {}).get("error", {})
                    self._last_graph_error = graph_error.get("message", str(data))
                    self._last_graph_error_code = graph_error.get("code")
                    logger.error(f"Graph API media error ({response.status}): {data}")
                    return None
                return data.get("id")

    async def _publish_creation(self, creation_id: str) -> bool:
        self._last_published_media_id = None
        endpoint = f"{self.base_url}/{self.ig_user_id}/media_publish"
        payload = {"creation_id": creation_id, "access_token": self.access_token}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        retries = 3
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(1, retries + 1):
                async with session.post(endpoint, data=payload) as response:
                    data = await response.json(content_type=None)
                    if response.status < 400:
                        media_id = (data or {}).get("id")
                        if media_id:
                            self._last_published_media_id = str(media_id)
                        return bool(media_id)

                    graph_error = (data or {}).get("error", {})
                    self._last_graph_error = graph_error.get("message", str(data))
                    self._last_graph_error_code = graph_error.get("code")
                    logger.error(f"Graph API media_publish error ({response.status}): {data}")

                    if str(self._last_graph_error_code) == "9007" and attempt < retries:
                        status_code = await self._get_creation_status_code(session, creation_id)
                        await asyncio.sleep(2)
                        continue
                    return False
        return False

    async def _get_creation_status_code(
        self, session: aiohttp.ClientSession, creation_id: str
    ) -> Optional[str]:
        endpoint = f"{self.base_url}/{creation_id}"
        params = {"fields": "status_code", "access_token": self.access_token}
        try:
            async with session.get(endpoint, params=params) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    return None
                return (payload or {}).get("status_code")
        except Exception:
            return None

    async def _download_telegram_file(self, file_id: str, save_path: Path) -> bool:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not bot_token:
            logger.error("Отсутствует TELEGRAM_BOT_TOKEN для скачивания медиа")
            return False

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)

        file_info_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(file_info_url) as response:
                if response.status != 200:
                    logger.error(f"Ошибка Telegram getFile: {response.status}")
                    return False
                payload = await response.json()
                file_path = payload.get("result", {}).get("file_path")
                if not payload.get("ok") or not file_path:
                    logger.error(f"Ошибка Telegram API getFile: {payload}")
                    return False

            download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            async with session.get(download_url) as media_response:
                if media_response.status != 200:
                    logger.error(f"Ошибка скачивания файла Telegram: {media_response.status}")
                    return False

                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_bytes(await media_response.read())
                return True
