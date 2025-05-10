import os
import logging
import asyncio
import ssl
from datetime import datetime, timezone
import aiohttp
import json
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from instagrapi import Client
from PIL import Image, ImageDraw, ImageFont
import io

from app.db.database import SessionLocal
from app.api.models.story import Story, StoryPublicationLog
from app.config.settings import MEDIA_DIR, API_HOST, API_PORT

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение данных из переменных окружения
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
INSTAGRAM_SESSION_PATH = os.getenv("INSTAGRAM_SESSION_PATH", "instagram_session.json")

class InstagramStoryPublisher:
    """Класс для публикации историй в Instagram."""

    def __init__(self):
        """Инициализация клиента Instagram."""
        self.client = Client()
        self.is_logged_in = False

    async def login(self) -> bool:
        """Авторизация в Instagram."""
        try:
            # Проверяем наличие сохраненной сессии
            if os.path.exists(INSTAGRAM_SESSION_PATH):
                try:
                    # Загружаем сессию из файла
                    with open(INSTAGRAM_SESSION_PATH, 'r') as f:
                        session_data = json.load(f)

                    # Устанавливаем сессию
                    self.client.set_settings(session_data)

                    # Проверяем валидность сессии
                    self.client.get_timeline_feed()
                    self.is_logged_in = True
                    logger.info("Успешно восстановлена сессия Instagram")
                    return True
                except Exception as e:
                    logger.warning(f"Не удалось восстановить сессию Instagram: {str(e)}")

            # Если сессия не найдена или недействительна, выполняем вход
            if not self.is_logged_in:
                if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
                    logger.error("Отсутствуют учетные данные Instagram")
                    return False

                # Выполняем вход
                self.client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)

                # Сохраняем сессию
                session_data = self.client.get_settings()
                with open(INSTAGRAM_SESSION_PATH, 'w') as f:
                    json.dump(session_data, f)

                self.is_logged_in = True
                logger.info("Успешная авторизация в Instagram")
                return True

        except Exception as e:
            logger.error(f"Ошибка при авторизации в Instagram: {str(e)}")
            return False

    async def download_telegram_file(self, file_id: str) -> Optional[bytes]:
        """Скачивание файла из Telegram."""
        try:
            # Получаем токен бота из переменных окружения
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")

            if not bot_token:
                logger.error("Отсутствует токен бота Telegram")
                return None

            # Используем локальный API вместо прямого обращения к Telegram API
            url = f"http://{API_HOST}:{API_PORT}/api/telegram/file/{file_id}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        logger.error(f"Failed to download file {file_id}: {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Ошибка при скачивании файла из Telegram: {str(e)}")
            return None

    def create_story_image(self, image_data: bytes, model_name: Optional[str], price: Optional[str]) -> Optional[bytes]:
        """Создание изображения для истории с наложением текста."""
        try:
            # Открываем изображение
            image = Image.open(io.BytesIO(image_data))

            # Изменяем размер изображения для формата истории (9:16)
            width, height = image.size
            target_ratio = 9 / 16
            current_ratio = width / height

            if current_ratio > target_ratio:
                # Изображение слишком широкое, обрезаем ширину
                new_width = int(height * target_ratio)
                left = (width - new_width) // 2
                image = image.crop((left, 0, left + new_width, height))
            elif current_ratio < target_ratio:
                # Изображение слишком высокое, обрезаем высоту
                new_height = int(width / target_ratio)
                top = (height - new_height) // 2
                image = image.crop((0, top, width, top + new_height))

            # Изменяем размер до стандартного размера истории
            image = image.resize((1080, 1920))

            # Создаем контекст для рисования
            draw = ImageDraw.Draw(image)

            # Пытаемся загрузить шрифт, используем стандартный, если не удалось
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

            # Сохраняем изображение в буфер
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG")
            buffer.seek(0)

            return buffer.getvalue()
        except Exception as e:
            logger.error(f"Ошибка при создании изображения для истории: {str(e)}")
            return None

    async def publish_story(self, story_id: str) -> bool:
        """Публикация истории в Instagram."""
        # Получаем сессию базы данных
        db = SessionLocal()

        try:
            # Получаем историю из базы данных
            story = db.query(Story).filter(Story.id == story_id).first()

            if not story:
                logger.error(f"История с ID {story_id} не найдена")
                return False

            # Проверяем, была ли история уже опубликована
            if story.is_published:
                logger.info(f"История с ID {story_id} уже опубликована в Instagram")
                return True

            # Авторизуемся в Instagram
            if not await self.login():
                # Добавляем лог об ошибке
                log = StoryPublicationLog(
                    story_id=story_id,
                    status="error",
                    message="Ошибка авторизации в Instagram"
                )
                db.add(log)
                db.commit()
                return False

            # Скачиваем медиафайл из Telegram
            if not story.media_file_id:
                logger.error(f"История с ID {story_id} не имеет медиафайла")
                return False

            media_data = await self.download_telegram_file(story.media_file_id)
            if not media_data:
                logger.error(f"Не удалось скачать медиафайл для истории {story_id}")
                return False

            # Создаем изображение для истории с наложением текста
            story_image_data = self.create_story_image(media_data, story.model_name, story.price)
            if not story_image_data:
                logger.error(f"Не удалось создать изображение для истории {story_id}")
                return False

            # Сохраняем изображение во временный файл
            temp_file = f"/tmp/instagram_story_{story_id}.jpg"
            with open(temp_file, "wb") as f:
                f.write(story_image_data)

            # Публикуем историю в Instagram
            caption = ""
            if story.model_name:
                caption += f"{story.model_name}\n"
            if story.price:
                caption += f"Цена: {story.price}\n"

            # Публикуем историю
            result = self.client.photo_upload_to_story(temp_file, caption)

            # Обновляем статус истории в базе данных
            story.is_published = True
            story.published_at = datetime.now(timezone.utc)

            # Добавляем лог об успешной публикации
            log = StoryPublicationLog(
                story_id=story.id,
                status="success",
                message="Опубликовано в Instagram"
            )
            db.add(log)

            db.commit()

            # Удаляем временный файл
            if os.path.exists(temp_file):
                os.remove(temp_file)

            logger.info(f"История {story_id} успешно опубликована в Instagram")
            return True
        except Exception as e:
            logger.error(f"Ошибка при публикации истории в Instagram: {str(e)}")

            # Добавляем лог об ошибке
            log = StoryPublicationLog(
                story_id=story_id,
                status="error",
                message=f"Ошибка: {str(e)}"
            )
            db.add(log)
            db.commit()

            return False
        finally:
            db.close()

async def publish_story_to_instagram(story_id: str) -> bool:
    """Публикация истории в Instagram."""
    publisher = InstagramStoryPublisher()
    return await publisher.publish_story(story_id)
