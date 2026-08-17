"""Обновление каталогов б/у в Telegram и Max одним вызовом."""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Текст незанятого зарезервированного слота каталога (TG и Max).
RESERVED_SLOT_TEXT = "зарезервировано ⬇️"


async def refresh_used_products_catalogs(bot: Optional[Any] = None) -> dict[str, bool]:
    """Правит зарезервированные сообщения каталога б/у в TG (если передан bot) и в Max."""
    result = {"telegram": False, "max": False}
    if bot is not None:
        try:
            from app.bot.utils.used_products_channel_updater import (
                update_used_products_list_in_channel,
            )

            result["telegram"] = await update_used_products_list_in_channel(bot)
        except Exception:
            logger.exception("Failed to update used products list in Telegram channel")
    try:
        from app.bot.utils.used_products_max_channel_updater import (
            update_used_products_list_in_max_channel,
        )

        result["max"] = await update_used_products_list_in_max_channel()
    except Exception:
        logger.exception("Failed to update used products list in Max channel")
    return result
