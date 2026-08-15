import logging
import os
import ssl
import asyncio
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from app.config.settings import APP_LOG_DIR, MEDIA_DIR
from app.db.database import SessionLocal
from app.db.post_queries import (
    fetch_post,
    insert_publication_log,
    mark_post_published_instagram,
    sync_instagram_fields_to_products,
)
from app.services.admin_alert_service import send_admin_alert
from app.utils.text_formatter import format_for_instagram
from app.utils.vk_client import community_token
from app.workers.instagram.token_manager import InstagramGraphTokenManager
from app.workers.instagram.graph_client import InstagramGraphClient

logger = logging.getLogger(__name__)

_file_handler_added = False

# Лимит карусели Instagram Graph API: до 10 media items (фото/видео).
# Посты >10 в приложении IG не означают, что Content Publishing API принимает больше.
IG_CAROUSEL_MAX_ITEMS = 10


def _ensure_app_ig_log_handler() -> None:
    """Пишет логи модуля в app/logs/instagram_graph.log."""
    global _file_handler_added
    if _file_handler_added:
        return
    try:
        APP_LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            APP_LOG_DIR / "instagram_graph.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(fh)
        logger.setLevel(logging.INFO)
        _file_handler_added = True
    except OSError:
        pass


def _redact_url(url: str) -> str:
    """Убирает секреты из URL для логов (bot token и т.п.)."""
    try:
        if "/file/bot" in url:
            parts = url.split("/file/bot", 1)
            rest = parts[1]
            slash = rest.find("/")
            token_part = rest if slash < 0 else rest[:slash]
            path_part = "" if slash < 0 else rest[slash:]
            return f"{parts[0]}/file/bot***{token_part[-4:]}{path_part}"
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:80]}"
    except Exception:
        return "<bad-url>"


