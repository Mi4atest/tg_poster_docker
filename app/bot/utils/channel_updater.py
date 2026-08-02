"""
Обновление сообщений в канале Telegram с полным прайсом (наличие ●/○).
Редактируются только существующие сообщения по AVAILABILITY_MESSAGE_IDS
(например 11728–11731). Новые посты в канал не создаются.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from aiogram.enums import ParseMode

from app.bot.utils.telegram_edit import edit_message_text_safe
from app.config.settings import AVAILABILITY_USE_CAPTION
from app.db.database import SessionLocal
from app.services.settings_service import get_settings_service
from app.utils.vk_channel_price_formatter import build_telegram_channel_price

logger = logging.getLogger(__name__)

NEW_COLLECTION_VALUES = {"iPhone новые", "Airpods", "Apple Watch", "iPad"}

TELEGRAM_MESSAGE_MAX_LENGTH = 4000
TELEGRAM_CAPTION_MAX_LENGTH = 1024


def _get_stored_message_ids() -> List[int]:
    """
    ID сообщений прайса в ТГ-канале.
    Только настройки (Отчёты и списки) → env AVAILABILITY_MESSAGE_IDS.
    Без fallback на products.*: при пустых настройках другого инстанса
    на общей БД не подтягиваются чужие ID.
    """
    _avail_ids = get_settings_service().get_availability_message_ids()
    if _avail_ids:
        return list(_avail_ids)
    return []


def _set_stored_message_ids(message_ids: List[int]) -> None:
    """Сохраняет ID в опорный product (best-effort; источник истины — settings)."""
    from sqlalchemy import text

    try:
        with SessionLocal() as db:
            row = db.execute(
                text(
                    """
                    SELECT id FROM products
                    WHERE collection_name = ANY(:cols)
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {"cols": list(NEW_COLLECTION_VALUES)},
            ).first()
            if not row:
                logger.warning("No new product row found to store availability_message_ids")
                return
            db.execute(
                text(
                    """
                    UPDATE products
                    SET availability_message_ids = :ids,
                        channel_message_id = :mid,
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {
                    "ids": json.dumps(message_ids) if message_ids else None,
                    "mid": message_ids[0] if message_ids else None,
                    "id": int(row[0]),
                },
            )
            db.commit()
    except Exception:
        logger.exception(
            "Failed to store availability_message_ids (non-fatal; settings IDs still apply)"
        )


def _build_price_parts(message_ids: List[int]) -> tuple[List[str], Dict[str, Any]]:
    cfg = get_settings_service().get_vk_channel_price_config()
    max_parts = max(1, len(message_ids))
    max_len = TELEGRAM_CAPTION_MAX_LENGTH if AVAILABILITY_USE_CAPTION else TELEGRAM_MESSAGE_MAX_LENGTH
    rendered = build_telegram_channel_price(
        with_links=bool(cfg.get("links_enabled", True)),
        marker_in_stock=cfg.get("marker_in_stock"),
        marker_on_order=cfg.get("marker_on_order"),
        max_len=max_len,
        max_parts=max_parts,
    )
    parts = list(rendered.parts or [rendered.text])
    if not parts:
        parts = ["—"]
    return parts, dict(rendered.stats or {})


async def get_or_create_availability_message(bot) -> Optional[int]:
    """
    Обновляет сообщения прайса в ТГ-канале (только edit существующих ID).
    """
    import asyncio

    chat_id = get_settings_service().get_telegram_channel_id()
    if not chat_id:
        logger.warning("TELEGRAM_CHANNEL_ID not set")
        return None

    message_ids = await asyncio.to_thread(_get_stored_message_ids)
    if not message_ids:
        logger.warning(
            "Нет ID сообщений для прайса. Задайте в Настройки → Отчёты и списки → "
            "ID сообщений «Наличие» (например: 11728,11729,11730,11731)."
        )
        return None

    parts, stats = await asyncio.to_thread(_build_price_parts, message_ids)
    logger.info(
        "TG price refresh: ids=%s parts=%s lens=%s matched=%s",
        message_ids,
        len(parts),
        [len(p) for p in parts],
        (stats or {}).get("matched_or_priced"),
    )

    if len(parts) > len(message_ids):
        logger.warning(
            "Прайс разбит на %s частей, а ID сообщений только %s — хвост не поместится",
            len(parts),
            len(message_ids),
        )

    edited = 0
    for i, mid in enumerate(message_ids):
        chunk = parts[i] if i < len(parts) else "—"
        try:
            if AVAILABILITY_USE_CAPTION:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=mid,
                    caption=chunk[:TELEGRAM_CAPTION_MAX_LENGTH],
                    parse_mode=ParseMode.HTML,
                )
                ok = True
            else:
                ok = await edit_message_text_safe(
                    bot,
                    chat_id=chat_id,
                    message_id=mid,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                    link_preview_disabled=True,
                )
            if ok:
                edited += 1
            else:
                logger.warning("Could not edit price message %s", mid)
        except Exception as e:
            logger.warning("Could not edit price message %s: %s", mid, e)

    try:
        await asyncio.to_thread(_set_stored_message_ids, message_ids)
    except Exception:
        logger.exception("store availability_message_ids raised (ignored)")

    return message_ids[0] if edited else None


async def update_availability_message(bot) -> bool:
    """Обновляет сообщения прайса в канале (только edit)."""
    mid = await get_or_create_availability_message(bot)
    return mid is not None
