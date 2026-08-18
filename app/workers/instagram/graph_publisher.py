import logging
import os
import ssl
import asyncio
import time
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
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm")
MediaItem = Tuple[str, str]  # (kind, url) — kind: "photo" | "video"


def _is_video_item(item: MediaItem) -> bool:
    kind, url = item
    if kind == "video":
        return True
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _VIDEO_EXTENSIONS)


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
        self.video_ready_timeout_seconds = int(
            os.getenv("INSTAGRAM_GRAPH_VIDEO_TIMEOUT_SECONDS", "180")
        )
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
            merged_items, _slot_audit = await self._assemble_merged_media_items(post)

            media_candidates: List[Tuple[str, List[MediaItem]]] = []
            if merged_items:
                media_candidates.append(("merged", merged_items))
            for source_name, source_items in await self._collect_media_candidates(post):
                if source_name == "merged":
                    continue
                media_candidates.append((source_name, source_items))

            if not media_candidates:
                self._log_error(db, post_id, "Нет доступных медиафайлов для Graph API")
                return False

            creation_id = None
            used_source = None
            for source_name, media_items in media_candidates:
                self._uri_reject_seen = False
                logger.info(
                    "IG media try post_id=%s source=%s count=%s photos=%s videos=%s",
                    post_id,
                    source_name,
                    len(media_items),
                    sum(1 for kind, _ in media_items if kind == "photo"),
                    sum(1 for kind, _ in media_items if kind == "video"),
                )
                creation_id = await self._create_media_container(media_items, caption)
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
                "IG container ok post_id=%s source=%s creation_id=%s items=%s",
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
                f"Пост опубликован через Instagram Graph API (source={used_source}, items={self._last_children_count})",
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

    async def _collect_media_candidates(self, post: Any) -> List[Tuple[str, List[MediaItem]]]:
        """Полные наборы по источнику (fallback): Telegram → local → VK wall."""
        candidates: List[Tuple[str, List[MediaItem]]] = []

        telegram_items = await self._get_telegram_media_items(post)
        if telegram_items:
            candidates.append(("telegram", telegram_items[:IG_CAROUSEL_MAX_ITEMS]))

        local_items = await self._get_local_media_items(post)
        if local_items:
            candidates.append(("local", local_items[:IG_CAROUSEL_MAX_ITEMS]))

        vk_photo_urls = await self._get_vk_wall_photo_urls(post)
        if vk_photo_urls:
            vk_items = [("photo", url) for url in vk_photo_urls[:IG_CAROUSEL_MAX_ITEMS]]
            candidates.append(("vk", vk_items))

        return candidates

    async def _assemble_merged_media_items(
        self, post: Any
    ) -> Tuple[List[MediaItem], List[dict]]:
        """Собирает медиагруппу по слотам: Telegram → local → VK wall → VK market."""
        items: List[MediaItem] = []
        slot_audit: List[dict] = []

        vk_wall_urls = await self._get_vk_wall_photo_urls(post)
        vk_product_id = self._get_vk_product_id_for_post(post.id)
        vk_market_urls = (
            await self._get_vk_market_photo_urls(vk_product_id) if vk_product_id else []
        )

        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for index, file_id in enumerate(post.photos or []):
                if len(items) >= IG_CAROUSEL_MAX_ITEMS:
                    break
                resolved: Optional[MediaItem] = None
                source = "missing"
                tg_error = None

                if bot_token:
                    tg_url, tg_error = await self._get_telegram_file_url_detailed(
                        session, bot_token, file_id
                    )
                    if tg_url:
                        resolved = ("photo", tg_url)
                        source = "telegram"

                if not resolved:
                    local_url = await self._resolve_local_photo_url(post, index)
                    if local_url:
                        resolved = ("photo", local_url)
                        source = "local"

                if not resolved and index < len(vk_wall_urls):
                    resolved = ("photo", vk_wall_urls[index])
                    source = "vk_wall"

                if not resolved and index < len(vk_market_urls):
                    resolved = ("photo", vk_market_urls[index])
                    source = "vk_market"

                slot_audit.append(
                    {
                        "slot": index,
                        "kind": "photo",
                        "source": source,
                        "tg_error": tg_error,
                        "resolved": bool(resolved),
                    }
                )
                if resolved:
                    items.append(resolved)

            for index, file_id in enumerate(post.videos or []):
                if len(items) >= IG_CAROUSEL_MAX_ITEMS:
                    break
                resolved = None
                source = "missing"
                tg_error = None

                if bot_token:
                    tg_url, tg_error = await self._get_telegram_file_url_detailed(
                        session, bot_token, file_id
                    )
                    if tg_url:
                        resolved = ("video", tg_url)
                        source = "telegram"

                if not resolved:
                    local_url = await self._resolve_local_video_url(post, index)
                    if local_url:
                        resolved = ("video", local_url)
                        source = "local"

                slot_audit.append(
                    {
                        "slot": index,
                        "kind": "video",
                        "source": source,
                        "tg_error": tg_error,
                        "resolved": bool(resolved),
                    }
                )
                if resolved:
                    items.append(resolved)

        logger.info(
            "IG merged assembly post_id=%s photos=%s/%s videos=%s/%s total=%s audit=%s",
            post.id,
            sum(1 for k, _ in items if k == "photo"),
            len(post.photos or []),
            sum(1 for k, _ in items if k == "video"),
            len(post.videos or []),
            len(items),
            slot_audit,
        )
        return items, slot_audit

    def _get_vk_product_id_for_post(self, post_id: str) -> Optional[int]:
        from sqlalchemy import text

        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT vk_product_id FROM products WHERE post_id = :id LIMIT 1"),
                {"id": post_id},
            ).fetchone()
            if row and row[0]:
                return int(row[0])
        except Exception as exc:
            logger.warning("IG vk_product_id lookup failed for %s: %s", post_id, exc)
        finally:
            db.close()
        return None

    async def _get_vk_market_photo_urls(self, vk_product_id: int) -> List[str]:
        try:
            from app.utils.vk_client import resolved_vk_group_id_int
            from app.utils.vk_urls import api_method_url

            token = community_token()
            if not token:
                return []
            gid = resolved_vk_group_id_int()
            params = {
                "item_ids": f"-{gid}_{int(vk_product_id)}",
                "extended": 1,
                "access_token": token,
                "v": self.vk_api_version,
            }
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_method_url("market.getById"), params=params) as resp:
                    payload = await resp.json(content_type=None)
            if payload.get("error"):
                logger.warning("IG market.getById error: %s", payload.get("error"))
                return []
            market_items = (payload.get("response") or {}).get("items") or []
            if not market_items:
                return []
            urls: List[str] = []
            for photo in market_items[0].get("photos") or []:
                best = self._pick_largest_vk_photo_url(photo.get("sizes") or [])
                if best:
                    urls.append(best)
            return urls
        except Exception as exc:
            logger.warning("IG VK market photo fetch failed: %s", exc)
            return []

    async def _resolve_local_photo_url(self, post: Any, index: int) -> Optional[str]:
        if not self.media_base_url or not post.storage_path:
            return None
        post_dir = MEDIA_DIR / post.storage_path
        local_path = post_dir / f"photo_{index}.jpg"
        if local_path.exists():
            return self._to_public_url(local_path)
        photos = post.photos or []
        if index < len(photos) and await self._download_telegram_file(photos[index], local_path):
            if local_path.exists():
                return self._to_public_url(local_path)
        return None

    async def _resolve_local_video_url(self, post: Any, index: int) -> Optional[str]:
        if not self.media_base_url or not post.storage_path:
            return None
        post_dir = MEDIA_DIR / post.storage_path
        local_path = self._resolve_local_video_path(post_dir, index)
        if local_path.exists():
            return self._to_public_url(local_path)
        videos = post.videos or []
        if index < len(videos):
            target = post_dir / f"video_{index}.mp4"
            if await self._download_telegram_file(videos[index], target):
                local_path = self._resolve_local_video_path(post_dir, index)
                if local_path.exists():
                    return self._to_public_url(local_path)
        return None

    async def _prepare_media_urls(self, post: Any) -> List[MediaItem]:
        candidates = await self._collect_media_candidates(post)
        return candidates[0][1] if candidates else []

    async def _get_local_media_items(self, post: Any) -> List[MediaItem]:
        if not self.media_base_url:
            logger.warning("MEDIA_BASE_URL не задан и локальные медиа недоступны")
            return []
        if not post.storage_path:
            return []

        post_dir = MEDIA_DIR / post.storage_path
        photos = post.photos or []
        videos = post.videos or []
        media_items: List[MediaItem] = []
        remaining = IG_CAROUSEL_MAX_ITEMS

        for i, file_id in enumerate(photos):
            if remaining <= 0:
                break
            local_path = post_dir / f"photo_{i}.jpg"
            if not local_path.exists():
                await self._download_telegram_file(file_id, local_path)
            if local_path.exists():
                media_items.append(("photo", self._to_public_url(local_path)))
                remaining -= 1

        for i, file_id in enumerate(videos):
            if remaining <= 0:
                break
            local_path = self._resolve_local_video_path(post_dir, i)
            if not local_path.exists():
                await self._download_telegram_file(file_id, post_dir / f"video_{i}.mp4")
                local_path = self._resolve_local_video_path(post_dir, i)
            if local_path.exists():
                media_items.append(("video", self._to_public_url(local_path)))
                remaining -= 1

        return media_items

    def _resolve_local_video_path(self, post_dir: Path, index: int) -> Path:
        for name in (f"video_{index}.mp4", f"video_{index}.mov"):
            candidate = post_dir / name
            if candidate.exists():
                return candidate
        return post_dir / f"video_{index}.mp4"

    async def _get_telegram_media_items(self, post: Any) -> List[MediaItem]:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not bot_token:
            return []

        photos = post.photos or []
        videos = post.videos or []
        if not photos and not videos:
            return []

        items: List[MediaItem] = []
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for file_id in photos:
                if len(items) >= IG_CAROUSEL_MAX_ITEMS:
                    break
                file_url = await self._get_telegram_file_url(session, bot_token, file_id)
                if file_url:
                    items.append(("photo", file_url))

            for file_id in videos:
                if len(items) >= IG_CAROUSEL_MAX_ITEMS:
                    break
                file_url = await self._get_telegram_file_url(session, bot_token, file_id)
                if file_url:
                    items.append(("video", file_url))
        return items

    async def _get_telegram_file_url(
        self,
        session: aiohttp.ClientSession,
        bot_token: str,
        file_id: str,
    ) -> Optional[str]:
        url, _error = await self._get_telegram_file_url_detailed(session, bot_token, file_id)
        return url

    async def _get_telegram_file_url_detailed(
        self,
        session: aiohttp.ClientSession,
        bot_token: str,
        file_id: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        file_info_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
        try:
            async with session.get(file_info_url) as response:
                if response.status != 200:
                    return None, f"http_{response.status}"
                payload = await response.json(content_type=None)
        except Exception as exc:
            return None, str(exc)
        if not payload.get("ok"):
            return None, payload.get("description") or "telegram_getFile_failed"
        file_path = payload.get("result", {}).get("file_path")
        if file_path:
            return f"https://api.telegram.org/file/bot{bot_token}/{file_path}", None
        return None, "missing_file_path"

    def _to_public_url(self, file_path: Path) -> str:
        relative = file_path.relative_to(MEDIA_DIR).as_posix()
        return f"{self.media_base_url}/{relative}"

    async def _create_media_container(self, media_items: List[MediaItem], caption: str) -> Optional[str]:
        media_items = media_items[:IG_CAROUSEL_MAX_ITEMS]
        if not media_items:
            return None

        self._last_children_count = 0

        if len(media_items) == 1:
            item = media_items[0]
            url = item[1]
            if _is_video_item(item):
                child_id = await self._create_video_container(
                    url, is_carousel_item=False, caption=caption
                )
            else:
                child_id = await self._create_container({"image_url": url, "caption": caption})
            if not child_id and self._is_uri_reject_error():
                self._uri_reject_seen = True
            if child_id:
                self._last_children_count = 1
            return child_id

        children: List[str] = []
        valid_items: List[MediaItem] = []
        for idx, item in enumerate(media_items):
            url = item[1]
            is_video = _is_video_item(item)
            if is_video:
                child_id = await self._create_video_container(url, is_carousel_item=True)
            else:
                child_id = await self._create_container(
                    {"image_url": url, "is_carousel_item": "true"}
                )

            if not child_id:
                if self._is_uri_reject_error():
                    self._uri_reject_seen = True
                    logger.warning(
                        "IG URI reject idx=%s/%s code=%s subcode=%s url=%s — abort source for fallback",
                        idx,
                        len(media_items),
                        self._last_graph_error_code,
                        self._last_graph_error_subcode,
                        _redact_url(url),
                    )
                    return None
                if self._is_aspect_ratio_error() and not is_video:
                    logger.warning(
                        "IG skip frame idx=%s code=36003 (aspect) url=%s",
                        idx,
                        _redact_url(url),
                    )
                    continue
                return None

            children.append(child_id)
            valid_items.append(item)

        self._last_children_count = len(children)
        if not children:
            return None

        if len(children) == 1:
            item = valid_items[0]
            url = item[1]
            if _is_video_item(item):
                return await self._create_video_container(
                    url, is_carousel_item=False, caption=caption
                )
            return await self._create_container({"image_url": url, "caption": caption})

        return await self._create_container(
            {
                "media_type": "CAROUSEL",
                "children": ",".join(children),
                "caption": caption,
            }
        )

    async def _create_video_container(
        self,
        video_url: str,
        *,
        is_carousel_item: bool,
        caption: Optional[str] = None,
    ) -> Optional[str]:
        params: dict = {
            "media_type": "VIDEO",
            "video_url": video_url,
        }
        if is_carousel_item:
            params["is_carousel_item"] = "true"
        if caption:
            params["caption"] = caption

        container_id = await self._create_container(params)
        if not container_id:
            return None

        ready = await self._wait_for_container_ready(container_id)
        if not ready:
            logger.error(
                "IG video container not ready id=%s url=%s",
                container_id,
                _redact_url(video_url),
            )
            return None
        return container_id

    async def _wait_for_container_ready(self, container_id: str) -> bool:
        deadline = time.monotonic() + self.video_ready_timeout_seconds
        while time.monotonic() < deadline:
            status = await self._get_creation_status_code(container_id=container_id)
            if status == "FINISHED":
                return True
            if status in ("ERROR", "EXPIRED"):
                logger.error("IG container status=%s id=%s", status, container_id)
                return False
            await asyncio.sleep(2)
        logger.error(
            "IG container ready timeout id=%s after %ss",
            container_id,
            self.video_ready_timeout_seconds,
        )
        return False

    async def _get_vk_wall_photo_urls(self, post: Any) -> List[str]:
        if not post.vk_post_id:
            return []
        token = community_token()
        if not token:
            logger.warning("VK community token не задан, не можем получить фото из VK")
            return []

        from app.utils.vk_urls import api_method_url

        endpoint = api_method_url("wall.getById")
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
                        status_code = await self._get_creation_status_code(
                            creation_id, session=session
                        )
                        await asyncio.sleep(2)
                        continue
                    return False
        return False

    async def _get_creation_status_code(
        self,
        creation_id: Optional[str] = None,
        *,
        session: Optional[aiohttp.ClientSession] = None,
        container_id: Optional[str] = None,
    ) -> Optional[str]:
        target_id = container_id or creation_id
        if not target_id:
            return None

        endpoint = f"{self.base_url}/{target_id}"
        params = {"fields": "status_code", "access_token": self.access_token}
        try:
            if session is not None:
                async with session.get(endpoint, params=params) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400:
                        return None
                    return (payload or {}).get("status_code")

            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as own_session:
                async with own_session.get(endpoint, params=params) as response:
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
