"""Фоновое обновление watchlist рынка Avito — по одной позиции, с circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta

from app.config.settings import (
    AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC,
    AVITO_MARKET_WL_BLOCK_PAUSE_SEC,
    AVITO_MARKET_WL_LIVE_INTERVAL_SEC,
    AVITO_MARKET_WL_SOFT_STREAK_PAUSE,
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
        self._soft_stale_streak = 0

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

    def _global_pause(self, now: datetime, *, reason: str) -> str:
        until = now + timedelta(seconds=AVITO_MARKET_WL_BLOCK_PAUSE_SEC)
        self._set_pause(until)
        self._soft_stale_streak = 0
        logger.warning(
            "Watchlist global pause %ss until %s (%s)",
            AVITO_MARKET_WL_BLOCK_PAUSE_SEC,
            until,
            reason,
        )
        # #region agent log
        try:
            from app.integrations.avito.debug_agent_log import agent_dbg

            agent_dbg(
                "E",
                "market_watchlist_worker.py:global_pause",
                "wl global pause",
                {
                    "pause_sec": AVITO_MARKET_WL_BLOCK_PAUSE_SEC,
                    "until": until.isoformat(),
                    "reason": reason[:80],
                },
                run_id="post-fix",
            )
        except Exception:
            pass
        # #endregion
        return "blocked"

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
            return self._global_pause(now, reason="unavailable")
        except Exception:
            logger.exception("Watchlist refresh failed id=%s", item.get("id"))
            return self._global_pause(now, reason="exception")

        logger.info(
            "Watchlist refresh id=%s model=%s %s outcome=%s",
            (updated or item).get("id"),
            item.get("model"),
            item.get("memory_gb"),
            outcome,
        )
        if outcome == "stale":
            live_tried = bool(
                estimate
                and (
                    getattr(estimate, "live_attempted", False)
                    or estimate.live_fetched
                )
            )
            if not live_tried:
                reason = str(getattr(estimate, "stale_reason", "") or "")
                # Суточный лимит: не крутить все 60 позиций каждую минуту — спать до окна.
                if "лимит" in reason.lower():
                    until = now + timedelta(seconds=AVITO_MARKET_WL_BLOCK_PAUSE_SEC)
                    self._set_pause(until)
                    # #region agent log
                    try:
                        from app.integrations.avito.debug_agent_log import agent_dbg

                        agent_dbg(
                            "H5",
                            "market_watchlist_worker.py:daily_limit_pause",
                            "wl pause on daily limit",
                            {
                                "item_id": item.get("id"),
                                "pause_sec": AVITO_MARKET_WL_BLOCK_PAUSE_SEC,
                                "until": until.isoformat(),
                            },
                            run_id="post-fix",
                        )
                    except Exception:
                        pass
                    # #endregion
                    logger.warning(
                        "Watchlist daily limit hit, pause %ss until %s",
                        AVITO_MARKET_WL_BLOCK_PAUSE_SEC,
                        until,
                    )
                    return "daily_limit"
                # #region agent log
                try:
                    from app.integrations.avito.debug_agent_log import agent_dbg

                    agent_dbg(
                        "H3",
                        "market_watchlist_worker.py:local_cooldown",
                        "wl stale without network attempt",
                        {
                            "item_id": item.get("id"),
                            "streak": self._soft_stale_streak,
                        },
                        run_id="post-fix",
                    )
                except Exception:
                    pass
                # #endregion
                return "cooldown"
            # Реальная сетевая неудача: одна ошибка ≠ стоп всего списка.
            self._soft_stale_streak += 1
            # #region agent log
            try:
                from app.integrations.avito.debug_agent_log import agent_dbg

                agent_dbg(
                    "H3",
                    "market_watchlist_worker.py:stale_continue",
                    "wl soft stale continue next",
                    {
                        "item_id": item.get("id"),
                        "streak": self._soft_stale_streak,
                        "streak_limit": AVITO_MARKET_WL_SOFT_STREAK_PAUSE,
                        "next_gap_sec": AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC,
                    },
                    run_id="post-fix",
                )
            except Exception:
                pass
            # #endregion
            if self._soft_stale_streak >= AVITO_MARKET_WL_SOFT_STREAK_PAUSE:
                return self._global_pause(
                    now, reason=f"soft_streak_{self._soft_stale_streak}"
                )
            jitter = random.randint(0, 60)
            await self._sleep(AVITO_MARKET_MIN_REQUEST_INTERVAL_SEC + jitter)
            return "stale"
        if outcome == "live":
            self._soft_stale_streak = 0
            jitter = random.randint(0, 90)
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
