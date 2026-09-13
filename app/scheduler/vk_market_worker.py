"""Фоновый worker: отложенные hide/price в VK Market (без серии коротких ретраев)."""
from __future__ import annotations

import asyncio
import logging

from app.utils.vk_market_ops import process_due_operation

logger = logging.getLogger(__name__)

POLL_SEC = 30


class VkMarketOpsWorker:
    def __init__(self) -> None:
        self.is_running = False

    def stop(self) -> None:
        self.is_running = False

    async def run(self) -> None:
        self.is_running = True
        logger.info("VK market ops worker started")
        while self.is_running:
            try:
                result = await asyncio.to_thread(process_due_operation)
                if result:
                    logger.info("VK market ops worker: %s", result)
            except Exception:
                logger.exception("VK market ops worker tick failed")
            await asyncio.sleep(POLL_SEC)
