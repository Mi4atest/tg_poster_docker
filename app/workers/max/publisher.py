import logging
from datetime import datetime, timezone
import asyncio

import aiohttp

from sqlalchemy import text

from app.api.models.post import PublicationLog
from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN, MEDIA_DIR, TELEGRAM_BOT_TOKEN
from app.db.database import SessionLocal
from app.db.post_queries import fetch_post, fetch_product_row_by_post_id, insert_publication_log
from app.services.settings_service import get_settings_service
from app.integrations.max.client import MaxApiClient, extract_message_id
from app.utils.text_formatter import format_for_max, format_for_max_plain
from app.utils.max_share_link import resolve_max_channel_share_url


logger = logging.getLogger(__name__)


class MaxPublisher:
    async def publish_post(self, post_id: str, signature_enabled: bool = True) -> bool:
        service = get_settings_service()
        max_token = (service.get_secret("max_bot_token") or MAX_BOT_TOKEN or "").strip()
        max_channel_id = (service.get_max_channel_id() or "").strip()
        db = SessionLocal()
        try:
            if not max_token or not max_channel_id:
                raise RuntimeError("MAX_BOT_TOKEN или MAX_CHANNEL_ID не заданы")
            client = MaxApiClient(max_token, MAX_API_BASE_URL)
            await client.get_me()
            await client.get_chat(max_channel_id)

            post = fetch_post(db, post_id)
            if not post:
                logger.error("Post %s not found", post_id)
                return False
            product_row = fetch_product_row_by_post_id(db, post_id) or {}
            vk_product_id = product_row.get("vk_product_id")
            storage_path = getattr(post, "storage_path", None)
            try:
                db.rollback()
            except Exception:
                pass

            formatted_text = format_for_max(post.text or "", signature_enabled=signature_enabled)
            plain_text = format_for_max_plain(post.text or "", signature_enabled=signature_enabled)
            parse_mode = "MarkdownV2"
            message_id = None

            if post.photos or post.videos:
                media: list[dict] = []
                photos = post.photos or []
                videos = post.videos or []
                if photos:
                    media.append({"type": "image", "media": photos[0], "caption": formatted_text, "parse_mode": parse_mode})
                    media.extend({"type": "image", "media": item} for item in photos[1:])
                    media.extend({"type": "video", "media": item} for item in videos)
                elif videos:
                    media.append({"type": "video", "media": videos[0], "caption": formatted_text, "parse_mode": parse_mode})
                    media.extend({"type": "video", "media": item} for item in videos[1:])

                prepared_media: list[dict] = []
                media_sources: list[dict] = []
                expected_count = sum(1 for item in media if item.get("media"))
                photo_i = 0
                video_i = 0
                for idx, item in enumerate(media):
                    file_id = item.get("media")
                    m_type = item.get("type")
                    if not file_id:
                        continue
                    if m_type == "image":
                        local_idx = photo_i
                        photo_i += 1
                    else:
                        local_idx = video_i
                        video_i += 1
                    file_bytes, filename, source = await self._load_media_bytes(
                        file_id,
                        m_type,
                        idx,
                        local_idx=local_idx,
                        vk_product_id=vk_product_id,
                        storage_path=storage_path,
                    )
                    media_sources.append(
                        {
                            "idx": idx,
                            "local_idx": local_idx,
                            "type": m_type,
                            "source": source,
                            "bytes": len(file_bytes or b""),
                        }
                    )
                    upload_resp = await client.create_upload("video" if m_type == "video" else "image")
                    upload_url = upload_resp.get("url")
                    if not upload_url:
                        raise RuntimeError(f"Max upload URL missing for media type={m_type}")
                    upload_result = await client.upload_binary(upload_url, file_bytes, filename)
                    if m_type == "video":
                        token = upload_resp.get("token") or upload_result.get("token")
                        payload = {"token": token} if token else upload_result
                    else:
                        payload = upload_result
                    prepared_item = {"type": m_type, "payload": payload}
                    if not prepared_media:
                        prepared_item["caption"] = formatted_text
                        prepared_item["parse_mode"] = parse_mode
                    prepared_media.append(prepared_item)
                logger.info(
                    "Max media for %s: prepared=%s/%s sources=%s",
                    post_id,
                    len(prepared_media),
                    expected_count,
                    media_sources,
                )
                if len(prepared_media) != expected_count:
                    raise RuntimeError(
                        "Max: неполная медиагруппа, в канал не отправляем "
                        f"({len(prepared_media)}/{expected_count})"
                    )

                try:
                    resp = await client.send_media_group(max_channel_id, prepared_media)
                except Exception:
                    prepared_media[0].pop("parse_mode", None)
                    prepared_media[0]["caption"] = plain_text
                    last_exc = None
                    for attempt in range(3):
                        try:
                            resp = await client.send_media_group(max_channel_id, prepared_media)
                            break
                        except Exception as exc:
                            last_exc = exc
                            if "attachment.not.ready" in str(exc):
                                await asyncio.sleep(2 + attempt * 2)
                                continue
                            raise
                    else:
                        raise last_exc
                message_id = extract_message_id(resp)
            else:
                try:
                    resp = await client.send_message(
                        max_channel_id,
                        formatted_text,
                        parse_mode=parse_mode,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    resp = await client.send_message(
                        max_channel_id,
                        plain_text,
                        parse_mode=None,
                        disable_web_page_preview=True,
                    )
                message_id = extract_message_id(resp)

            now = datetime.now(timezone.utc)
            max_link = None
            max_share_url = None
            if message_id:
                max_link = f"max://channel/{max_channel_id}/{message_id}"
                payloads: list = [resp]
                try:
                    info = await client.get_message(str(message_id))
                    payloads.append(info)
                except Exception as fetch_err:
                    logger.warning(
                        "Max: не удалось GET /messages/%s для публичного url (post_id=%s): %s",
                        message_id,
                        post_id,
                        fetch_err,
                    )
                share_url = resolve_max_channel_share_url(str(max_channel_id), *payloads)
                if share_url:
                    max_share_url = share_url
                try:
                    db.execute(
                        text(
                            "UPDATE products SET max_link = COALESCE(:max_link, max_link), "
                            "max_share_url = COALESCE(:max_share_url, max_share_url) "
                            "WHERE post_id = :post_id"
                        ),
                        {
                            "post_id": post_id,
                            "max_link": max_link,
                            "max_share_url": max_share_url,
                        },
                    )
                except Exception as sync_err:
                    logger.warning(
                        "Failed to sync max_link to products for post %s: %s", post_id, sync_err
                    )
                if not share_url:
                    logger.info(
                        "Max publish: публичный url не найден (post_id=%s), в Telegram — fallback https://max.ru/c/...",
                        post_id,
                    )

            db.execute(
                text(
                    "UPDATE posts SET is_published_max = true, published_max_at = :now, "
                    "max_link = COALESCE(:max_link, max_link), "
                    "max_share_url = COALESCE(:max_share_url, max_share_url), updated_at = NOW() "
                    "WHERE id = :id"
                ),
                {
                    "id": post_id,
                    "now": now,
                    "max_link": max_link,
                    "max_share_url": max_share_url,
                },
            )
            insert_publication_log(db, post_id, "max", "success", "Published to Max")
            db.commit()
            try:
                from app.bot.utils.used_products_max_channel_updater import (
                    update_used_products_list_in_max_channel,
                )

                await update_used_products_list_in_max_channel()
            except Exception as upd_err:
                logger.warning("Failed to update used products list in Max channel: %s", upd_err)
            return True
        except Exception as exc:
            logger.error("Error publishing post %s to Max: %s", post_id, exc)
            try:
                insert_publication_log(db, post_id, "max", "error", str(exc))
                db.commit()
            except Exception as log_err:
                logger.warning(
                    "Max: failed to write publication_log for %s: %s", post_id, log_err
                )
                try:
                    db.rollback()
                except Exception:
                    pass
                db2 = SessionLocal()
                try:
                    insert_publication_log(db2, post_id, "max", "error", str(exc))
                    db2.commit()
                except Exception:
                    try:
                        db2.rollback()
                    except Exception:
                        pass
                finally:
                    db2.close()
            return False
        finally:
            db.close()

    async def _load_media_bytes(
        self,
        file_id: str,
        media_type: str,
        idx: int,
        *,
        local_idx: int,
        vk_product_id,
        storage_path,
    ) -> tuple[bytes, str, str]:
        try:
            content, filename = await self._download_telegram_media(file_id, media_type)
            return content, filename, "telegram"
        except Exception as tg_err:
            logger.warning(
                "Max: Telegram media failed idx=%s local_idx=%s type=%s: %s",
                idx,
                local_idx,
                media_type,
                tg_err,
            )
            local, local_name = self._read_local_media(storage_path, media_type, local_idx)
            if local:
                logger.info(
                    "Max: media idx=%s type=%s recovered from local %s (%s bytes)",
                    idx,
                    media_type,
                    local_name,
                    len(local),
                )
                return local, local_name or f"local_{local_idx}", "local"
            if media_type == "image":
                vk_url = await self._vk_market_photo_url(vk_product_id, local_idx)
                if vk_url:
                    content = await self._download_http_bytes(vk_url)
                    logger.info(
                        "Max: photo local_idx=%s recovered from VK market last-resort (%s bytes)",
                        local_idx,
                        len(content),
                    )
                    return content, f"vk_market_{local_idx}.jpg", "vk_market"
            raise RuntimeError(
                f"Max: media idx={idx} type={media_type} Telegram failed and no fallback: {tg_err}"
            ) from tg_err

    def _read_local_media(
        self, storage_path, media_type: str, local_idx: int
    ) -> tuple[bytes | None, str | None]:
        if not storage_path:
            return None, None
        post_dir = MEDIA_DIR / str(storage_path)
        if media_type == "image":
            names = (
                f"photo_{local_idx}.jpg",
                f"photo_{local_idx}.jpeg",
                f"photo_{local_idx}.png",
                f"photo_{local_idx}.webp",
            )
        else:
            names = (f"video_{local_idx}.mp4", f"video_{local_idx}.mov")
        for name in names:
            path = post_dir / name
            try:
                if path.is_file() and path.stat().st_size > 0:
                    return path.read_bytes(), name
            except Exception:
                continue
        return None, None

    async def _vk_market_photo_url(self, vk_product_id, idx: int) -> str | None:
        urls = await self._vk_market_photo_urls(vk_product_id)
        if 0 <= idx < len(urls):
            return urls[idx]
        return None

    async def _vk_market_photo_urls(self, vk_product_id) -> list[str]:
        cached = getattr(self, "_vk_market_photo_urls_cache", None)
        if cached is not None:
            return cached
        self._vk_market_photo_urls_cache = []
        if not vk_product_id:
            return self._vk_market_photo_urls_cache
        try:
            from app.utils.vk_client import community_token, resolved_vk_group_id_int
            from app.utils.vk_urls import api_method_url

            token = community_token()
            if not token:
                return self._vk_market_photo_urls_cache
            gid = resolved_vk_group_id_int()
            item_ids = f"-{gid}_{int(vk_product_id)}"
            params = {
                "item_ids": item_ids,
                "extended": 1,
                "access_token": token,
                "v": "5.199",
            }
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_method_url("market.getById"), params=params) as resp:
                    payload = await resp.json(content_type=None)
            if payload.get("error"):
                logger.warning("Max VK market.getById error: %s", payload.get("error"))
                return self._vk_market_photo_urls_cache
            items = (payload.get("response") or {}).get("items") or []
            if not items:
                return self._vk_market_photo_urls_cache
            urls: list[str] = []
            for photo in items[0].get("photos") or []:
                url = self._pick_largest_vk_photo_url(photo.get("sizes") or [])
                if url:
                    urls.append(url)
            self._vk_market_photo_urls_cache = urls
        except Exception as exc:
            logger.warning("Max VK market photo fallback failed: %s", exc)
        return self._vk_market_photo_urls_cache

    @staticmethod
    def _pick_largest_vk_photo_url(sizes: list) -> str | None:
        best_url = None
        best_area = -1
        for size in sizes:
            url = size.get("url") if isinstance(size, dict) else None
            if not url:
                continue
            area = int(size.get("width") or 0) * int(size.get("height") or 0)
            if area > best_area:
                best_area = area
                best_url = url
        return best_url

    async def _download_http_bytes(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"VK photo download failed status={resp.status}")
                content = await resp.read()
        if not content:
            raise RuntimeError("VK photo download empty")
        return content

    async def _download_telegram_media(self, file_id: str, media_type: str) -> tuple[bytes, str]:
        if not TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required for Max media relay")
        get_file_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile"
        async with aiohttp.ClientSession() as session:
            async with session.get(get_file_url, params={"file_id": file_id}, timeout=45) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400 or not data.get("ok"):
                    raise RuntimeError(f"Telegram getFile failed for media relay: {data}")
                file_path = data.get("result", {}).get("file_path")
                if not file_path:
                    raise RuntimeError("Telegram file_path is missing")
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            async with session.get(download_url, timeout=120) as download_resp:
                if download_resp.status >= 400:
                    raise RuntimeError(f"Telegram media download failed status={download_resp.status}")
                content = await download_resp.read()
        ext = "mp4" if media_type == "video" else "jpg"
        filename = f"{file_id}.{ext}"
        return content, filename

    @staticmethod
    def _extract_message_id(resp: dict | None) -> str | None:
        return extract_message_id(resp)


async def publish_post_to_max(post_id: str, signature_enabled: bool = True) -> bool:
    publisher = MaxPublisher()
    return await publisher.publish_post(post_id, signature_enabled=signature_enabled)
