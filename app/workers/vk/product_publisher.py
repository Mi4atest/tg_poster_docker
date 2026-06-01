import vk_api
import logging
import aiohttp
import asyncio
import requests
import time
import re
import json
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import List, Optional, Dict

from vk_api.exceptions import ApiError

from app.config.settings import (
    VK_GROUP_ID,
    VK_MARKET_ACCESS_TOKEN,
    API_HOST,
    API_PORT,
    VK_MARKET_ENABLED,
    VK_MARKET_AUTO_CATEGORY,
    VK_MARKET_AUTO_COLLECTION
)
from app.db.database import SessionLocal
from app.api.models.post import Post
from app.api.models.product import Product
from app.utils.product_parser import parse_product_data
from app.utils.vk_client import (
    agent_debug_log,
    get_market_vk_session,
    market_token_source,
    vk_api_error_code,
)

logger = logging.getLogger(__name__)


class VKProductPublisher:
    """Class for publishing products to VK Market."""

    def __init__(self):
        """Initialize VK API session."""
        self.vk_session = get_market_vk_session(api_version="5.199")
        self.vk = self.vk_session.get_api()
        self.upload = vk_api.VkUpload(self.vk_session)
        self._categories_cache = None
        self._albums_cache = None
        self._last_api_call = 0
        self._min_api_interval = 3  # Минимальный интервал между запросами к API (секунды)

    async def _wait_for_api_interval(self):
        """Ожидание минимального интервала между запросами к API."""
        elapsed = time.time() - self._last_api_call
        if elapsed < self._min_api_interval:
            wait_time = self._min_api_interval - elapsed
            await asyncio.sleep(wait_time)
        self._last_api_call = time.time()

    def _coerce_saved_market_photo_list(self, raw: object, _depth: int = 0) -> List[Dict]:
        """Ответ saveMarketPhoto / saveProductPhoto → список {id, owner_id} для market.add."""
        if _depth > 8:
            return []
        if isinstance(raw, list):
            out: List[Dict] = []
            for el in raw:
                if isinstance(el, dict) and "id" in el and "owner_id" in el:
                    out.append(el)
                elif isinstance(el, dict):
                    out.extend(self._coerce_saved_market_photo_list(el, _depth + 1))
            return out

        if not isinstance(raw, dict):
            return []

        def _as_photo_row(d: dict) -> Optional[Dict]:
            if "photo_id" in d and "owner_id" in d:
                return {"id": d["photo_id"], "owner_id": d["owner_id"]}
            if "id" in d:
                oid = d.get("owner_id")
                if oid is None:
                    oid = -abs(int(VK_GROUP_ID))
                return {"id": d["id"], "owner_id": oid}
            return None

        row = _as_photo_row(raw)
        if row:
            return [row]

        for nk in ("response", "photo", "photos", "picture", "result", "data", "image", "item"):
            v = raw.get(nk)
            if v is None:
                continue
            sub = self._coerce_saved_market_photo_list(v, _depth + 1)
            if sub:
                return sub

        return []

    def _save_market_uploaded_product_photo(self, upload_data: dict) -> List[Dict]:
        """
        Сохранить фото товара после POST на upload_url.

        Классический ответ (photos.getMarketUploadServer): есть строка photo + server + hash → photos.saveMarketPhoto.
        Новый ответ (market.getProductPhotoUploadServer): часто нет ключа photo → market.saveProductPhoto
        с параметром upload_response (JSON всего ответа upload-сервера; иначе VK: «upload_response is undefined»).
        """
        gid = abs(int(VK_GROUP_ID))
        if upload_data.get("photo") is not None:
            save_params = {
                "group_id": gid,
                "photo": upload_data["photo"],
                "server": upload_data["server"],
                "hash": upload_data["hash"],
            }
            raw = self.vk.photos.saveMarketPhoto(**save_params)
        else:
            upload_response_json = json.dumps(upload_data, ensure_ascii=False)
            params: Dict = {
                "group_id": gid,
                "upload_response": upload_response_json,
            }
            raw = self.vk.market.saveProductPhoto(**params)

        return self._coerce_saved_market_photo_list(raw)

    async def download_telegram_file(self, file_id):
        """Download file from Telegram by file_id."""
        # Используем ту же логику, что и в VKPublisher
        bot = None
        try:
            from app.config.settings import TELEGRAM_BOT_TOKEN
            from aiogram import Bot
            bot = Bot(token=TELEGRAM_BOT_TOKEN)

            file_info = await bot.get_file(file_id)
            file_path = file_info.file_path
            file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"

            async with aiohttp.ClientSession() as session:
                try:
                    import ssl
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE

                    async with session.get(file_url, ssl=ssl_context) as response:
                        if response.status == 200:
                            return await response.read()
                    logger.error(f"Failed to download file from Telegram: {response.status}")
                    return None
                except Exception as e:
                    logger.error(f"Error downloading file from Telegram: {str(e)}")
                    return None
        except Exception as e:
            logger.error(f"Error downloading file {file_id} from Telegram: {str(e)}")
            return None
        finally:
            if bot:
                await bot.session.close()

    def get_market_categories(self) -> Optional[Dict]:
        """
        Получить список категорий товаров через market.getCategories.

        Returns:
            Словарь с категориями или None
        """
        if self._categories_cache:
            return self._categories_cache

        try:
            # Используем синхронный вызов, так как метод вызывается из синхронного контекста
            elapsed = time.time() - self._last_api_call
            if elapsed < self._min_api_interval:
                time.sleep(self._min_api_interval - elapsed)
            self._last_api_call = time.time()

            response = self.vk.market.getCategories()
            self._categories_cache = response
            return response
        except Exception as e:
            logger.error(f"Error getting market categories: {str(e)}")
            return None

    def find_category_id(self, category_name: str) -> Optional[int]:
        """
        Найти ID категории по названию.

        Args:
            category_name: Название категории (например, "Смартфоны")

        Returns:
            ID категории или None
        """
        if not category_name:
            return None

        # Получаем категории через API
        categories = self.get_market_categories()
        if not categories:
            logger.warning("Could not get categories from VK API, category will be determined by VK")
            return None

        # Обрабатываем структуру ответа от VK API
        # API может возвращать либо dict с 'response' -> 'items', либо список напрямую
        items = None
        if isinstance(categories, dict):
            if 'response' in categories:
                if 'items' in categories['response']:
                    items = categories['response']['items']
                else:
                    items = categories['response']
            elif 'items' in categories:
                items = categories['items']
        elif isinstance(categories, list):
            items = categories

        if not items:
            logger.warning("No category items found in API response")
            return None

        # Парсим дерево категорий
        category_lower = category_name.lower()

        # Маппинг русских названий на английские для поиска в API
        name_mapping = {
            'смартфоны': ['смартфон', 'smartphone', 'телефон', 'phone', 'мобильный телефон'],
            'планшеты': ['планшет', 'tablet', 'ipad'],
            'ноутбуки': ['ноутбук', 'laptop', 'macbook'],
            'часы': ['часы', 'watch', 'apple watch'],
            'наушники': ['наушники', 'headphones', 'airpods', 'earbuds'],
            'компьютеры': ['компьютер', 'computer', 'pc', 'desktop', 'imac']
        }

        search_terms = name_mapping.get(category_lower, [category_lower])

        def search_in_categories(cats, terms):
            """Рекурсивный поиск категории в дереве."""
            if not isinstance(cats, (list, dict)):
                return None

            if isinstance(cats, list):
                for cat in cats:
                    result = search_in_categories(cat, terms)
                    if result:
                        return result
            elif isinstance(cats, dict):
                # Проверяем название категории
                cat_name = cats.get('name', '').lower()
                if any(term in cat_name for term in terms):
                    cat_id = cats.get('id')
                    logger.info(f"Found category '{cats.get('name')}' with ID: {cat_id}")
                    return cat_id

                # Ищем в дочерних категориях
                if 'children' in cats:
                    result = search_in_categories(cats['children'], terms)
                    if result:
                        return result

            return None

        category_id = search_in_categories(items, search_terms)
        if category_id:
            return category_id

        logger.warning(f"Category '{category_name}' not found in API response")
        return None

    async def get_market_albums(self) -> Optional[List[Dict]]:
        """
        Получить список подборок (альбомов) товаров через market.getAlbums.

        Returns:
            Список подборок или None
        """
        if self._albums_cache:
            return self._albums_cache

        try:
            await self._wait_for_api_interval()
            response = self.vk.market.getAlbums(
                owner_id=-abs(int(VK_GROUP_ID)),
                count=100
            )

            if response and 'items' in response:
                self._albums_cache = response['items']
                return response['items']
            return []
        except Exception as e:
            logger.error(f"Error getting market albums: {str(e)}")
            return None

    async def find_collection_id(self, collection_name: str) -> Optional[int]:
        """
        Найти ID подборки по названию.

        Args:
            collection_name: Название подборки (например, "iPhone б/у")

        Returns:
            ID подборки или None
        """
        if not collection_name:
            return None

        # Обновляем кэш подборок перед поиском
        self._albums_cache = None
        albums = await self.get_market_albums()

        if not albums:
            logger.warning("No albums found in VK group")
            return None

        collection_lower = collection_name.lower().strip()
        logger.info(f"Searching for collection: '{collection_name}' (normalized: '{collection_lower}')")
        logger.info(f"Available albums: {[album.get('title', '') for album in albums]}")

        for album in albums:
            album_title = album.get('title', '').lower().strip()
            if album_title == collection_lower:
                album_id = album.get('id')
                logger.info(f"Found collection '{collection_name}' with ID: {album_id}")
                return album_id

        logger.warning(f"Collection '{collection_name}' not found in available albums")
        return None

    async def upload_product_photos(self, photo_file_ids: List[str], post: Post) -> List[Dict]:
        """
        Загрузить фотографии товара на сервер ВК (первые 5).

        Args:
            photo_file_ids: Список file_id фотографий из Telegram
            post: Объект поста

        Returns:
            Список словарей с данными фотографий: [{'id': int, 'owner_id': int}, ...]
        """
        photo_data_list = []

        # Берем первые 5 фотографий
        photos_to_upload = photo_file_ids[:5]

        for idx, file_id in enumerate(photos_to_upload):
            try:
                # Скачиваем фото из Telegram
                photo_data = await self.download_telegram_file(file_id)
                if not photo_data:
                    logger.error(f"Failed to download photo {file_id}")
                    continue

                # Сохраняем во временный файл
                import tempfile
                import os
                temp_file = f"/tmp/product_{file_id}.jpg"
                with open(temp_file, "wb") as f:
                    f.write(photo_data)

                # Проверяем размер изображения и обрезаем первую фотографию, если она альбомная
                try:
                    from PIL import Image
                    img = Image.open(temp_file)
                    width, height = img.size
                    
                    # Для первой фотографии (главной): если она альбомная, обрезаем до портретной ориентации
                    # VK Market требует портретную ориентацию для главной фотографии товара
                    # Используем соотношение сторон 3:4 (как у работающих портретных фотографий 960x1280)
                    if idx == 0 and width > height:
                        logger.info(f"Cropping main photo from landscape ({width}x{height}) to portrait 3:4")
                        # Вычисляем новую ширину для соотношения 3:4 при текущей высоте
                        # Для соотношения 3:4 (ширина:высота): ширина = высота * 3/4
                        new_width = int(height * 3 / 4)
                        # Убеждаемся, что новая ширина не меньше минимального требования VK (400px)
                        if new_width < 400:
                            logger.warning(f"Calculated width {new_width} is less than minimum 400px, using 400px")
                            new_width = 400
                        
                        # Обрезаем слева и справа поровну
                        left = (width - new_width) // 2
                        right = left + new_width
                        img = img.crop((left, 0, right, height))
                        width, height = img.size
                        
                        # Сохраняем обрезанное изображение
                        img.save(temp_file, "JPEG", quality=95)
                        logger.info(f"Main photo cropped to portrait 3:4 ({width}x{height})")
                    
                    img.close()

                    if width < 400 or height < 400:
                        logger.warning(f"Image {file_id} size {width}x{height} is less than minimum 400x400px required by VK Market")
                except ImportError:
                    logger.debug("PIL/Pillow not available, skipping image size check")
                except Exception as e:
                    logger.warning(f"Could not check image size for {file_id}: {str(e)}")

                # Загружаем фото на сервер ВК для товаров
                try:
                    await self._wait_for_api_interval()

                    # URL загрузки: в актуальном сценарии VK — market.getProductPhotoUploadServer;
                    # ранее использовался photos.getMarketUploadServer (в новых версиях API может отвечать [3] Unknown method).
                    # Главная фотография по-прежнему задаётся в market.add через main_photo_id.
                    upload_server_params = {
                        'group_id': abs(int(VK_GROUP_ID))
                    }

                    try:
                        upload_server = self.vk.market.getProductPhotoUploadServer(**upload_server_params)
                    except ApiError as e:
                        err_code = getattr(e, "code", None)
                        if err_code is None and isinstance(getattr(e, "error", None), dict):
                            err_code = e.error.get("error_code")
                        if err_code == 3:
                            upload_server = self.vk.photos.getMarketUploadServer(**upload_server_params)
                        else:
                            raise
                    upload_url = upload_server['upload_url']

                    # Загружаем файл на сервер VK
                    with open(temp_file, 'rb') as f:
                        files = {'file': f}
                        upload_response = requests.post(upload_url, files=files)
                        upload_response.raise_for_status()
                        upload_data = upload_response.json()

                    await self._wait_for_api_interval()
                    photo_result = self._save_market_uploaded_product_photo(upload_data)

                    if photo_result and len(photo_result) > 0:
                        photo_info = photo_result[0]
                        # Сохраняем и id, и owner_id для каждой фотографии
                        photo_data_list.append({
                            'id': photo_info['id'],
                            'owner_id': photo_info['owner_id']
                        })

                        logger.info(f"Uploaded product photo {file_id} to VK, got ID {photo_info['id']}")

                    # Удаляем временный файл
                    if os.path.exists(temp_file):
                        os.unlink(temp_file)

                except Exception as e:
                    logger.error(f"Error uploading product photo {file_id}: {str(e)}")
                    if os.path.exists(temp_file):
                        os.unlink(temp_file)
                    continue

            except Exception as e:
                logger.error(f"Error processing product photo {file_id}: {str(e)}")
                continue

        return photo_data_list

    async def upload_product_video(self, video_file_id: str, post: Post) -> Optional[int]:
        """
        Загрузить видео товара на сервер ВК.

        Args:
            video_file_id: file_id видео из Telegram
            post: Объект поста

        Returns:
            ID загруженного видео или None
        """
        try:
            # Скачиваем видео из Telegram
            video_data = await self.download_telegram_file(video_file_id)
            if not video_data:
                logger.error(f"Failed to download video {video_file_id}")
                return None

            # Сохраняем во временный файл
            import tempfile
            import os
            temp_file = f"/tmp/product_video_{video_file_id}.mp4"
            with open(temp_file, "wb") as f:
                f.write(video_data)

            # Загружаем видео на сервер ВК
            try:
                await self._wait_for_api_interval()
                upload_result = self.upload.video(
                    video_file=temp_file,
                    name=post.name or "Product video",
                    description="Product video",
                    group_id=abs(int(VK_GROUP_ID))
                )

                video_id = upload_result.get('video_id')
                if video_id:
                    logger.info(f"Uploaded product video {video_file_id} to VK, got ID {video_id}")

                # Удаляем временный файл
                if os.path.exists(temp_file):
                    os.unlink(temp_file)

                return video_id
            except Exception as e:
                logger.error(f"Error uploading product video {video_file_id}: {str(e)}")
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
                return None

        except Exception as e:
            logger.error(f"Error processing product video {video_file_id}: {str(e)}")
            return None

    async def publish_product(
        self,
        post_id: str,
        product_data: Dict,
        category_id: Optional[int],
        category_name: Optional[str],
        collection_id: Optional[int],
        photo_data_list: List[Dict],
        video_id: Optional[int]
    ) -> Optional[Dict]:
        """
        Опубликовать товар через market.add.

        Args:
            post_id: ID поста
            product_data: Словарь с данными товара (name, description, price)
            category_id: ID категории ВК
            collection_id: ID подборки ВК
            photo_data_list: Список словарей с данными фотографий [{'id': int, 'owner_id': int}, ...]
            video_id: ID видео (опционально)

        Returns:
            Результат публикации или None
        """
        if not photo_data_list:
            logger.error("No photos to publish product")
            return None

        try:
            await self._wait_for_api_interval()

            # Используем только ID фотографии (целое число), а не формат owner_id_photo_id
            main_photo = photo_data_list[0]
            main_photo_id = main_photo['id']  # Используем только ID фотографии

            # Подготавливаем параметры для market.add
            params = {
                'owner_id': -abs(int(VK_GROUP_ID)),
                'name': product_data.get('name', ''),
                'description': product_data.get('description', ''),
                'main_photo_id': main_photo_id,  # Используем только ID фотографии (целое число)
            }

            # Добавляем category_id только если он найден
            # Если не указан, ВК использует категорию по умолчанию группы
            if category_id:
                params['category_id'] = category_id
                logger.info(f"Using category_id: {category_id} for category: {category_name}")
            else:
                logger.info(f"Category ID not specified, VK will use group default category")

            # Добавляем дополнительные фотографии (только ID)
            if len(photo_data_list) > 1:
                additional_photos = [str(photo['id']) for photo in photo_data_list[1:]]
                params['photo_ids'] = ','.join(additional_photos)

            # Добавляем цену, если есть
            price = product_data.get('price', '')
            if price:
                # Извлекаем число из строки цены
                price_match = re.search(r'(\d+)', price.replace(' ', '').replace(',', ''))
                if price_match:
                    params['price'] = int(price_match.group(1))

            # Добавляем видео, если есть
            # В VK API для товаров видео добавляется через параметр video_ids (множественное число)
            if video_id:
                params['video_ids'] = str(video_id)  # video_ids принимает строку с ID видео
                logger.info(f"Adding video {video_id} to product")

            # Добавляем небольшую задержку перед вызовом market.add,
            # чтобы убедиться, что фотографии полностью обработаны сервером VK
            await asyncio.sleep(1)
            await self._wait_for_api_interval()

            # Публикуем товар
            result = self.vk.market.add(**params)
            logger.info(f"Product published to VK Market: {result}")

            # Добавляем товар в подборку после публикации (если указана)
            if collection_id and result:
                try:
                    vk_product_id = result.get('market_item_id') or result.get('item_id')
                    if vk_product_id:
                        await self._wait_for_api_interval()
                        # Используем market.addToAlbum для добавления товара в подборку
                        # album_ids должен быть строкой с ID альбома
                        self.vk.market.addToAlbum(
                            owner_id=-abs(int(VK_GROUP_ID)),
                            item_id=vk_product_id,
                            album_ids=str(collection_id)
                        )
                        logger.info(f"Product {vk_product_id} added to collection {collection_id}")
                except Exception as e:
                    logger.error(f"Error adding product to collection: {str(e)}")
                    # Не прерываем выполнение, товар уже опубликован

            return result
        except Exception as e:
            logger.error(f"Error publishing product to VK Market: {str(e)}")
            return None

    async def update_product_price(self, vk_product_id: int, price: int):
        """
        Обновить цену товара в ВК через market.edit.

        Args:
            vk_product_id: ID товара в ВК
            price: Новая цена (число в копейках или рублях, зависит от настроек ВК)

        Returns:
            True если успешно, False в противном случае
        """
        token_src = market_token_source()
        agent_debug_log(
            "H1",
            "product_publisher.update_product_price:entry",
            "market.edit attempt",
            {
                "vk_product_id": vk_product_id,
                "price_rub": price,
                "token_source": token_src,
                "has_dedicated_market_token": bool(VK_MARKET_ACCESS_TOKEN),
            },
        )
        try:
            await self._wait_for_api_interval()

            # Обновляем цену через market.edit
            self.vk.market.edit(
                owner_id=-abs(int(VK_GROUP_ID)),
                item_id=vk_product_id,
                price=price
            )

            logger.info(f"Product {vk_product_id} price updated to {price}")
            agent_debug_log(
                "H1",
                "product_publisher.update_product_price:ok",
                "market.edit success",
                {"vk_product_id": vk_product_id, "price_rub": price, "token_source": token_src},
                run_id="post-fix",
            )
            return True
        except Exception as e:
            err_code = vk_api_error_code(e)
            agent_debug_log(
                "H1",
                "product_publisher.update_product_price:error",
                str(e),
                {
                    "vk_product_id": vk_product_id,
                    "price_rub": price,
                    "token_source": token_src,
                    "error_code": err_code,
                },
            )
            if err_code == 27 and not VK_MARKET_ACCESS_TOKEN:
                logger.error(
                    "market.edit unavailable with community token (error 27). "
                    "Set VK_MARKET_ACCESS_TOKEN to official user OAuth token (market scope)."
                )
            logger.error(f"Error updating product price in VK Market: {str(e)}")
            return False

    async def publish_product_to_vk(self, post_id: str) -> bool:
        """
        Главная функция публикации товара в ВК.

        Args:
            post_id: ID поста

        Returns:
            True если успешно, False иначе
        """
        try:
            from app.services.settings_service import get_settings_service

            _svc = get_settings_service()
            _publish_allowed = _svc.is_vk_market_publish_allowed()
            try:
                _s_vm = bool(_svc.is_vk_market_enabled())
            except Exception:
                _s_vm = None
        except Exception:
            _publish_allowed = bool(VK_MARKET_ENABLED)
            _s_vm = None
        if not _publish_allowed:
            logger.info("VK Market publishing disabled (env or app settings)")
            return False

        db = SessionLocal()
        try:
            # Получаем пост из базы
            post = db.query(Post).filter(Post.id == post_id).first()
            if not post:
                logger.error(f"Post {post_id} not found")
                return False

            # Проверяем, не опубликован ли уже товар для этого поста
            existing_product = db.query(Product).filter(Product.post_id == post_id).first()
            if existing_product and existing_product.vk_product_id:
                logger.info(f"Product already published for post {post_id}")
                return True

            # Парсим данные товара из текста поста
            product_data = parse_product_data(post.text)
            if not product_data.get('name'):
                logger.warning(f"Could not extract product name from post {post_id}")
                return False

            # Определяем категорию
            category_id = None
            category_name = None
            if VK_MARKET_AUTO_CATEGORY and product_data.get('category'):
                category_name = product_data['category']
                category_id = self.find_category_id(category_name)
                if not category_id:
                    logger.warning(f"Category '{category_name}' not found via API, will use group default category")
                    # Не устанавливаем category_id, чтобы ВК использовал категорию по умолчанию группы
                    category_name = "Смартфоны"  # Для отображения в БД
            else:
                # Не устанавливаем category_id, чтобы ВК использовал категорию по умолчанию группы
                category_name = "Смартфоны"  # Для отображения в БД

            # Определяем подборку
            collection_id = None
            collection_name = None
            if VK_MARKET_AUTO_COLLECTION and product_data.get('collection'):
                collection_name = product_data['collection']
                collection_id = await self.find_collection_id(collection_name)
                if not collection_id:
                    logger.warning(f"Collection '{collection_name}' not found, will create without collection")

            # Загружаем фотографии (первые 5)
            photo_data_list = []
            if post.photos:
                photo_data_list = await self.upload_product_photos(post.photos, post)
                if not photo_data_list:
                    logger.error(f"No photos uploaded for post {post_id}")
                    return False

            # Загружаем видео, если есть
            video_id = None
            if post.videos and len(post.videos) > 0:
                video_id = await self.upload_product_video(post.videos[0], post)

            # Публикуем товар
            result = await self.publish_product(
                post_id=post_id,
                product_data=product_data,
                category_id=category_id,
                category_name=category_name,
                collection_id=collection_id,
                photo_data_list=photo_data_list,
                video_id=video_id
            )

            if not result:
                logger.error(f"Failed to publish product for post {post_id}")
                return False

            # Сохраняем информацию о товаре в базу
            vk_product_id = result.get('market_item_id') or result.get('item_id')
            vk_product_link = f"https://vk.com/market{VK_GROUP_ID}?w=product-{abs(int(VK_GROUP_ID))}_{vk_product_id}"

            # Получаем ссылку на Telegram пост, если пост опубликован в Telegram
            telegram_link = None
            if post.is_published_telegram and post.telegram_link:
                telegram_link = post.telegram_link

            product = Product(
                post_id=post_id,
                vk_product_id=vk_product_id,
                vk_product_link=vk_product_link,
                telegram_link=telegram_link,
                name=product_data.get('name', ''),
                price=product_data.get('price'),
                category_id=category_id,
                category_name=category_name,
                collection_id=collection_id,
                collection_name=collection_name,
                status='active'
            )
            db.add(product)
            db.commit()

            logger.info(f"Product {vk_product_id} published successfully for post {post_id}")
            return True

        except Exception as e:
            logger.error(f"Error publishing product to VK for post {post_id}: {str(e)}")
            db.rollback()
            return False
        finally:
            db.close()


async def publish_product_to_vk(post_id: str) -> bool:
    """Publish a product to VK Market."""
    publisher = VKProductPublisher()
    return await publisher.publish_product_to_vk(post_id)
