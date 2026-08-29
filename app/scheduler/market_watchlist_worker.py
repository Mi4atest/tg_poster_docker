"""Фоновое обновление watchlist рынка Avito — по одной позиции, с circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta

from app.config.settings import (
    AVITO_MARKET_WL_BLOCK_PAUSE_SEC,
    AVITO_MARKET_WL_LIVE_INTERVAL_SEC,
    AVITO_MARKET_WL_TICK_SEC,
)
from app.db.avito_market_watchlist_queries import get_due_watchlist_item
from app.db.database import run_db
from app.services.iphone_market_price_service import MarketTemporarilyUnavailable
from app.services.iphone_market_watchlist_service import get_iphone_market_watchlist_service
from app.services.settings_service import get_settings_service


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MarketWatchlistWorker:
    def __init__(self) -> None:
        self.is_running = False

    def stop(self) -> None:
        self.is_running = False

    @staticmethod
    def _pause_until() -> datetime | None:
        try:
            return get_settings_service().get_avito_market_watchlist_pause_until()
        except Exception:
            return None

    @staticmethod
    def _set_pause(until: datetime | None) -> None:
        try:
            get_settings_service().set_avito_market_watchlist_pause_until(until)
        except Exception:
            logger.exception("Failed to persist watchlist pause")

    async def _sleep(self, seconds: float) -> None:
        remaining = max(1.0, float(seconds))
        while self.is_running and remaining > 0:
            step = min(5.0, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _handle_due(self) -> str:
        settings = get_settings_service()
        if not settings.is_avito_market_watchlist_enabled():
            return "disabled"
        pause = self._pause_until()
        now = _utcnow()
        if pause and pause > now:
            return "paused"
        item = await run_db(get_due_watchlist_item, now)
        if not item:
            return "idle"
        service = get_iphone_market_watchlist_service()
        try:
            updated, estimate, outcome = await service.refresh_item(
                int(item["id"]),
                source="watchlist",
            )
        except MarketTemporarilyUnavailable:
            until = now + timedelta(seconds=AVITO_MARKET_WL_BLOCK_PAUSE_SEC)
            self._set_pause(until)
            logger.warning("Watchlist refresh unavailable, pause until %s", until)
            return "blocked"
        except Exception:
            until = now + timedelta(seconds=AVITO_MARKET_WL_BLOCK_PAUSE_SEC)
            self._set_pause(until)
            logger.exception("Watchlist refresh failed id=%s", item.get("id"))
            return "blocked"

        logger.info(
            "Watchlist refresh id=%s model=%s %s outcome=%s",
            (updated or item).get("id"),
            item.get("model"),
            item.get("memory_gb"),
            outcome,
        )
        if outcome == "stale":
            until = now + timedelta(seconds=AVITO_MARKET_WL_BLOCK_PAUSE_SEC)
            self._set_pause(until)
            return "blocked"
        if outcome == "live":
            jitter = random.randint(0, 120)
            await self._sleep(AVITO_MARKET_WL_LIVE_INTERVAL_SEC + jitter)
            return "live"
        return outcome

    async def run(self) -> None:
        self.is_running = True
        logger.info("Avito market watchlist worker started")
        while self.is_running:
            try:
                await self._handle_due()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Avito market watchlist worker tick failed")
            if not self.is_running:
                break
            await self._sleep(AVITO_MARKET_WL_TICK_SEC)
        logger.info("Avito market watchlist worker stopped")
