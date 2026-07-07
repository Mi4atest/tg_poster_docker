"""
Обновление сообщений в канале Telegram с актуальным наличием новых товаров.
При изменении цены или статуса только редактируются существующие сообщения (100, 101, 102…),
новые не создаются. Если список длинный — разбивается на части по лимиту Telegram (4096).
"""
import json
import logging
from typing import Optional, List, Dict, Any

from aiogram.exceptions import TelegramBadRequest

from app.config.settings import AVAILABILITY_USE_CAPTION
from app.db.database import SessionLocal
from app.api.models.product import Product
from app.services.settings_service import get_settings_service
from app.utils.new_products_formatter import format_availability_list

logger = logging.getLogger(__name__)

NEW_COLLECTION_VALUES = {"iPhone новые", "Airpods", "Apple Watch", "iPad"}

# Лимит длины одного сообщения Telegram (оставляем запас)
TELEGRAM_MESSAGE_MAX_LENGTH = 4080
# Лимит подписи к фото/медиа в Telegram
TELEGRAM_CAPTION_MAX_LENGTH = 1024


def _get_new_products_from_db() -> List[Dict[str, Any]]:
    """Возвращает список новых товаров (по collection_name) в виде словарей."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Product)
            .filter(Product.collection_name.in_(NEW_COLLECTION_VALUES))
            .filter(Product.status == "active")
            .all()
        )
        return [
            {
                "id": p.id,
                "name": p.name,
                "price": p.price,
                "availability_status": p.availability_status,
                "collection_name": p.collection_name,
            }
            for p in rows
        ]
    finally:
        db.close()


def _get_anchor_product() -> Optional[Product]:
    """Возвращает «опорный» товар новых (первый по id) для хранения ID сообщений."""
    db = SessionLocal()
    try:
        return (
            db.query(Product)
            .filter(Product.collection_name.in_(NEW_COLLECTION_VALUES))
            .order_by(Product.id)
            .limit(1)
            .first()
        )
    finally:
        db.close()


def _get_stored_message_ids() -> List[int]:
    """
    Возвращает список ID сообщений в канале с наличием.
    Приоритет: env AVAILABILITY_MESSAGE_IDS (если задан) > БД опорного товара.
    Так можно привязать бота к сообщениям 100, 101, … через .env, даже если в БД лежат старые id.
    """
    _avail_ids = get_settings_service().get_availability_message_ids()
    if _avail_ids:
        return list(_avail_ids)
    db = SessionLocal()
    try:
        row = (
            db.query(Product)
            .filter(Product.collection_name.in_(NEW_COLLECTION_VALUES))
            .order_by(Product.id)
            .limit(1)
            .first()
        )
        if not row:
            return []
        if row.availability_message_ids:
            try:
                ids = json.loads(row.availability_message_ids)
                if isinstance(ids, list):
                    out = [int(x) for x in ids if isinstance(x, (int, float))]
                    return out
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        if row.channel_message_id is not None:
            return [row.channel_message_id]
        return []
    finally:
        db.close()


def _set_stored_message_ids(message_ids: List[int]) -> None:
    """Сохраняет список ID сообщений в «опорном» товаре новых."""
    db = SessionLocal()
    try:
        row = (
            db.query(Product)
            .filter(Product.collection_name.in_(NEW_COLLECTION_VALUES))
            .order_by(Product.id)
            .limit(1)
            .first()
        )
        if row:
            row.availability_message_ids = json.dumps(message_ids) if message_ids else None
            row.channel_message_id = message_ids[0] if message_ids else None
            db.commit()
        else:
            logger.warning("No new product row found to store availability_message_ids")
    finally:
        db.close()


def _split_text_into_chunks(full_text: str, max_len: int = TELEGRAM_MESSAGE_MAX_LENGTH) -> List[str]:
    """
    Разбивает текст на части по max_len символов, режем по границам строк,
    чтобы не обрывать строку посередине.
    """
    if not full_text or len(full_text) <= max_len:
        return [full_text] if full_text else []
    chunks = []
    rest = full_text
    while rest:
        if len(rest) <= max_len:
            chunks.append(rest)
            break
        block = rest[:max_len]
        last_newline = block.rfind("\n")
        if last_newline > max_len // 2:
            cut = last_newline + 1
        else:
            cut = max_len
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    return chunks


async def get_or_create_availability_message(bot) -> Optional[int]:
    """
    Обновляет сообщение(я) в канале с актуальным наличием.
    Текст разбивается на части по лимиту Telegram; каждая часть редактирует
    соответствующее существующее сообщение (100, 101, 102…). Новые сообщения
    не создаются при обновлениях; создаются только если сообщений ещё не было
    или список вырос и не хватает сообщений.
    """
    import asyncio

    AVAILABILITY_MESSAGE_IDS = get_settings_service().get_availability_message_ids()
    chat_id = get_settings_service().get_telegram_channel_id()
    if not chat_id:
        logger.warning("TELEGRAM_CHANNEL_ID not set")
        return None
    products = await asyncio.to_thread(_get_new_products_from_db)
    full_text = format_availability_list(products)
    max_chunk_len = TELEGRAM_CAPTION_MAX_LENGTH if AVAILABILITY_USE_CAPTION else TELEGRAM_MESSAGE_MAX_LENGTH
    chunks = _split_text_into_chunks(full_text, max_len=max_chunk_len)
    if not chunks:
        chunks = [""]
    message_ids = await asyncio.to_thread(_get_stored_message_ids)

    # Без сохранённых ID не отправляем новые сообщения — только редактируем существующие
    if not message_ids:
        logger.warning(
            "Нет сохранённых ID сообщений для списка наличия. "
            "Добавьте в .env: AVAILABILITY_MESSAGE_IDS=100 (или 100,101,102) и перезапустите бота, "
            "либо один раз вручную задайте channel_message_id/availability_message_ids в БД у любого товара из новых."
        )
        return None

    # Редактируем только существующие сообщения; новые не создаём
    need_save = False
    for i, chunk in enumerate(chunks):
        if i < len(message_ids):
            mid = message_ids[i]
            try:
                if AVAILABILITY_USE_CAPTION:
                    await bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=mid,
                        caption=chunk,
                    )
                else:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=mid,
                        text=chunk,
                    )
                if AVAILABILITY_MESSAGE_IDS:
                    need_save = True  # сохранить в БД при первом успешном редактировании с env
            except TelegramBadRequest as e:
                err_msg = (str(e) or "").lower()
                if "message is not modified" in err_msg or "message not modified" in err_msg:
                    pass
                elif "message to edit not found" in err_msg or "message not found" in err_msg or "message can't be edited" in err_msg:
                    if AVAILABILITY_USE_CAPTION:
                        logger.warning(
                            "Сообщение %s не удалось отредактировать (подпись). Проверьте, что это id сообщения с фото/медиа с подписью (первое в медиагруппе).",
                            mid,
                        )
                    else:
                        logger.warning(
                            "Сообщение %s не удалось отредактировать. Если это фото/медиа — задайте AVAILABILITY_USE_CAPTION=true и укажите id первого сообщения каждой медиагруппы (95, 101, 107…).",
                            mid,
                        )
                else:
                    logger.warning("Could not edit availability message %s: %s", mid, e)
            except Exception as e:
                logger.warning("Could not edit availability message %s: %s", message_ids[i], e)
        # Чанков больше, чем сообщений — только редактируем то, что есть; лишний контент не показываем
    # Лишние сообщения (чанков меньше, чем было) — очищаем текст/caption
    for j in range(len(chunks), len(message_ids)):
        try:
            if AVAILABILITY_USE_CAPTION:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_ids[j],
                    caption="—",
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_ids[j],
                    text="—",
                )
        except TelegramBadRequest as e:
            err_msg = (str(e) or "").lower()
            if "message is not modified" not in err_msg and "message not modified" not in err_msg:
                logger.warning("Could not clear availability message %s: %s", message_ids[j], e)
        except Exception as e:
            logger.warning("Could not clear availability message %s: %s", message_ids[j], e)
    stored_ids = await asyncio.to_thread(_get_stored_message_ids)
    if need_save or len(message_ids) != len(stored_ids):
        await asyncio.to_thread(_set_stored_message_ids, message_ids)

    return message_ids[0] if message_ids else None


async def update_availability_message(bot) -> bool:
    """
    Обновляет сообщения в канале с актуальным наличием.
    Редактирует только существующие сообщения; при отсутствии сообщений создаёт первое.
    """
    mid = await get_or_create_availability_message(bot)
    return mid is not None
