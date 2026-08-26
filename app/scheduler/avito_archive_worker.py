"""Фоновый worker: очередь снятия + единый диспетчер (авто-режим)."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.db.database import SessionLocal
from app.integrations.avito.archive_queue import reconcile_pending_with_avito
from app.integrations.avito.avito_feed_dispatcher import is_manual_feed_upload, try_auto_upload
from app.scheduler.queue_manager import QueueManager
from app.services.settings_service import get_settings_service

logger = logging.getLogger(__name__)


class AvitoArchiveWorker:
    def __init__(self, queue_manager: QueueManager, orchestrator=None) -> None:
        self.queue_manager = queue_manager
        self.orchestrator = orchestrator
        self.is_running = False

    def stop(self) -> None:
        self.is_running = False

    def _is_paused(self) -> bool:
        if self.orchestrator:
            if self.orchestrator.global_pause:
                return True
            avito = self.orchestrator.workers.get("avito")
            if avito and avito.is_paused:
                return True
        try:
            return get_settings_service().is_publishing_paused("avito")
        except Exception:
            return False

    async def run(self) -> None:
        self.is_running = True
        logger.info("Avito feed worker started (manual=%s)", is_manual_feed_upload())
        while self.is_running:
            try:
                if self._is_paused():
                    await asyncio.sleep(1)
                    continue
                await reconcile_pending_with_avito()
                if not is_manual_feed_upload():
                    db = SessionLocal()
                    try:
                        await try_auto_upload(db, self.queue_manager)
                    finally:
                        db.close()
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Avito feed worker error: %s", e, exc_info=True)
                await asyncio.sleep(20)
        logger.info("Avito feed worker stopped")
