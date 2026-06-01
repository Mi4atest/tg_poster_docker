"""
Утилита для синхронизации товаров из подборок ВК маркета.
"""
import logging
import re
import asyncio
from typing import List, Optional, Dict, Any

from app.config.settings import VK_GROUP_ID
from app.utils.vk_client import get_market_vk_session
from app.db.database import SessionLocal
from app.api.models.product import Product
from app.api.models.post import Post

logger = logging.getLogger(__name__)

# post_id для товаров, синхронизированных из ВК (без привязки к посту "Создать пост")
POST_ID_FOR_NEW_PRODUCTS = "vk_new_products_sync"

# Названия подборок для новых товаров
NEW_PRODUCT_COLLECTIONS = [
    "iPhone новые",
    "Airpods",
    "Apple Watch",
    "iPad",
]


def _format_vk_price(price_value: Optional[int]) -> Optional[str]:
    """Форматирует цену из ВК API: цены приходят в копейках, делим на 100."""
    if price_value is None:
        return None
    # Преобразуем в int, если это строка
    if isinstance(price_value, str):
        try:
            price_value = int(price_value)
        except (ValueError, TypeError):
            return None
    # Если цена слишком большая (например, 13590000), делим на 100 (цены в ВК в копейках)
    if price_value > 1000000:
        price_value = price_value // 100
    return f"{price_value}₽"


def _get_vk_api():
    """Получить экземпляр VK API (market token)."""
    return get_market_vk_session(api_version="5.199").get_api()


def get_vk_products_from_collection(collection_name: str) -> List[Dict[str, Any]]:
    """
    Получить товары из подборки ВК по названию.

    Args:
        collection_name: Название подборки (например, "iPhone новые", "Airpods")

    Returns:
        Список товаров из ВК API (поля id, owner_id, title, price, etc.)
    """
    vk = _get_vk_api()
    owner_id = -abs(int(VK_GROUP_ID))

    # Получить список подборок
    try:
        albums_resp = vk.market.getAlbums(owner_id=owner_id, count=100)
        albums = albums_resp.get("items", [])
    except Exception as e:
        logger.error(f"Error getting VK market albums: {e}")
        return []

    collection_lower = collection_name.lower().strip()
    album_id = None
    for album in albums:
        if album.get("title", "").lower().strip() == collection_lower:
            album_id = album.get("id")
            break

    if album_id is None:
        logger.warning(f"Collection '{collection_name}' not found in VK")
        return []

    # Получить товары из подборки
    all_items = []
    offset = 0
    count = 200
    while True:
        try:
            resp = vk.market.get(
                owner_id=owner_id,
                album_id=album_id,
                count=count,
                offset=offset,
                extended=0,
            )
            items = resp.get("items", [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < count:
                break
            offset += count
        except Exception as e:
            logger.error(f"Error getting VK market items: {e}")
            break

    return all_items


def _vk_item_to_product_dict(item: Dict, collection_name: str, collection_id: int) -> Dict:
    """Преобразовать товар из ответа ВК в словарь для Product."""
    vk_product_id = item.get("id")
    owner_id = item.get("owner_id", -abs(int(VK_GROUP_ID)))
    vk_product_link = f"https://vk.com/market{VK_GROUP_ID}?w=product-{abs(owner_id)}_{vk_product_id}"
    title = item.get("title", "Без названия")
    price_obj = item.get("price", {})
    price_value = price_obj.get("amount") if isinstance(price_obj, dict) else None
    price_str = _format_vk_price(price_value)
    return {
        "vk_product_id": vk_product_id,
        "vk_product_link": vk_product_link,
        "name": title,
        "price": price_str,
        "collection_id": collection_id,
        "collection_name": collection_name,
    }


def _get_or_create_sync_post(db) -> Optional[str]:
    """Получить или создать пост-заглушку для синхронизированных товаров."""
    post = db.query(Post).filter(Post.id == POST_ID_FOR_NEW_PRODUCTS).first()
    if post:
        return post.id
    try:
        post = Post(id=POST_ID_FOR_NEW_PRODUCTS, text="Синхронизация товаров из ВК")
        db.add(post)
        db.commit()
        return post.id
    except Exception as e:
        logger.error(f"Error creating sync post: {e}")
        db.rollback()
        return None


def sync_new_products_from_vk() -> Dict[str, int]:
    """
    Синхронизировать все новые товары из подборок ВК в БД.

    Returns:
        Словарь {collection_name: количество синхронизированных товаров}
    """
    vk = _get_vk_api()
    owner_id = -abs(int(VK_GROUP_ID))
    db = SessionLocal()
    result = {}

    try:
        post_id = _get_or_create_sync_post(db)
        if not post_id:
            return result

        albums_resp = vk.market.getAlbums(owner_id=owner_id, count=100)
        albums = {a.get("title", "").lower().strip(): a for a in albums_resp.get("items", [])}

        for collection_name in NEW_PRODUCT_COLLECTIONS:
            collection_lower = collection_name.lower().strip()
            album = albums.get(collection_lower)
            if not album:
                logger.warning(f"Collection '{collection_name}' not found")
                result[collection_name] = 0
                continue

            album_id = album.get("id")
            count_synced = 0
            offset = 0
            count = 200
            while True:
                try:
                    resp = vk.market.get(
                        owner_id=owner_id,
                        album_id=album_id,
                        count=count,
                        offset=offset,
                        extended=0,
                    )
                    items = resp.get("items", [])
                    if not items:
                        break
                    for item in items:
                        vk_product_id = item.get("id")
                        existing = db.query(Product).filter(
                            Product.vk_product_id == vk_product_id,
                            Product.collection_name == collection_name,
                        ).first()
                        data = _vk_item_to_product_dict(item, collection_name, album_id)
                        if existing:
                            existing.name = data["name"]
                            existing.price = data["price"]
                            existing.vk_product_link = data["vk_product_link"]
                            existing.collection_id = data["collection_id"]
                            db.flush()
                            count_synced += 1
                        else:
                            product = Product(
                                post_id=post_id,
                                vk_product_id=data["vk_product_id"],
                                vk_product_link=data["vk_product_link"],
                                name=data["name"],
                                price=data["price"],
                                collection_id=data["collection_id"],
                                collection_name=data["collection_name"],
                                status="active",
                            )
                            db.add(product)
                            count_synced += 1
                    if len(items) < count:
                        break
                    offset += count
                except Exception as e:
                    logger.error(f"Error syncing collection '{collection_name}': {e}")
                    break
            db.commit()
            result[collection_name] = count_synced
    finally:
        db.close()
    return result
