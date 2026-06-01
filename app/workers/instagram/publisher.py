import os
import logging
import asyncio
import ssl
import random
import time
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
from app.workers.instagram.graph_publisher import InstagramGraphPublisher

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
        self.last_activity_time = 0
        self.activity_count = 0
        self._device_settings = None
        
        # Настройка устройства на iPhone 15 Pro Max для лучшей имитации
        self._setup_iphone_device()

    def _setup_iphone_device(self):
        """Настройка устройства на iPhone 15 Pro Max для имитации реального пользователя."""
        try:
            # Параметры устройства iPhone 15 Pro Max для instagrapi
            # Эти настройки будут применены при следующем логине
            device_settings = {
                "app_version": "269.0.0.18.75",
                "android_version": 33,
                "android_release": "13",
                "dpi": "480dpi",
                "resolution": "1170x2532",
                "manufacturer": "Apple",
                "device": "iPhone15,3",  # iPhone 15 Pro Max
                "model": "iPhone15,3",
                "cpu": "arm64",
                "version_code": "314665256",
                "user_agent": "Instagram 269.0.0.18.75 (iPhone15,3; iOS 17_0; en_US; en-US; scale=3.00; 1170x2532; 461865296) AppleWebKit/420+"
            }
            
            # Пробуем получить текущие настройки
            try:
                current_settings = self.client.get_settings()
                if current_settings:
                    # Обновляем настройки устройства
                    current_settings.update(device_settings)
                    self.client.set_settings(current_settings)
                    logger.info("Настройки устройства iPhone 15 Pro Max применены")
                else:
                    # Сохраняем настройки для применения при логине
                    self._device_settings = device_settings
                    logger.info("Настройки устройства iPhone 15 Pro Max сохранены для применения при авторизации")
            except Exception as settings_error:
                # Если не удалось получить настройки (сессия еще не создана)
                self._device_settings = device_settings
                logger.info("Настройки устройства iPhone 15 Pro Max будут применены при первом логине")
                
        except Exception as e:
            logger.warning(f"Не удалось настроить устройство iPhone: {str(e)}")
            self._device_settings = None

    async def login(self) -> bool:
        """Авторизация в Instagram с улучшенной обработкой ошибок."""
        try:
            # Проверяем наличие сохраненной сессии
            if os.path.exists(INSTAGRAM_SESSION_PATH):
                try:
                    # Загружаем сессию из файла
                    with open(INSTAGRAM_SESSION_PATH, 'r') as f:
                        session_data = json.load(f)

                    # Устанавливаем сессию
                    self.client.set_settings(session_data)
                    
                    # Применяем настройки устройства iPhone
                    self._setup_iphone_device()

                    # Проверяем валидность сессии
                    self.client.get_timeline_feed()
                    self.is_logged_in = True
                    logger.info("Успешно восстановлена сессия Instagram")
                    return True
                except Exception as e:
                    logger.warning(f"Не удалось восстановить сессию Instagram: {str(e)}")
                    # Удаляем недействительную сессию
                    try:
                        os.remove(INSTAGRAM_SESSION_PATH)
                        logger.info("Удалена недействительная сессия Instagram")
                    except Exception as remove_error:
                        logger.warning(f"Не удалось удалить файл сессии: {str(remove_error)}")

            # Если сессия не найдена или недействительна, выполняем вход
            if not self.is_logged_in:
                if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
                    logger.error("Отсутствуют учетные данные Instagram")
                    return False

                try:
                    # Применяем настройки устройства перед логином, если они были сохранены
                    if self._device_settings:
                        try:
                            # Пробуем установить настройки перед логином
                            temp_settings = self.client.get_settings() if hasattr(self.client, 'get_settings') else {}
                            temp_settings.update(self._device_settings)
                            self.client.set_settings(temp_settings)
                        except:
                            pass  # Если не получилось, настройки применятся автоматически
                    
                    # Выполняем вход
                    self.client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)

                    # Сохраняем сессию (включая настройки устройства)
                    session_data = self.client.get_settings()
                    
                    # Убеждаемся, что настройки устройства сохранены в сессии
                    if self._device_settings:
                        session_data.update(self._device_settings)
                        self.client.set_settings(session_data)
                    
                    with open(INSTAGRAM_SESSION_PATH, 'w') as f:
                        json.dump(session_data, f)

                    self.is_logged_in = True
                    logger.info("Успешная авторизация в Instagram")
                    return True
                except Exception as login_error:
                    error_str = str(login_error)
                    logger.error(f"Ошибка при входе в Instagram: {error_str}")
                    
                    # Проверяем, нужна ли 2FA
                    if "challenge" in error_str.lower() or "two_factor" in error_str.lower():
                        logger.error("Instagram требует двухфакторную аутентификацию (2FA)")
                        logger.error("Необходимо настроить 2FA в instagrapi или отключить 2FA в настройках Instagram")
                        return False
                    
                    # Проверяем, не заблокирован ли аккаунт
                    if "checkpoint" in error_str.lower() or "suspended" in error_str.lower():
                        logger.error("Аккаунт Instagram может быть заблокирован или требует проверки")
                        logger.error("Проверьте аккаунт вручную через браузер или приложение Instagram")
                        return False
                    
                    # Другие ошибки
                    logger.error(f"Неизвестная ошибка авторизации: {error_str}")
                    return False

        except Exception as e:
            logger.error(f"Ошибка при авторизации в Instagram: {str(e)}")
            return False

    async def _human_like_delay(self, min_seconds: int = 30, max_seconds: int = 120):
        """Имитация человеческого поведения с случайными задержками."""
        # Базовое время ожидания (увеличено для более реалистичного поведения)
        base_delay = random.randint(min_seconds, max_seconds)
        
        # Дополнительная задержка в зависимости от времени суток
        current_hour = datetime.now().hour
        if 2 <= current_hour <= 6:  # Ночное время - больше задержек
            base_delay += random.randint(60, 180)  # 1-3 минуты
        elif 9 <= current_hour <= 17:  # Рабочее время - средние задержки
            base_delay += random.randint(30, 120)  # 30 секунд - 2 минуты
        else:  # Вечернее время - небольшие задержки
            base_delay += random.randint(15, 60)  # 15-60 секунд
        
        # Дополнительная задержка в зависимости от количества активности
        if self.activity_count > 0:
            # Чем больше активности, тем больше задержка
            activity_delay = min(self.activity_count * 30, 300)  # Максимум 5 минут
            base_delay += activity_delay
        
        logger.info(f"Человекоподобная задержка: {base_delay} секунд ({base_delay/60:.1f} минут)")
        await asyncio.sleep(base_delay)
        
        # Обновляем счетчик активности
        self.activity_count += 1
        self.last_activity_time = time.time()

    async def _simulate_human_activity(self):
        """Имитация человеческой активности в Instagram с улучшенным поведением."""
        try:
            # Проверяем, что мы авторизованы
            if not self.is_logged_in:
                logger.warning("Пропускаем имитацию активности - не авторизованы")
                return
            
            # Случайные паузы между действиями (имитация чтения, размышления)
            await asyncio.sleep(random.randint(3, 8))
            
            # Случайно просматриваем ленту (увеличена вероятность и время)
            if random.random() < 0.5:  # 50% вероятность
                logger.info("Имитация просмотра ленты...")
                try:
                    feed = self.client.get_timeline_feed()
                    # Имитируем просмотр нескольких постов
                    posts_to_view = random.randint(2, 5)
                    for i in range(min(posts_to_view, len(feed.get('items', [])))):
                        await asyncio.sleep(random.randint(3, 8))  # Время на просмотр каждого поста
                    await asyncio.sleep(random.randint(5, 15))  # Дополнительная пауза
                except Exception as e:
                    logger.warning(f"Ошибка при просмотре ленты: {str(e)}")
            
            # Случайно проверяем уведомления
            if random.random() < 0.4:  # 40% вероятность
                logger.info("Имитация проверки уведомлений...")
                try:
                    self.client.get_notifications()
                    await asyncio.sleep(random.randint(5, 15))
                except Exception as e:
                    logger.warning(f"Ошибка при проверке уведомлений: {str(e)}")
            
            # Случайно просматриваем свой профиль
            if random.random() < 0.3:  # 30% вероятность
                logger.info("Имитация просмотра профиля...")
                try:
                    self.client.user_info(self.client.user_id)
                    await asyncio.sleep(random.randint(3, 10))
                except Exception as e:
                    logger.warning(f"Ошибка при просмотре профиля: {str(e)}")
            
            # Случайно просматриваем истории (если доступно)
            if random.random() < 0.2:  # 20% вероятность
                logger.info("Имитация просмотра историй...")
                try:
                    # Пробуем получить истории (может не работать в зависимости от API)
                    await asyncio.sleep(random.randint(5, 12))
                except Exception as e:
                    logger.warning(f"Ошибка при просмотре историй: {str(e)}")
                    
        except Exception as e:
            logger.warning(f"Ошибка при имитации активности: {str(e)}")

    async def _random_scroll_behavior(self):
        """Имитация случайного скроллинга и пауз с более реалистичным поведением."""
        # Случайные паузы между действиями (имитация чтения, размышления)
        pause_duration = random.randint(8, 25)
        await asyncio.sleep(pause_duration)
        
        # Имитация случайных микропауз (как при реальном использовании)
        if random.random() < 0.8:  # 80% вероятность микропауз
            micro_pause = random.uniform(0.5, 2.5)
            await asyncio.sleep(micro_pause)
        
        # Имитация "размышления" перед действием
        if random.random() < 0.5:  # 50% вероятность
            thinking_pause = random.randint(2, 6)
            await asyncio.sleep(thinking_pause)

    def reset_activity_counter(self):
        """Сброс счетчика активности (вызывать периодически)."""
        self.activity_count = 0
        self.last_activity_time = 0
        logger.info("Счетчик активности сброшен")

    async def publish_post(self, post_id: str) -> bool:
        """Публикация поста в Instagram с имитацией человеческого поведения."""
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

            # Имитация человеческого поведения перед публикацией
            logger.info("Начинаем имитацию человеческого поведения...")
            
            # Сброс счетчика активности для новой сессии
            if self.activity_count > 10:  # Если слишком много активности, сбрасываем
                self.reset_activity_counter()
            
            # Случайная задержка перед началом работы (увеличено)
            await self._human_like_delay(min_seconds=30, max_seconds=120)
            
            # Имитация активности в Instagram
            await self._simulate_human_activity()
            
            # Случайные паузы
            await self._random_scroll_behavior()

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

                    # Имитация человеческого поведения перед загрузкой (увеличено)
                    await self._random_scroll_behavior()
                    await asyncio.sleep(random.randint(10, 30))

                    try:
                        if media_path.endswith(('.jpg', '.jpeg', '.png')):
                            # Публикуем фото
                            logger.info("Загружаем фото с имитацией человеческого поведения...")
                            self.client.photo_upload(media_path, caption)
                        elif media_path.endswith(('.mp4', '.mov')):
                            # Публикуем видео как обычный пост (не Reels)
                            logger.info("Загружаем видео с имитацией человеческого поведения...")
                            try:
                                # Используем video_upload для обычного поста (не Reels)
                                self.client.video_upload(media_path, caption)
                            except Exception as e:
                                error_str = str(e)
                                if "Please install moviepy" in error_str:
                                    # Если ошибка связана с moviepy, пробуем альтернативный метод
                                    logger.warning(f"Ошибка при загрузке видео через video_upload: {error_str}")
                                    logger.warning("Пробуем использовать альтернативный метод...")
                                    # Пробуем video_upload с другими параметрами или используем clip_upload как последний вариант
                                    try:
                                        self.client.video_upload(media_path, caption)
                                    except:
                                        logger.error("Не удалось опубликовать видео. Убедитесь, что moviepy установлен.")
                                        raise
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
                            # Имитация человеческого поведения перед загрузкой карусели
                            await self._random_scroll_behavior()
                            await asyncio.sleep(random.randint(15, 45))
                            
                            # Проверяем, есть ли видео в карусели
                            has_videos = any(path.endswith(('.mp4', '.mov')) for path in valid_paths)

                            if has_videos:
                                # ПУБЛИКУЕМ ФОТО И ВИДЕО ВМЕСТЕ В ОДНОЙ КАРУСЕЛИ
                                # Сортируем медиафайлы: сначала фото, потом видео (как в оригинальном посте)
                                sorted_paths = photo_paths + video_paths
                                
                                logger.info(f"Публикуем карусель с {len(photo_paths)} фото и {len(video_paths)} видео в одном посте")
                                
                                try:
                                    # Пробуем опубликовать карусель с фото и видео вместе
                                    # В instagrapi album_upload может поддерживать смешанные типы, но нужно проверить
                                    # Если не поддерживает, используем альтернативный метод
                                    self.client.album_upload(sorted_paths, caption)
                                    logger.info(f"✅ Успешно опубликована карусель с фото и видео в одном посте")
                                except Exception as album_error:
                                    error_str = str(album_error)
                                    logger.warning(f"Не удалось опубликовать карусель с видео через album_upload: {error_str}")
                                    
                                    # Альтернативный подход: пробуем использовать комбинированный метод
                                    # Сначала публикуем фото как карусель, затем добавляем видео
                                    # Но это создаст два поста, что не то, что нужно
                                    
                                    # Лучше попробовать другой способ - использовать video_upload для видео в карусели
                                    # Или просто опубликовать все вместе и посмотреть, что получится
                                    
                                    # Если album_upload не поддерживает видео, публикуем только фото
                                    if photo_paths:
                                        logger.info(f"Публикуем фотографии ({len(photo_paths)} шт) как карусель")
                                        if len(photo_paths) == 1:
                                            self.client.photo_upload(photo_paths[0], caption)
                                        else:
                                            self.client.album_upload(photo_paths, caption)
                                        
                                        # Видео публикуем отдельно как обычный пост (не Reels)
                                        logger.warning("⚠️ Видео не удалось добавить в карусель, публикуем отдельно как обычный пост")
                                        for video_path in video_paths:
                                            try:
                                                await self._random_scroll_behavior()
                                                await asyncio.sleep(random.randint(15, 40))
                                                # Используем video_upload для обычного поста (не clip_upload для Reels)
                                                self.client.video_upload(video_path, caption)
                                                logger.info(f"Видео опубликовано отдельно как обычный пост: {video_path}")
                                            except Exception as video_error:
                                                logger.error(f"Ошибка при публикации видео {video_path}: {str(video_error)}")
                                    else:
                                        # Если нет фото, публикуем только видео как обычный пост
                                        if video_paths:
                                            logger.info(f"Публикуем только видео как обычный пост: {video_paths[0]}")
                                            self.client.video_upload(video_paths[0], caption)
                            else:
                                # Если нет видео, загружаем все файлы как карусель
                                logger.info("Загружаем карусель с имитацией человеческого поведения...")
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

                # Имитация человеческого поведения после публикации (увеличено)
                logger.info("Имитация активности после публикации...")
                await self._random_scroll_behavior()
                await asyncio.sleep(random.randint(20, 60))  # 20-60 секунд
                
                # Случайно просматриваем свой профиль или ленту (увеличена вероятность)
                if random.random() < 0.5:  # 50% вероятность
                    try:
                        await self._simulate_human_activity()
                    except Exception as e:
                        logger.warning(f"Ошибка при имитации активности после публикации: {str(e)}")

                # Обновляем статус публикации в базе данных
                post.is_published_instagram = True
                post.published_instagram_at = datetime.now(timezone.utc)

                # Добавляем лог об успешной публикации
                log = PublicationLog(
                    post_id=post_id,
                    platform="instagram",
                    status="success",
                    message="Пост успешно опубликован в Instagram с имитацией человеческого поведения"
                )

                db.add(log)
                db.commit()

                logger.info(f"Пост с ID {post_id} успешно опубликован в Instagram с имитацией человеческого поведения")
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
    graph_publisher = InstagramGraphPublisher()
    if graph_publisher.enabled:
        logger.info("Публикация в Instagram через Graph API")
        return await graph_publisher.publish_post(post_id)

    logger.warning("Graph API не настроен, используем instagrapi fallback")
    publisher = InstagramPublisher()
    return await publisher.publish_post(post_id)
