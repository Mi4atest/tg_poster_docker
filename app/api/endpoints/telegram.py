"""Публичный прокси файлов Telegram по file_id (витрина, Avito XML, воркеры)."""
from __future__ import annotations

import logging
import re

import aiohttp
from aiogram import Bot
from fastapi import APIRouter, HTTPException, Response

from app.config.settings import TELEGRAM_BOT_TOKEN

router = APIRouter()
logger = logging.getLogger(__name__)

_BOT_TOKEN_IN_URL = re.compile(r"/file/bot[^/\s]+")


def _redact(text: object) -> str:
    """Убирает токен бота из URL/текста перед записью в лог."""
    return _BOT_TOKEN_IN_URL.sub("/file/bot***", "" if text is None else str(text))


def _content_type_for_path(file_path: str) -> str:
    lower = (file_path or "").lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".mov"):
        return "video/quicktime"
    return "application/octet-stream"


@router.get("/file/{file_id}")
async def get_telegram_file(file_id: str):
    """Get a file from Telegram by file_id."""
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Telegram bot is not configured")

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    try:
        try:
            file_info = await bot.get_file(file_id)
            file_path = file_info.file_path
        except Exception as exc:
            logger.warning("Telegram getFile failed for file_id=%s: %s", file_id[:32], _redact(exc))
            raise HTTPException(status_code=404, detail="File not found") from None

        if not file_path:
            raise HTTPException(status_code=404, detail="File not found")

        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"

        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # TLS verification включена (по умолчанию); CERT_NONE / curl -k убраны.
                async with session.get(file_url) as response:
                    if response.status != 200:
                        body_preview = _redact((await response.text())[:200])
                        logger.warning(
                            "Telegram file download failed: status=%s body=%s",
                            response.status,
                            body_preview,
                        )
                        raise HTTPException(
                            status_code=502,
                            detail="Failed to download file from Telegram",
                        )
                    content = await response.read()
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Telegram file download error: %s", _redact(exc))
            raise HTTPException(
                status_code=502,
                detail="Failed to download file from Telegram",
            ) from None

        return Response(content=content, media_type=_content_type_for_path(file_path))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error in get_telegram_file: %s", _redact(exc))
        raise HTTPException(status_code=500, detail="Error getting file") from None
    finally:
        await bot.session.close()
