"""
Обновление сообщений в канале Telegram со списком б/у товаров.
Тот же список, что в кнопке «Список б/у товаров», плюс блок «Новинки» (24 ч).
Редактируются только существующие сообщения по USED_PRODUCTS_LIST_MESSAGE_IDS, новые не создаются.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

from app.db.database import SessionLocal
from app.db.product_queries import fetch_used_products_for_list
from app.services.settings_service import get_settings_service
from app.bot.utils.product_list_formatter import format_full_products_list
from app.bot.utils.used_products_lists import RESERVED_SLOT_TEXT
from app.utils.product_formatter import format_product_name_for_list

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_MAX_LENGTH = 4080


def _get_used_products_from_db() -> List[Dict[str, Any]]:
    """Возвращает список б/у товаров (raw SQL, без ORM lazy-load)."""
    db = SessionLocal()
    try:
        return fetch_used_products_for_list(db)
    finally:
        db.close()


def _format_product_line(product: Dict[str, Any]) -> str:
    """Одна строка товара в формате списка (название со ссылкой, цена, ВК)."""
    formatted_name = format_product_name_for_list(product.get("name", "Без названия"))
    price = product.get("price", "") or "Цена не указана"
    telegram_link = product.get("telegram_link")
    vk_link = product.get("vk_product_link")
    line_parts = []
    if telegram_link:
        line_parts.append(f'<a href="{telegram_link}">{formatted_name}</a>')
    else:
        line_parts.append(formatted_name)
    line_parts.append(f"- {price}")
    if vk_link:
        line_parts.append(f'<a href="{vk_link}">ВК</a>')
    return " ".join(line_parts)


def _msk_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=3)


def _build_header(total: int, updated_at: Optional[datetime] = None) -> str:
    dt = updated_at or _msk_now()
    return (
        f"<b>📦 Каталог б/у ({total})</b>\n"
        f"Обновлено {dt.strftime('%d.%m.%y')} в {dt.strftime('%H:%M')}\n"
    )


def _published_at_to_utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Приводит дату публикации к naive UTC для сравнения (БД может вернуть aware в локальной TZ)."""
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt  # naive — считаем UTC (publisher пишет datetime.now(timezone.utc))

def _build_today_block(products: List[Dict[str, Any]]) -> str:
    """Товары, опубликованные в TG за последние 24 часа: блок «Новинки» жирным и в цитате."""
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = (now_utc - timedelta(hours=24)).replace(tzinfo=None)  # naive UTC для сравнения
    today_products = [
        p for p in products
        if p.get("published_telegram_at")
        and _published_at_to_utc_naive(p["published_telegram_at"]) is not None
        and _published_at_to_utc_naive(p["published_telegram_at"]) >= cutoff_utc
    ]
    if not today_products:
        return "<blockquote><b>🆕 Новинки:</b>\n—</blockquote>\n"
    lines = ["<b>🆕 Новинки:</b>"]
    for p in today_products:
        line = _format_product_line(p)
        lines.append(f"<b>🆕 {line}</b>")
    return "<blockquote>" + "\n".join(lines) + "\n</blockquote>\n"


def _build_full_text(products: List[Dict[str, Any]]) -> str:
    """Собирает полный текст: заголовок + блок новинок + разделитель + полный список."""
    total = len(products)
    header = _build_header(total)
    today_block = _build_today_block(products)
    separator = "━━━━━━━━━━━━━━\n"
    full_list = format_full_products_list(products)
    if full_list.startswith("📦 Список товаров пуст") or full_list.startswith("📦 Нет других"):
        full_list = ""
    return header + today_block + separator + full_list


def _split_text_into_chunks(full_text: str, max_len: int = TELEGRAM_MESSAGE_MAX_LENGTH) -> List[str]:
    """Разбивает текст на части по max_len символов по границам строк."""
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


async def update_used_products_list_in_channel(bot) -> bool:
    """
    Обновляет сообщения в канале списком б/у товаров (тот же контент, что в кнопке «Список б/у товаров»),
    с блоком «Новинки» (24 ч). Редактирует только существующие сообщения.
    Возвращает True при успехе, False если не заданы ID сообщений или канал.
    """
    TELEGRAM_CHANNEL_ID = get_settings_service().get_telegram_channel_id()
    USED_PRODUCTS_LIST_MESSAGE_IDS = get_settings_service().get_used_products_list_message_ids()
    if not TELEGRAM_CHANNEL_ID:
        logger.debug("USED_PRODUCTS_LIST: TELEGRAM_CHANNEL_ID not set, skip")
        return False
    if not USED_PRODUCTS_LIST_MESSAGE_IDS:
        logger.debug("USED_PRODUCTS_LIST: USED_PRODUCTS_LIST_MESSAGE_IDS not set, skip")
        return False

    products = await asyncio.to_thread(_get_used_products_from_db)
    full_text = _build_full_text(products)
    chunks = _split_text_into_chunks(full_text)
    if not chunks:
        chunks = [RESERVED_SLOT_TEXT]

    message_ids = list(USED_PRODUCTS_LIST_MESSAGE_IDS)
    chat_id = TELEGRAM_CHANNEL_ID
    edited = 0

    for i, chunk in enumerate(chunks):
        if i >= len(message_ids):
            break
        mid = message_ids[i]
        from app.bot.utils.telegram_edit import edit_message_text_safe

        ok = await edit_message_text_safe(
            bot,
            chat_id=chat_id,
            message_id=mid,
            text=chunk,
            parse_mode="HTML",
            link_preview_disabled=True,
        )
        if ok:
            edited += 1
        else:
            logger.warning("Could not edit used products list message %s", mid)

    for j in range(len(chunks), len(message_ids)):
        from app.bot.utils.telegram_edit import edit_message_text_safe

        ok = await edit_message_text_safe(
            bot,
            chat_id=chat_id,
            message_id=message_ids[j],
            text=RESERVED_SLOT_TEXT,
            parse_mode="HTML",
        )
        if not ok:
            logger.warning("Could not clear used products list message %s", message_ids[j])

    return edited > 0
