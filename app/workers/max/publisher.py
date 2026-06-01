import logging
from datetime import datetime, timezone
import asyncio

import aiohttp

from app.api.models.post import Post, PublicationLog
from app.config.settings import MAX_API_BASE_URL, MAX_BOT_TOKEN, TELEGRAM_BOT_TOKEN
from app.db.database import SessionLocal
from app.services.settings_service import get_settings_service
from app.integrations.max.client import MaxApiClient
from app.utils.text_formatter import format_for_max, format_for_max_plain
from app.utils.max_share_link import resolve_max_channel_share_url


logger = logging.getLogger(__name__)


class MaxPublisher:
    def __init__(self):
        self.client = MaxApiClient(MAX_BOT_TOKEN, MAX_API_BASE_URL)

    async def publish_post(self, post_id: str, signature_enabled: bool = True) -> bool:
        MAX_CHANNEL_ID = get_settings_service().get_max_channel_id()
        db = SessionLocal()
        try:
            if not MAX_BOT_TOKEN or not MAX_CHANNEL_ID:
                raise RuntimeError("MAX_BOT_TOKEN или MAX_CHANNEL_ID не заданы")
            await self.client.get_me()
            await self.client.get_chat(MAX_CHANNEL_ID)

            post = db.query(Post).filter(Post.id == post_id).first()
            if not post:
                logger.error("Post %s not found", post_id)
                return False

            text = format_for_max(post.text or "", signature_enabled=signature_enabled)
            plain_text = format_for_max_plain(post.text or "", signature_enabled=signature_enabled)
            parse_mode = "MarkdownV2"
            message_id = None

            if post.photos or post.videos:
                media: list[dict] = []
                photos = post.photos or []
                videos = post.videos or []
                if photos:
                    media.append({"type": "image", "media": photos[0], "caption": text, "parse_mode": parse_mode})
                    media.extend({"type": "image", "media": item} for item in photos[1:])
                    media.extend({"type": "video", "media": item} for item in videos)
                elif videos:
                    media.append({"type": "video", "media": videos[0], "caption": text, "parse_mode": parse_mode})
                    media.extend({"type": "video", "media": item} for item in videos[1:])

                prepared_media: list[dict] = []
                for idx, item in enumerate(media):
                    file_id = item.get("media")
                    m_type = item.get("type")
                    if not file_id:
                        continue
                    file_bytes, filename = await self._download_telegram_media(file_id, m_type)
                    upload_resp = await self.client.create_upload("video" if m_type == "video" else "image")
                    upload_url = upload_resp.get("url")
                    if not upload_url:
                        raise RuntimeError(f"Max upload URL missing for media type={m_type}")
                    upload_result = await self.client.upload_binary(upload_url, file_bytes, filename)
                    if m_type == "video":
                        token = upload_resp.get("token") or upload_result.get("token")
                        payload = {"token": token} if token else upload_result
                    else:
                        payload = upload_result
                    prepared_item = {"type": m_type, "payload": payload}
                    if idx == 0:
                        prepared_item["caption"] = text
                        prepared_item["parse_mode"] = parse_mode
                    prepared_media.append(prepared_item)

                try:
                    resp = await self.client.send_media_group(MAX_CHANNEL_ID, prepared_media)
                except Exception:
                    prepared_media[0].pop("parse_mode", None)
                    prepared_media[0]["caption"] = plain_text
                    last_exc = None
                    for attempt in range(3):
                        try:
                            resp = await self.client.send_media_group(MAX_CHANNEL_ID, prepared_media)
                            break
                        except Exception as exc:
                            last_exc = exc
                            if "attachment.not.ready" in str(exc):
                                await asyncio.sleep(2 + attempt * 2)
                                continue
                            raise
                    else:
                        raise last_exc
                message_id = self._extract_message_id(resp)
            else:
                try:
                    resp = await self.client.send_message(
                        MAX_CHANNEL_ID,
                        text,
                        parse_mode=parse_mode,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    resp = await self.client.send_message(
                        MAX_CHANNEL_ID,
                        plain_text,
                        parse_mode=None,
                        disable_web_page_preview=True,
                    )
                message_id = self._extract_message_id(resp)

            post.is_published_max = True
            post.published_max_at = datetime.now(timezone.utc)
            if message_id:
                post.max_link = f"max://channel/{MAX_CHANNEL_ID}/{message_id}"
                payloads: list = [resp]
                try:
                    info = await self.client.get_message(str(message_id))
                    payloads.append(info)
                except Exception as fetch_err:
                    logger.warning(
                        "Max: не удалось GET /messages/%s для публичного url (post_id=%s): %s",
                        message_id,
                        post_id,
                        fetch_err,
                    )
                share_url = resolve_max_channel_share_url(str(MAX_CHANNEL_ID), *payloads)
                if share_url:
                    post.max_share_url = share_url
                try:
                    from app.api.models.product import Product

                    products_for_post = db.query(Product).filter(Product.post_id == post_id).all()
                    for product in products_for_post:
                        product.max_link = post.max_link
                        if share_url:
                            product.max_share_url = share_url
                except Exception as sync_err:
                    logger.warning("Failed to sync max_link to products for post %s: %s", post_id, sync_err)
                if not share_url:
                    logger.info(
                        "Max publish: публичный url не найден (post_id=%s), в Telegram — fallback https://max.ru/c/...",
                        post_id,
                    )

            db.add(
                PublicationLog(
                    post_id=post.id,
                    platform="max",
                    status="success",
                    message="Published to Max",
                )
            )
            db.commit()
            return True
        except Exception as exc:
            logger.error("Error publishing post %s to Max: %s", post_id, exc)
            db.add(
                PublicationLog(
                    post_id=post_id,
                    platform="max",
                    status="error",
                    message=str(exc),
                )
            )
            db.commit()
            return False
        finally:
            db.close()

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
        if not isinstance(resp, dict):
            return None
        # Встречающиеся варианты ответа Max API:
        # 1) {"result":{"message_id":"..."}}
        # 2) {"message":{"message_id":"..."}}
        # 3) {"message_id":"..."} или {"id":"..."}
        result = resp.get("result")
        if isinstance(result, dict):
            mid = result.get("message_id") or result.get("id")
            if mid:
                return str(mid)
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, dict):
                mid = first.get("message_id") or first.get("id")
                if mid:
                    return str(mid)
        msg = resp.get("message")
        if isinstance(msg, dict):
            mid = msg.get("message_id") or msg.get("id")
            if mid:
                return str(mid)
            body = msg.get("body")
            if isinstance(body, dict):
                nested_mid = body.get("mid") or body.get("message_id") or body.get("id")
                if nested_mid:
                    return str(nested_mid)
        top_mid = resp.get("message_id") or resp.get("id")
        return str(top_mid) if top_mid else None


async def publish_post_to_max(post_id: str, signature_enabled: bool = True) -> bool:
    publisher = MaxPublisher()
    return await publisher.publish_post(post_id, signature_enabled=signature_enabled)
