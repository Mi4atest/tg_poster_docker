import logging
import asyncio
from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputFile
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import io
from PIL import Image, ImageDraw, ImageFont

from app.config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID
from app.db.database import SessionLocal
from app.api.models.story import Story, StoryPublicationLog

logger = logging.getLogger(__name__)

class TelegramStoryPublisher:
    """Class for publishing stories to Telegram channel."""

    def __init__(self):
        """Initialize Telegram bot."""
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)

    async def create_story_image(self, file_id, model_name, price):
        """Create a story image with model name and price overlay."""
        try:
            # Download the file from Telegram
            file = await self.bot.get_file(file_id)
            file_path = file.file_path
            file_content = await self.bot.download_file(file_path)

            # Open the image
            image = Image.open(io.BytesIO(file_content))

            # Resize image to story format (9:16)
            width, height = image.size
            target_ratio = 9 / 16
            current_ratio = width / height

            if current_ratio > target_ratio:
                # Image is too wide, crop width
                new_width = int(height * target_ratio)
                left = (width - new_width) // 2
                image = image.crop((left, 0, left + new_width, height))
            elif current_ratio < target_ratio:
                # Image is too tall, crop height
                new_height = int(width / target_ratio)
                top = (height - new_height) // 2
                image = image.crop((0, top, width, top + new_height))

            # Resize to standard story size
            image = image.resize((1080, 1920))

            # Create a drawing context
            draw = ImageDraw.Draw(image)

            # Try to load a font, fall back to default if not available
            try:
                font_large = ImageFont.truetype("arial.ttf", 60)
                font_small = ImageFont.truetype("arial.ttf", 48)
            except IOError:
                font_large = ImageFont.load_default()
                font_small = font_large

            # Add semi-transparent background for text
            if model_name:
                # Draw model name at the top
                text_width = draw.textlength(model_name, font=font_large)
                text_x = (1080 - text_width) // 2
                draw.rectangle([(0, 100), (1080, 200)], fill=(0, 0, 0, 128))
                draw.text((text_x, 120), model_name, font=font_large, fill=(255, 255, 255))

            if price:
                # Draw price at the bottom
                text_width = draw.textlength(f"Цена: {price}", font=font_small)
                text_x = (1080 - text_width) // 2
                draw.rectangle([(0, 1720), (1080, 1820)], fill=(0, 0, 0, 128))
                draw.text((text_x, 1740), f"Цена: {price}", font=font_small, fill=(255, 255, 255))

            # Save the image to a buffer
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG")
            buffer.seek(0)

            return buffer
        except Exception as e:
            logger.error(f"Error creating story image: {str(e)}")
            return None

    async def publish_story(self, story_id):
        """Publish a story to Telegram channel."""
        db = SessionLocal()
        try:
            # Get story from database
            story = db.query(Story).filter(Story.id == story_id).first()

            if not story:
                logger.error(f"Story {story_id} not found")
                return False

            # Check if already published
            if story.is_published:
                logger.info(f"Story {story_id} already published to Telegram")
                return True

            # Create story image with overlay
            if not story.media_file_id:
                logger.error(f"Story {story_id} has no media file")
                return False

            story_image_buffer = await self.create_story_image(
                story.media_file_id,
                story.model_name,
                story.price
            )

            if not story_image_buffer:
                logger.error(f"Failed to create story image for story {story_id}")
                return False

            # Create caption with link to post if available
            caption = ""
            if story.model_name:
                caption += f"{story.model_name}\n"
            if story.price:
                caption += f"Цена: {story.price}\n"
            if story.post_link:
                caption += f"\nПодробнее: {story.post_link}"

            # Send story to Telegram channel
            message = await self.bot.send_photo(
                TELEGRAM_CHANNEL_ID,
                InputFile(story_image_buffer.getvalue()),
                caption=caption
            )

            # Update story status in database
            story.is_published = True
            story.published_at = datetime.now(timezone.utc)
            story.post_link = f"https://t.me/{TELEGRAM_CHANNEL_ID.replace('@', '')}/{message.message_id}"

            # Add publication log
            log = StoryPublicationLog(
                story_id=story.id,
                status="success",
                message="Published to Telegram"
            )
            db.add(log)

            db.commit()

            logger.info(f"Story {story_id} published to Telegram successfully")
            return True
        except Exception as e:
            logger.error(f"Error publishing story {story_id} to Telegram: {str(e)}")

            # Add error log
            log = StoryPublicationLog(
                story_id=story_id,
                status="error",
                message=str(e)
            )
            db.add(log)
            db.commit()

            return False
        finally:
            db.close()
            await self.bot.session.close()

async def publish_story_to_telegram(story_id):
    """Publish a story to Telegram channel."""
    publisher = TelegramStoryPublisher()
    return await publisher.publish_story(story_id)
