"""Фоновый worker: обновление VK ID Access Token до истечения часа."""
from __future__ import annotations

import asyncio
import logging

from app.utils.vk_token_refresh import POLL_SEC, ensure_fresh_vk_token

logger = logging.getLogger(__name__)


class VkTokenRefreshWorker:
    def __init__(self) -> None:
        self.is_running = False

    def stop(self) -> None:
        self.is_running = False

    async def run(self) -> None:
        self.is_running = True
        logger.info("VK ID token refresh worker started")
        while self.is_running:
            try:
                result = await asyncio.to_thread(ensure_fresh_vk_token)
                if result:
                    logger.info("VK ID token refresh: %s", result)
            except Exception:
                logger.exception("VK ID token refresh tick failed")
            await asyncio.sleep(POLL_SEC)
