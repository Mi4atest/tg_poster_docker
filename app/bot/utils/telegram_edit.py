"""Правки сообщений Telegram с учётом flood control и общего лимитера."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import LinkPreviewOptions

logger = logging.getLogger(__name__)

# Минимальный интервал между edit* в один канал (секунды).
_CHANNEL_EDIT_MIN_INTERVAL = 1.1
_channel_edit_lock = asyncio.Lock()
_last_channel_edit_at = 0.0


def parse_retry_seconds(err: Exception) -> int:
    s = str(err)
    m = re.search(r"[Rr]etry in (\d+) seconds", s) or re.search(r"retry after (\d+)", s, re.I)
    return int(m.group(1)) if m else 0


def _is_flood(err: Exception) -> bool:
    s = str(err).lower()
    return "flood control" in s or "too many requests" in s


def _is_not_modified(err: Exception) -> bool:
    s = str(err).lower()
    return "message is not modified" in s or "message not modified" in s


async def _wait_channel_slot() -> None:
    global _last_channel_edit_at
    async with _channel_edit_lock:
        elapsed = time.monotonic() - _last_channel_edit_at
        if elapsed < _CHANNEL_EDIT_MIN_INTERVAL:
            await asyncio.sleep(_CHANNEL_EDIT_MIN_INTERVAL - elapsed)
        _last_channel_edit_at = time.monotonic()


async def edit_message_text_safe(
    bot: Bot,
    *,
    chat_id: int | str,
    message_id: int,
    text: str,
    parse_mode: Optional[str | ParseMode] = None,
    link_preview_disabled: bool = False,
    max_attempts: int = 4,
    apply_rate_limit: bool = True,
) -> bool:
    """edit_message_text с паузой между правками и повтором при flood."""
    kwargs: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    if link_preview_disabled:
        kwargs["link_preview_options"] = LinkPreviewOptions(is_disabled=True)

    err: Optional[Exception] = None
    for attempt in range(max_attempts):
        if attempt > 0 and err is not None:
            sec = parse_retry_seconds(err)
            if sec > 0:
                logger.info(
                    "Flood control: waiting %ss before retry edit_message_text %s",
                    sec,
                    message_id,
                )
                await asyncio.sleep(sec + 0.5)
            elif not _is_flood(err):
                return False
        if apply_rate_limit:
            await _wait_channel_slot()
        try:
            await bot.edit_message_text(**kwargs)
            return True
        except TelegramBadRequest as e:
            err = e
            if _is_not_modified(e):
                return True
            if not _is_flood(e) and attempt >= max_attempts - 1:
                logger.warning("edit_message_text failed msg=%s: %s", message_id, e)
                return False
        except Exception as e:
            err = e
            if not _is_flood(e):
                logger.warning("edit_message_text failed msg=%s: %s", message_id, e)
                return False
    return False


async def edit_message_caption_safe(
    bot: Bot,
    *,
    chat_id: int | str,
    message_id: int,
    caption: str,
    parse_mode: Optional[str | ParseMode] = None,
    max_attempts: int = 4,
) -> bool:
    """edit_message_caption с паузой и повтором при flood."""
    kwargs: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": caption,
    }
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode

    err: Optional[Exception] = None
    for attempt in range(max_attempts):
        if attempt > 0 and err is not None:
            sec = parse_retry_seconds(err)
            if sec > 0:
                logger.info(
                    "Flood control: waiting %ss before retry edit_message_caption %s",
                    sec,
                    message_id,
                )
                await asyncio.sleep(sec + 0.5)
            elif not _is_flood(err):
                return False
        await _wait_channel_slot()
        try:
            await bot.edit_message_caption(**kwargs)
            return True
        except TelegramBadRequest as e:
            err = e
            if _is_not_modified(e):
                return True
            if not _is_flood(e) and attempt >= max_attempts - 1:
                logger.warning("edit_message_caption failed msg=%s: %s", message_id, e)
                return False
        except Exception as e:
            err = e
            if not _is_flood(e):
                logger.warning("edit_message_caption failed msg=%s: %s", message_id, e)
                return False
    return False