class InstagramGraphPublisher:
    """Публикация постов через официальный Instagram Graph API."""

    def __init__(self) -> None:
        _ensure_app_ig_log_handler()
        self.token_manager = InstagramGraphTokenManager()
        self.access_token = self.token_manager.get_access_token()
        self.ig_user_id = self.token_manager.get_ig_user_id()
        self.api_version = os.getenv("INSTAGRAM_GRAPH_API_VERSION", "v19.0").strip()
        self.media_base_url = self.token_manager.get_media_base_url()
        self.timeout_seconds = int(os.getenv("INSTAGRAM_GRAPH_TIMEOUT_SECONDS", "60"))
        self.vk_api_version = os.getenv("VK_API_VERSION", "5.199")
        self._last_graph_error: Optional[str] = None
        self._last_graph_error_code: Optional[int] = None
        self._last_graph_error_subcode: Optional[int] = None
        self._last_published_media_id: Optional[str] = None
        self._uri_reject_seen: bool = False
        self._last_children_count: int = 0

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

            caption = format_for_instagram(post.text)
            media_candidates = await self._collect_media_candidates(post)
            if not media_candidates:
                self._log_error(db, post_id, "Нет доступных медиафайлов для Graph API")
                return False

            creation_id = None
            used_source = None
            for source_name, media_urls in media_candidates:
                self._uri_reject_seen = False
                logger.info(
                    "IG media try post_id=%s source=%s count=%s",
                    post_id,
                    source_name,
                    len(media_urls),
                )
                creation_id = await self._create_media_container(media_urls, caption)
                if creation_id:
                    used_source = source_name
                    break
                # VK CDN URI часто режет Meta (9004/2207052) — пробуем следующий источник.
                if self._uri_reject_seen or self._is_skippable_media_error():
                    logger.warning(
                        "IG source=%s rejected (code=%s subcode=%s uri_reject=%s), fallback next",
                        source_name,
                        self._last_graph_error_code,
                        self._last_graph_error_subcode,
                        self._uri_reject_seen,
                    )
                    continue
                break

            if not creation_id:
                details = self._last_graph_error or "Не удалось создать media container в Graph API"
                if self._last_graph_error_code == 190:
                    details = f"OAuthException code 190: {details}"
                    await send_admin_alert(f"Instagram OAuthException (code 190) при создании media: {details}")
                self._log_error(db, post_id, f"Graph API ошибка: {details}")
                return False

            logger.info(
                "IG container ok post_id=%s source=%s creation_id=%s photos=%s",
                post_id,
                used_source,
                creation_id,
                self._last_children_count,
            )

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

            # Свежая сессия: параллельный VK Market INSERT мог убить соединение пула.
            try:
                db.rollback()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass
            db = SessionLocal()
            mark_post_published_instagram(
                db,
                post_id,
                media_id=published_media_id,
                link=permalink,
            )
            db.commit()

            if permalink or published_media_id:
                try:
                    sync_instagram_fields_to_products(
                        db,
                        post_id,
                        media_id=published_media_id,
                        link=permalink,
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
                db,
                post_id,
                "instagram",
                "success",
                f"Пост опубликован через Instagram Graph API (source={used_source}, photos={self._last_children_count})",
            )
            db.commit()
            logger.info(f"Пост {post_id} успешно опубликован через Graph API")
            return True
        except Exception as exc:
            logger.error(f"Ошибка Graph API при публикации поста {post_id}: {exc}")
            try:
                self._log_error(db, post_id, f"Ошибка Graph API: {exc}")
            except Exception:
                try:
                    db2 = SessionLocal()
                    self._log_error(db2, post_id, f"Ошибка Graph API: {exc}")
                    db2.close()
                except Exception:
                    logger.exception("Failed to write IG error log for %s", post_id)
            return False
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _log_error(self, db, post_id: str, message: str) -> None:
        insert_publication_log(db, post_id, "instagram", "error", message)
        db.commit()

    def _is_uri_reject_error(self) -> bool:
        """Meta не смогла скачать URI (часто VK CDN) — нужен fallback на другой источник."""
        code = str(self._last_graph_error_code or "")
        sub = str(self._last_graph_error_subcode or "")
        if code == "9004" and sub == "2207052":
            return True
        msg = (self._last_graph_error or "").lower()
        return "uri" in msg and ("requirement" in msg or "media type" in msg)

    def _is_aspect_ratio_error(self) -> bool:
        """Кадр не проходит по aspect ratio — можно пропустить только его."""
        return str(self._last_graph_error_code or "") == "36003"

    def _is_skippable_media_error(self) -> bool:
        """Ошибки, при которых имеет смысл пробовать другой источник медиа."""
        return self._is_uri_reject_error() or self._is_aspect_ratio_error()

    async def _collect_media_candidates(self, post: Any) -> List[Tuple[str, List[str]]]:
        """Список источников медиа по приоритету: VK → Telegram → local."""
        candidates: List[Tuple[str, List[str]]] = []

        vk_photo_urls = await self._get_vk_wall_photo_urls(post)
        if vk_photo_urls:
            candidates.append(("vk", vk_photo_urls[:IG_CAROUSEL_MAX_ITEMS]))

        telegram_photo_urls = await self._get_telegram_photo_urls(post)
        if telegram_photo_urls:
            candidates.append(("telegram", telegram_photo_urls[:IG_CAROUSEL_MAX_ITEMS]))

        local_urls = await self._get_local_photo_urls(post)
        if local_urls:
            candidates.append(("local", local_urls[:IG_CAROUSEL_MAX_ITEMS]))

        return candidates

    async def _prepare_media_urls(self, post: Any) -> List[str]:
        candidates = await self._collect_media_candidates(post)
        return candidates[0][1] if candidates else []

    async def _get_local_photo_urls(self, post: Any) -> List[str]:
        if not self.media_base_url:
            logger.warning("MEDIA_BASE_URL не задан и локальные фото недоступны")
            return []

        post_dir = MEDIA_DIR / post.storage_path
        photos = (post.photos or [])[:IG_CAROUSEL_MAX_ITEMS]
        media_urls: List[str] = []

        for i, _ in enumerate(photos):
            local_path = post_dir / f"photo_{i}.jpg"
            if not local_path.exists() and i < len(photos):
                await self._download_telegram_file(photos[i], local_path)
            if local_path.exists():
                media_urls.append(self._to_public_url(local_path))
        return media_urls[:IG_CAROUSEL_MAX_ITEMS]

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
            for file_id in photos[:IG_CAROUSEL_MAX_ITEMS]:
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
        self._last_children_count = 0

        # Компромиссный режим: публикуем только фото (одиночное или карусель).
        if len(photos) > 1:
            children = []
            valid_photo_urls = []
            for idx, photo_url in enumerate(photos[:IG_CAROUSEL_MAX_ITEMS]):
                child_id = await self._create_container(
                    {
                        "image_url": photo_url,
                        "is_carousel_item": "true",
                    }
                )
                if not child_id:
                    # URI reject (часто VK CDN): не выкидываем отдельные кадры —
                    # иначе карусель публикуется неполной. Abort → fallback на Telegram.
                    if self._is_uri_reject_error():
                        self._uri_reject_seen = True
                        logger.warning(
                            "IG URI reject idx=%s/%s code=%s subcode=%s url=%s — abort source for fallback",
                            idx,
                            len(photos[:IG_CAROUSEL_MAX_ITEMS]),
                            self._last_graph_error_code,
                            self._last_graph_error_subcode,
                            _redact_url(photo_url),
                        )
                        return None
                    # 36003 (aspect): пропускаем только проблемный кадр.
                    if self._is_aspect_ratio_error():
                        logger.warning(
                            "IG skip frame idx=%s code=36003 (aspect) url=%s",
                            idx,
                            _redact_url(photo_url),
                        )
                        continue
                    return None
                children.append(child_id)
                valid_photo_urls.append(photo_url)

            self._last_children_count = len(children)
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
            child_id = await self._create_container({"image_url": photos[0], "caption": caption})
            if not child_id and self._is_uri_reject_error():
                self._uri_reject_seen = True
            if child_id:
                self._last_children_count = 1
            return child_id

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
                    self._last_graph_error = graph_error.get("message") or graph_error.get(
                        "error_user_msg"
                    ) or str(data)
                    self._last_graph_error_code = graph_error.get("code")
                    self._last_graph_error_subcode = graph_error.get("error_subcode")
                    # Prefer user-facing URI message when present.
                    user_msg = graph_error.get("error_user_msg")
                    if user_msg:
                        self._last_graph_error = f"{self._last_graph_error} | {user_msg}"
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
