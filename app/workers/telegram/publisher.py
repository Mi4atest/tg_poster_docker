
import logging
import asyncio
from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo
from aiogram.enums import ParseMode  # Изменен импорт ParseMode
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID
from app.db.database import SessionLocal
from app.api.models.post import Post, PublicationLog
from app.utils.text_formatter import format_for_telegram

logger = logging.getLogger(__name__)

class TelegramPublisher:
    """Class for publishing posts to Telegram channel."""

    def __init__(self):
        """Initialize Telegram bot."""
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)

    async def publish_post(self, post_id):
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
            text = format_for_telegram(post.text)

            # Check if post has media
            if post.photos or post.videos:
                # Prepare media group
                media = []

                # Preserve the exact order of media as it was added by the user
                all_media = []

                # Get all photos and videos with their original order
                photos = post.photos
                videos = post.videos

                # Log the media order for debugging
                logger.info(f"Original photos order: {photos}")
                logger.info(f"Original videos order: {videos}")

                # Add all media to the group with caption on the first item
                if len(photos) > 0 or len(videos) > 0:
                    # Add the first item with caption
                    if len(photos) > 0:
                        # First photo gets the caption
                        media.append(InputMediaPhoto(media=photos[0], caption=text, parse_mode=ParseMode.MARKDOWN_V2))
                        # Add remaining photos without caption
                        for file_id in photos[1:]:
                            media.append(InputMediaPhoto(media=file_id))
                        # Add all videos without caption
                        for file_id in videos:
                            media.append(InputMediaVideo(media=file_id))
                    else:
                        # First video gets the caption
                        media.append(InputMediaVideo(media=videos[0], caption=text, parse_mode=ParseMode.MARKDOWN_V2))
                        # Add remaining videos without caption
                        for file_id in videos[1:]:
                            media.append(InputMediaVideo(media=file_id))

                # Send media group in batches of 10 (Telegram limit)
                if len(media) > 0:
                    # Send first batch (up to 10 items)
                    first_batch = media[:min(10, len(media))]
                    logger.info(f"Sending first batch of {len(first_batch)} media items")
                    await self.bot.send_media_group(TELEGRAM_CHANNEL_ID, media=first_batch)

                    # If there are more than 10 media files, send them in additional batches
                    if len(media) > 10:
                        for i in range(10, len(media), 10):
                            batch = media[i:min(i+10, len(media))]
                            if batch:
                                logger.info(f"Sending additional batch of {len(batch)} media items")
                                await self.bot.send_media_group(TELEGRAM_CHANNEL_ID, media=batch)
            else:
                # Send text only
                await self.bot.send_message(TELEGRAM_CHANNEL_ID, text, parse_mode=ParseMode.MARKDOWN_V2)

            # Update post status in database
            post.is_published_telegram = True
            post.published_telegram_at = datetime.now(timezone.utc)

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

async def publish_post_to_telegram(post_id):
    """Publish a post to Telegram channel."""
    publisher = TelegramPublisher()
    return await publisher.publish_post(post_id)
