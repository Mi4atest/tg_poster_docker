
import logging
from typing import Optional
from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo
from aiogram.enums import ParseMode  # Изменен импорт ParseMode
from datetime import datetime, timezone

from app.config.settings import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_ID,
)
from app.db.database import SessionLocal
from app.api.models.post import Post, PublicationLog
from app.utils.text_formatter import format_for_telegram

logger = logging.getLogger(__name__)

class TelegramPublisher:
    """Class for publishing posts to Telegram channel."""

    def __init__(self):
        """Initialize Telegram bot."""
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)

    async def publish_post(self, post_id, signature_enabled: bool = True):
        """Publish a post to Telegram channel."""
        db = SessionLocal()
        try:
            # Get post from database
            post = db.query(Post).filter(Post.id == post_id).first()

            if not post:
                logger.error(f"Post {post_id} not found")
                return False

            # Log if already published, but continue with republishing
            if post.is_published_telegram:
                logger.info(f"Post {post_id} already published to Telegram, republishing")

            # Get post text and format it
            text = format_for_telegram(post.text, signature_enabled=signature_enabled)

            # Get channel username for link generation
            channel_username = None
            is_numeric_id = False
            try:
                chat = await self.bot.get_chat(TELEGRAM_CHANNEL_ID)
                channel_username = chat.username
                if not channel_username:
                    # Channel has no username, use numeric ID format
                    is_numeric_id = True
                    # Extract numeric ID (remove -100 prefix if present)
                    channel_id_str = str(chat.id).replace('-100', '')
                    channel_username = channel_id_str
            except Exception as e:
                logger.warning(f"Could not get channel info: {str(e)}, using channel ID")
                # Fallback: try to extract from TELEGRAM_CHANNEL_ID
                channel_id_str = str(TELEGRAM_CHANNEL_ID).replace('@', '').replace('-100', '')
                # Check if it's numeric (channel ID) or alphanumeric (username)
                if channel_id_str.lstrip('-').isdigit():
                    is_numeric_id = True
                    channel_username = channel_id_str.lstrip('-')
                else:
                    channel_username = channel_id_str

            # Check if post has media
            if post.photos or post.videos:
                # Prepare media group
                media = []

                photos = post.photos
                videos = post.videos

                # Log the media order for debugging
                logger.info(f"Original photos order: {photos}")
                logger.info(f"Original videos order: {videos}")

                # Add all media to the group with caption on the first item
                if len(photos) > 0 or len(videos) > 0:
                    if len(photos) > 0:
                        media.append(InputMediaPhoto(media=photos[0], caption=text, parse_mode=ParseMode.MARKDOWN_V2))
                        for file_id in photos[1:]:
                            media.append(InputMediaPhoto(media=file_id))
                        for file_id in videos:
                            media.append(InputMediaVideo(media=file_id))
                    else:
                        media.append(InputMediaVideo(media=videos[0], caption=text, parse_mode=ParseMode.MARKDOWN_V2))
                        for file_id in videos[1:]:
                            media.append(InputMediaVideo(media=file_id))

                # sendMediaGroup requires 2-10 items; a single photo/video must use
                # send_photo / send_video or Telegram rejects with HTTP 400.
                message_id = None
                if len(media) == 1:
                    item = media[0]
                    logger.info("Sending single media item (not a media group)")
                    if isinstance(item, InputMediaPhoto):
                        message = await self.bot.send_photo(
                            TELEGRAM_CHANNEL_ID,
                            item.media,
                            caption=item.caption,
                            parse_mode=item.parse_mode,
                        )
                    else:
                        message = await self.bot.send_video(
                            TELEGRAM_CHANNEL_ID,
                            item.media,
                            caption=item.caption,
                            parse_mode=item.parse_mode,
                        )
                    message_id = message.message_id
                elif len(media) > 1:
                    # Send media group in batches of 10 (Telegram limit)
                    first_batch = media[:min(10, len(media))]
                    logger.info(f"Sending first batch of {len(first_batch)} media items")
                    messages = await self.bot.send_media_group(TELEGRAM_CHANNEL_ID, media=first_batch)
                    # Get message_id from first message in the group
                    if messages and len(messages) > 0:
                        message_id = messages[0].message_id

                    if len(media) > 10:
                        for i in range(10, len(media), 10):
                            batch = media[i:min(i + 10, len(media))]
                            if batch:
                                logger.info(f"Sending additional batch of {len(batch)} media items")
                                await self.bot.send_media_group(TELEGRAM_CHANNEL_ID, media=batch)
            else:
                # Send text only
                message = await self.bot.send_message(
                    TELEGRAM_CHANNEL_ID,
                    text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_web_page_preview=True,
                )
                message_id = message.message_id

            # Update post status in database
            post.is_published_telegram = True
            post.published_telegram_at = datetime.now(timezone.utc)
            
            # Save Telegram post link
            if message_id and channel_username:
                if is_numeric_id:
                    # For channels with numeric ID, use /c/ format
                    post.telegram_link = f"https://t.me/c/{channel_username}/{message_id}"
                else:
                    # For channels with username, use standard format
                    post.telegram_link = f"https://t.me/{channel_username}/{message_id}"
                logger.info(f"Saved Telegram link for post {post_id}: {post.telegram_link}")
            else:
                logger.warning(f"Could not generate Telegram link for post {post_id}: message_id={message_id}, channel_username={channel_username}")

            # Add publication log
            log = PublicationLog(
                post_id=post.id,
                platform="telegram",
                status="success",
                message="Published to Telegram"
            )
            db.add(log)

            db.commit()

            logger.info(f"Post {post_id} published to Telegram successfully")
            return True
        except Exception as e:
            logger.error(f"Error publishing post {post_id} to Telegram: {str(e)}")

            # Add error log
            log = PublicationLog(
                post_id=post_id,
                platform="telegram",
                status="error",
                message=str(e)
            )
            db.add(log)
            db.commit()

            return False
        finally:
            db.close()
            await self.bot.session.close()

async def publish_post_to_telegram(post_id, signature_enabled: bool = True):
    """Publish a post to Telegram channel."""
    publisher = TelegramPublisher()
    return await publisher.publish_post(post_id, signature_enabled=signature_enabled)
