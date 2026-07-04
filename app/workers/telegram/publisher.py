
import logging
import re
import asyncio
from typing import Optional
from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo
from aiogram.enums import ParseMode
from datetime import datetime, timezone

from sqlalchemy import text

from app.config.settings import (
    TELEGRAM_BOT_TOKEN,
)
from app.db.database import SessionLocal
from app.db.post_queries import fetch_post, insert_publication_log
from app.services.settings_service import get_settings_service
from app.utils.text_formatter import format_for_telegram

logger = logging.getLogger(__name__)


def _parse_retry_seconds(err: Exception) -> int:
    s = str(err)
    m = re.search(r'[Rr]etry in (\d+) seconds', s) or re.search(r'retry after (\d+)', s)
    return int(m.group(1)) if m else 0

class TelegramPublisher:
    """Class for publishing posts to Telegram channel."""

    def __init__(self):
        """Initialize Telegram bot."""
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)

    async def publish_post(self, post_id, signature_enabled: bool = True):
        """Publish a post to Telegram channel."""
        TELEGRAM_CHANNEL_ID = get_settings_service().get_telegram_channel_id()
        db = SessionLocal()
        try:
            post = fetch_post(db, post_id)

            if not post:
                logger.error(f"Post {post_id} not found")
                return False

            if post.is_published_telegram:
                logger.info(f"Post {post_id} already published to Telegram, republishing")

            formatted_text = format_for_telegram(post.text, signature_enabled=signature_enabled)

            channel_username = None
            is_numeric_id = False
            try:
                chat = await self.bot.get_chat(TELEGRAM_CHANNEL_ID)
                channel_username = chat.username
                if not channel_username:
                    is_numeric_id = True
                    channel_id_str = str(chat.id).replace('-100', '')
                    channel_username = channel_id_str
            except Exception as e:
                logger.warning(f"Could not get channel info: {str(e)}, using channel ID")
                channel_id_str = str(TELEGRAM_CHANNEL_ID).replace('@', '').replace('-100', '')
                if channel_id_str.lstrip('-').isdigit():
                    is_numeric_id = True
                    channel_username = channel_id_str.lstrip('-')
                else:
                    channel_username = channel_id_str

            if post.photos or post.videos:
                media = []
                photos = post.photos
                videos = post.videos

                logger.info(f"Original photos order: {photos}")
                logger.info(f"Original videos order: {videos}")

                if len(photos) > 0 or len(videos) > 0:
                    if len(photos) > 0:
                        media.append(InputMediaPhoto(media=photos[0], caption=formatted_text, parse_mode=ParseMode.MARKDOWN_V2))
                        for file_id in photos[1:]:
                            media.append(InputMediaPhoto(media=file_id))
                        for file_id in videos:
                            media.append(InputMediaVideo(media=file_id))
                    else:
                        media.append(InputMediaVideo(media=videos[0], caption=formatted_text, parse_mode=ParseMode.MARKDOWN_V2))
                        for file_id in videos[1:]:
                            media.append(InputMediaVideo(media=file_id))

                message_id = None
                if len(media) > 0:
                    first_batch = media[:min(10, len(media))]
                    logger.info(f"Sending first batch of {len(first_batch)} media items")
                    err = None
                    for attempt in range(3):
                        if attempt > 0 and err is not None:
                            sec = _parse_retry_seconds(err)
                            if sec > 0:
                                logger.info(f"Flood control: waiting {sec}s before retry publish post {post_id}")
                                await asyncio.sleep(sec)
                        try:
                            messages = await self.bot.send_media_group(TELEGRAM_CHANNEL_ID, media=first_batch)
                            if messages and len(messages) > 0:
                                message_id = messages[0].message_id
                            break
                        except Exception as e:
                            err = e
                            if _parse_retry_seconds(e) == 0 or attempt >= 2:
                                raise
                    if message_id and len(media) > 10:
                        for i in range(10, len(media), 10):
                            batch = media[i:min(i + 10, len(media))]
                            if batch:
                                logger.info(f"Sending additional batch of {len(batch)} media items")
                                await asyncio.sleep(1)
                                await self.bot.send_media_group(TELEGRAM_CHANNEL_ID, media=batch)
            else:
                err = None
                for attempt in range(3):
                    if attempt > 0 and err is not None:
                        sec = _parse_retry_seconds(err)
                        if sec > 0:
                            logger.info(f"Flood control: waiting {sec}s before retry publish post {post_id}")
                            await asyncio.sleep(sec)
                    try:
                        message = await self.bot.send_message(
                            TELEGRAM_CHANNEL_ID,
                            formatted_text,
                            parse_mode=ParseMode.MARKDOWN_V2,
                            disable_web_page_preview=True,
                        )
                        message_id = message.message_id
                        break
                    except Exception as e:
                        err = e
                        if _parse_retry_seconds(e) == 0 or attempt >= 2:
                            raise

            now = datetime.now(timezone.utc)
            telegram_link = None
            if message_id and channel_username:
                if is_numeric_id:
                    telegram_link = f"https://t.me/c/{channel_username}/{message_id}"
                else:
                    telegram_link = f"https://t.me/{channel_username}/{message_id}"
                logger.info(f"Saved Telegram link for post {post_id}: {telegram_link}")
            else:
                logger.warning(
                    f"Could not generate Telegram link for post {post_id}: "
                    f"message_id={message_id}, channel_username={channel_username}"
                )

            db.execute(
                text(
                    "UPDATE posts SET is_published_telegram = true, published_telegram_at = :now, "
                    "telegram_link = COALESCE(:link, telegram_link), updated_at = NOW() "
                    "WHERE id = :id"
                ),
                {"id": post_id, "now": now, "link": telegram_link},
            )
            db.commit()

            if telegram_link:
                try:
                    db.execute(
                        text(
                            "UPDATE products SET telegram_link = :link WHERE post_id = :post_id"
                        ),
                        {"link": telegram_link, "post_id": post_id},
                    )
                    db.commit()
                except Exception as sync_err:
                    logger.warning(
                        f"Failed to sync telegram_link to products for post {post_id}: {sync_err}"
                    )
                    db.rollback()

            insert_publication_log(db, post_id, "telegram", "success", "Published to Telegram")
            db.commit()

            logger.info(f"Post {post_id} published to Telegram successfully")
            try:
                from app.bot.utils.used_products_channel_updater import update_used_products_list_in_channel
                await update_used_products_list_in_channel(self.bot)
            except Exception as upd_err:
                logger.warning("Failed to update used products list in channel: %s", upd_err)
            return True
        except Exception as e:
            logger.error(f"Error publishing post {post_id} to Telegram: {str(e)}")

            insert_publication_log(db, post_id, "telegram", "error", str(e))
            db.commit()

            return False
        finally:
            db.close()
            await self.bot.session.close()

async def publish_post_to_telegram(post_id, signature_enabled: bool = True):
    """Publish a post to Telegram channel."""
    publisher = TelegramPublisher()
    return await publisher.publish_post(post_id, signature_enabled=signature_enabled)
