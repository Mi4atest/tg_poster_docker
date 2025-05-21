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

from app.db.database import SessionLocal
from app.api.models.post import Post, PublicationLog
from app.config.settings import MEDIA_DIR
from app.utils.text_formatter import format_for_instagram

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение данных из переменных окружения
INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
INSTAGRAM_SESSION_PATH = os.getenv("INSTAGRAM_SESSION_PATH", "instagram_session.json")

class InstagramPublisher:
    """Класс для публикации постов в Instagram."""

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

    async def publish_post(self, post_id: str) -> bool:
        """Публикация поста в Instagram."""
        # Получаем сессию базы данных
        db = SessionLocal()

        try:
            # Получаем пост из базы данных
            post = db.query(Post).filter(Post.id == post_id).first()

            if not post:
                logger.error(f"Пост с ID {post_id} не найден")
                return False

            # Логируем, если пост уже опубликован, но продолжаем с повторной публикацией
            if post.is_published_instagram:
                logger.info(f"Пост с ID {post_id} уже опубликован в Instagram, выполняем повторную публикацию")

            # Авторизуемся в Instagram
            if not await self.login():
                # Добавляем лог об ошибке
                log = PublicationLog(
                    post_id=post_id,
                    platform="instagram",
                    status="error",
                    message="Ошибка авторизации в Instagram"
                )
                db.add(log)
                db.commit()
                return False

            # Получаем путь к директории с медиафайлами поста
            post_dir = MEDIA_DIR / post.storage_path

            # Получаем текст поста и форматируем его
            caption = format_for_instagram(post.text)

            # Загружаем медиафайлы
            media_paths = []

            # Загружаем фотографии из Telegram
            photos = post.photos
            videos = post.videos

            # Если есть фотографии или видео, загружаем их
            if photos or videos:
                # Загружаем фотографии
                for i, photo_id in enumerate(photos):
                    photo_path = post_dir / f"photo_{i}.jpg"
                    if not os.path.exists(photo_path):
                        # Если файл не существует, скачиваем его
                        await self._download_telegram_file(photo_id, photo_path)

                    if os.path.exists(photo_path):
                        media_paths.append(str(photo_path))

                # Загружаем видео
                for i, video_id in enumerate(videos):
                    video_path = post_dir / f"video_{i}.mp4"
                    if not os.path.exists(video_path):
                        # Если файл не существует, скачиваем его
                        await self._download_telegram_file(video_id, video_path)

                    if os.path.exists(video_path):
                        media_paths.append(str(video_path))

            # Публикуем пост в Instagram
            try:
                if len(media_paths) == 0:
                    # Если нет медиафайлов, публикуем только текст
                    logger.info("Публикация текстового поста в Instagram не поддерживается")

                    # Добавляем лог об ошибке
                    log = PublicationLog(
                        post_id=post_id,
                        platform="instagram",
                        status="error",
                        message="Публикация текстового поста в Instagram не поддерживается"
                    )
                    db.add(log)
                    db.commit()
                    return False

                elif len(media_paths) == 1:
                    # Если один медиафайл, публикуем как одиночный пост
                    media_path = media_paths[0]

                    try:
                        if media_path.endswith(('.jpg', '.jpeg', '.png')):
                            # Публикуем фото
                            self.client.photo_upload(media_path, caption)
                        elif media_path.endswith(('.mp4', '.mov')):
                            # Публикуем видео
                            try:
                                # Пробуем использовать video_upload
                                self.client.video_upload(media_path, caption)
                            except Exception as e:
                                if "Please install moviepy" in str(e):
                                    # Если ошибка связана с moviepy, используем альтернативный метод
                                    logger.warning(f"Ошибка при загрузке видео через video_upload: {str(e)}. Пробуем clip_upload.")
                                    self.client.clip_upload(media_path, caption)
                                else:
                                    # Если другая ошибка, пробрасываем её дальше
                                    raise
                        else:
                            logger.error(f"Неподдерживаемый формат файла: {media_path}")

                            # Добавляем лог об ошибке
                            log = PublicationLog(
                                post_id=post_id,
                                platform="instagram",
                                status="error",
                                message=f"Неподдерживаемый формат файла: {media_path}"
                            )
                            db.add(log)
                            db.commit()
                            return False
                    except Exception as e:
                        logger.error(f"Ошибка при публикации медиафайла: {str(e)}")

                        # Добавляем лог об ошибке
                        log = PublicationLog(
                            post_id=post_id,
                            platform="instagram",
                            status="error",
                            message=f"Ошибка при публикации медиафайла: {str(e)}"
                        )
                        db.add(log)
                        db.commit()
                        return False

                else:
                    # Если несколько медиафайлов, публикуем как карусель
                    # Проверяем, что все файлы существуют
                    valid_paths = []
                    photo_paths = []
                    video_paths = []

                    for path in media_paths:
                        if os.path.exists(path):
                            valid_paths.append(path)
                            # Разделяем фото и видео
                            if path.endswith(('.jpg', '.jpeg', '.png')):
                                photo_paths.append(path)
                            elif path.endswith(('.mp4', '.mov')):
                                video_paths.append(path)

                    if valid_paths:
                        try:
                            # Проверяем, есть ли видео в карусели
                            has_videos = any(path.endswith(('.mp4', '.mov')) for path in valid_paths)

                            if has_videos:
                                # Если есть видео, пробуем сначала загрузить только фото
                                if photo_paths:
                                    logger.info(f"Пост содержит видео. Сначала публикуем только фотографии ({len(photo_paths)} шт).")

                                    if len(photo_paths) == 1:
                                        # Если одно фото, публикуем как одиночный пост
                                        self.client.photo_upload(photo_paths[0], caption)
                                    else:
                                        # Если несколько фото, публикуем как карусель
                                        self.client.album_upload(photo_paths, caption)

                                    # Затем пробуем загрузить видео отдельно
                                    for video_path in video_paths:
                                        try:
                                            logger.info(f"Пробуем загрузить видео отдельно: {video_path}")
                                            # Пробуем использовать clip_upload вместо video_upload
                                            self.client.clip_upload(video_path, caption)
                                            logger.info(f"Видео успешно загружено: {video_path}")
                                        except Exception as video_error:
                                            logger.error(f"Ошибка при загрузке видео {video_path}: {str(video_error)}")
                                else:
                                    # Если нет фото, пробуем загрузить первое видео
                                    if video_paths:
                                        try:
                                            logger.info(f"Пост содержит только видео. Пробуем загрузить первое видео.")
                                            # Пробуем использовать clip_upload вместо video_upload
                                            self.client.clip_upload(video_paths[0], caption)
                                            logger.info(f"Видео успешно загружено: {video_paths[0]}")
                                        except Exception as video_error:
                                            logger.error(f"Ошибка при загрузке видео {video_paths[0]}: {str(video_error)}")
                                            raise
                            else:
                                # Если нет видео, загружаем все файлы как карусель
                                self.client.album_upload(valid_paths, caption)
                        except Exception as e:
                            if "Please install moviepy" in str(e) and photo_paths:
                                # Если ошибка связана с moviepy и есть фотографии, публикуем только фото
                                logger.warning(f"Ошибка при загрузке видео: {str(e)}. Публикуем только фотографии.")

                                if len(photo_paths) == 1:
                                    # Если одно фото, публикуем как одиночный пост
                                    self.client.photo_upload(photo_paths[0], caption)
                                else:
                                    # Если несколько фото, публикуем как карусель
                                    self.client.album_upload(photo_paths, caption)
                            else:
                                # Если другая ошибка, пробрасываем её дальше
                                raise
                    else:
                        logger.error("Нет доступных медиафайлов для публикации")

                        # Добавляем лог об ошибке
                        log = PublicationLog(
                            post_id=post_id,
                            platform="instagram",
                            status="error",
                            message="Нет доступных медиафайлов для публикации"
                        )
                        db.add(log)
                        db.commit()
                        return False

                # Обновляем статус публикации в базе данных
                post.is_published_instagram = True
                post.published_instagram_at = datetime.now(timezone.utc)

                # Добавляем лог об успешной публикации
                log = PublicationLog(
                    post_id=post_id,
                    platform="instagram",
                    status="success",
                    message="Пост успешно опубликован в Instagram"
                )

                db.add(log)
                db.commit()

                logger.info(f"Пост с ID {post_id} успешно опубликован в Instagram")
                return True

            except Exception as e:
                logger.error(f"Ошибка при публикации поста в Instagram: {str(e)}")

                # Добавляем лог об ошибке
                log = PublicationLog(
                    post_id=post_id,
                    platform="instagram",
                    status="error",
                    message=f"Ошибка при публикации: {str(e)}"
                )
                db.add(log)
                db.commit()
                return False

        except Exception as e:
            logger.error(f"Ошибка при публикации поста в Instagram: {str(e)}")

            # Добавляем лог об ошибке
            log = PublicationLog(
                post_id=post_id,
                platform="instagram",
                status="error",
                message=f"Ошибка: {str(e)}"
            )
            db.add(log)
            db.commit()
            return False

        finally:
            db.close()

    async def _download_telegram_file(self, file_id: str, save_path: str) -> bool:
        """Скачивание файла из Telegram."""
        try:
            # Получаем токен бота из переменных окружения
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")

            if not bot_token:
                logger.error("Отсутствует токен бота Telegram")
                return False

            # Создаем SSL-контекст, который игнорирует проверку сертификатов
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # Получаем информацию о файле
            file_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"

            # Используем SSL-контекст при создании сессии
            conn = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=conn) as session:
                async with session.get(file_url) as response:
                    if response.status != 200:
                        logger.error(f"Ошибка при получении информации о файле: {response.status}")
                        return False

                    data = await response.json()

                    if not data.get("ok"):
                        logger.error(f"Ошибка API Telegram: {data.get('description')}")
                        return False

                    file_path = data.get("result", {}).get("file_path")

                    if not file_path:
                        logger.error("Не удалось получить путь к файлу")
                        return False

                    # Скачиваем файл
                    download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

                    # Создаем директорию для сохранения файла, если она не существует
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)

                    async with session.get(download_url) as file_response:
                        if file_response.status != 200:
                            logger.error(f"Ошибка при скачивании файла: {file_response.status}")
                            return False

                        with open(save_path, 'wb') as f:
                            f.write(await file_response.read())

                        logger.info(f"Файл успешно скачан и сохранен: {save_path}")
                        return True

        except Exception as e:
            logger.error(f"Ошибка при скачивании файла из Telegram: {str(e)}")
            return False

# Функция для публикации поста в Instagram
async def publish_post_to_instagram(post_id: str) -> bool:
    """Публикация поста в Instagram."""
    publisher = InstagramPublisher()
    return await publisher.publish_post(post_id)
