import vk_api
import logging
import aiohttp
import asyncio
import requests
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import os
from PIL import Image, ImageDraw, ImageFont
import io

from app.config.settings import VK_ACCESS_TOKEN, VK_GROUP_ID, API_HOST, API_PORT
from app.db.database import SessionLocal
from app.api.models.story import Story, StoryPublicationLog

logger = logging.getLogger(__name__)

class VKStoryPublisher:
    """Class for publishing stories to VK."""

    def __init__(self):
        """Initialize VK API session."""
        self.vk_session = vk_api.VkApi(token=VK_ACCESS_TOKEN, api_version="5.131")
        self.vk = self.vk_session.get_api()
        self.upload = vk_api.VkUpload(self.vk_session)

    async def download_telegram_file(self, file_id):
        """Download file from Telegram."""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://{API_HOST}:{API_PORT}/api/telegram/file/{file_id}"

                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        logger.error(f"Failed to download file {file_id}: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error downloading file {file_id}: {str(e)}")
            return None

    def create_story_image(self, image_data, model_name, price):
        """Create a story image with model name and price overlay."""
        try:
            # Open the image
            image = Image.open(io.BytesIO(image_data))

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
                # Используем более крупный шрифт для лучшей видимости
                font_large = ImageFont.truetype("arial.ttf", 80)
            except IOError:
                try:
                    # Пробуем загрузить системный шрифт
                    font_large = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 80)
                except IOError:
                    # Если не удалось, используем стандартный
                    font_large = ImageFont.load_default()

            # Формируем текст в одну строку
            text = ""
            if model_name and price:
                text = f"{model_name} - {price}"
            elif model_name:
                text = model_name
            elif price:
                text = f"Цена: {price}"

            if text:
                # Рисуем текст с обводкой для лучшей видимости на любом фоне
                # Сначала рисуем черную обводку
                for offset_x, offset_y in [(-2, -2), (-2, 2), (2, -2), (2, 2)]:
                    draw.text((540 + offset_x, 1800 + offset_y), text, font=font_large, fill=(0, 0, 0), anchor="ms")

                # Затем рисуем белый текст поверх
                draw.text((540, 1800), text, font=font_large, fill=(255, 255, 255), anchor="ms")

            # Save the image to a buffer
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG")
            buffer.seek(0)

            return buffer.getvalue()
        except Exception as e:
            logger.error(f"Error creating story image: {str(e)}")
            return None

    async def publish_story(self, story_id):
        """Publish a story to VK."""
        db = SessionLocal()
        try:
            # Get story from database
            story = db.query(Story).filter(Story.id == story_id).first()

            if not story:
                logger.error(f"Story {story_id} not found")
                return False

            # Check if already published
            if story.is_published:
                logger.info(f"Story {story_id} already published to VK")
                return True

            # Download media file from Telegram
            if not story.media_file_id:
                logger.error(f"Story {story_id} has no media file")
                return False

            media_data = await self.download_telegram_file(story.media_file_id)
            if not media_data:
                logger.error(f"Failed to download media file for story {story_id}")
                return False

            # Create story image with overlay
            story_image_data = self.create_story_image(media_data, story.model_name, story.price)
            if not story_image_data:
                logger.error(f"Failed to create story image for story {story_id}")
                return False

            # Save story image to temporary file
            temp_file = f"/tmp/vk_story_{story_id}.jpg"
            with open(temp_file, "wb") as f:
                f.write(story_image_data)

            # Для публикации сторис в группе ВКонтакте нужно использовать другой подход
            # Сначала получаем URL для загрузки фото
            try:
                # Получаем адрес сервера для загрузки истории
                upload_server = self.vk.stories.getPhotoUploadServer(
                    add_to_news=1,  # Добавить в новости
                    group_id=abs(int(VK_GROUP_ID)),  # ID группы (положительное число)
                    user_ids=[],  # Пустой список пользователей
                    link_text="",  # Пустой текст ссылки
                    link_url=""  # Пустой URL ссылки
                )

                logger.info(f"VK upload server: {upload_server}")

                if not upload_server or 'upload_url' not in upload_server:
                    logger.error(f"Failed to get upload server for VK story")
                    raise Exception("Failed to get upload server for VK story")

                # Загружаем фото на сервер
                with open(temp_file, 'rb') as file:
                    response = requests.post(upload_server['upload_url'], files={'file': file})

                if response.status_code != 200:
                    logger.error(f"Failed to upload story to VK: {response.status_code} {response.text}")
                    raise Exception(f"Failed to upload story to VK: {response.status_code}")

                upload_data = response.json()
                logger.info(f"VK upload response: {upload_data}")

                if not upload_data or 'upload_result' not in upload_data:
                    logger.error(f"Invalid upload response from VK: {upload_data}")
                    raise Exception("Invalid upload response from VK")

                # Сохраняем историю
                save_result = self.vk.stories.save(
                    upload_results=upload_data['upload_result'],
                    group_id=abs(int(VK_GROUP_ID))
                )

                logger.info(f"VK save result: {save_result}")

                # Проверяем результат сохранения
                if not save_result or not isinstance(save_result, list) or len(save_result) == 0:
                    logger.error(f"Failed to save story to VK: {save_result}")
                    raise Exception("Failed to save story to VK")

                # Получаем ID истории и ссылку
                story_data = save_result[0]
                owner_id = story_data.get('owner_id', VK_GROUP_ID)
                story_id = story_data.get('id', 'unknown')
                story_link = f"https://vk.com/stories{owner_id}_{story_id}"

                # Проверяем, что история действительно опубликована
                try:
                    # Получаем список историй группы
                    stories = self.vk.stories.get(owner_id=VK_GROUP_ID)
                    logger.info(f"VK stories response: {stories}")

                    if not stories or 'items' not in stories or len(stories['items']) == 0:
                        logger.warning(f"Story may not be published to VK: no stories found for group {VK_GROUP_ID}")
                        # Но продолжаем, так как сохранение истории прошло успешно
                except Exception as e:
                    logger.warning(f"Could not verify story publication: {str(e)}")
                    # Продолжаем, так как основная операция сохранения прошла успешно
            except Exception as e:
                logger.error(f"Error publishing story to VK: {str(e)}")
                raise Exception(f"Error publishing story to VK: {str(e)}")

            # Update story status in database
            story.is_published = True
            story.published_at = datetime.now(timezone.utc)
            story.post_link = story_link

            # Add publication log
            log = StoryPublicationLog(
                story_id=story.id,
                status="success",
                message="Published to VK"
            )
            db.add(log)

            db.commit()

            # Clean up temporary file
            if os.path.exists(temp_file):
                os.remove(temp_file)

            logger.info(f"Story {story_id} published to VK successfully")
            return True
        except Exception as e:
            logger.error(f"Error publishing story {story_id} to VK: {str(e)}")

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

async def publish_story_to_vk(story_id):
    """Publish a story to VK."""
    publisher = VKStoryPublisher()
    return await publisher.publish_story(story_id)
