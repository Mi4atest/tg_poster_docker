"""Оркестрация управляемого watchlist рынка Avito."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from app.config.settings import (
    AVITO_MARKET_WL_BLOCK_PAUSE_SEC,
    AVITO_MARKET_WL_TIER_DAILY_SEC,
    AVITO_MARKET_WL_TIER_SLOW_SEC,
)
from app.db.avito_market_queries import list_success_snapshot_configs
from app.db.avito_market_watchlist_queries import (
    add_watchlist_item,
    delete_watchlist_item,
    get_due_watchlist_item,
    get_watchlist_item,
    get_watchlist_item_by_config,
    is_vintage_market_model,
    list_used_catalog_configs,
    list_watchlist_items,
    list_watchlist_keys,
    update_watchlist_item,
)
from app.db.database import run_db
from app.services.iphone_market_price_service import (
    MarketPriceEstimate,
    get_iphone_market_price_service,
)
from app.utils.iphone_market_query import IphoneMarketQuery
from app.utils.iphone_parser import sort_models_for_display


logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def tier_interval_sec(tier: str) -> int:
    if tier == "slow":
        return max(3600, AVITO_MARKET_WL_TIER_SLOW_SEC)
    return max(3600, AVITO_MARKET_WL_TIER_DAILY_SEC)


def compute_next_refresh_at(
    tier: str,
    *,
    now: Optional[datetime] = None,
    last_refreshed_at: Optional[datetime] = None,
) -> datetime:
    moment = now or _utcnow()
    base = last_refreshed_at or moment
    nxt = base + timedelta(seconds=tier_interval_sec(tier))
    return nxt if nxt > moment else moment


def sort_watchlist_rows(rows: list[dict]) -> list[dict]:
    models = [str(row.get("model") or "") for row in rows]
    order = {name: index for index, name in enumerate(sort_models_for_display(list(set(models))))}
    return sorted(
        rows,
        key=lambda row: (
            0 if row.get("tier") == "daily" else 1,
            not bool(row.get("enabled")),
            order.get(str(row.get("model") or ""), 999),
            int(row.get("memory_gb") or 0),
        ),
    )


class IphoneMarketWatchlistService:
    async def list_items(self) -> list[dict]:
        rows = await run_db(list_watchlist_items)
        return sort_watchlist_rows(rows)

    async def get_item(self, item_id: int) -> Optional[dict]:
        return await run_db(get_watchlist_item, int(item_id))

    async def get_by_config(self, model: str, memory_gb: int) -> Optional[dict]:
        return await run_db(get_watchlist_item_by_config, model, int(memory_gb))

    async def add(
        self,
        model: str,
        memory_gb: int,
        *,
        tier: str = "daily",
        source: str = "manual",
        last_snapshot_id: Optional[int] = None,
        fetched_at: Optional[datetime] = None,
    ) -> dict:
        existing = await self.get_by_config(model, memory_gb)
        if existing:
            return existing
        nxt = compute_next_refresh_at(tier, last_refreshed_at=fetched_at)
        return await run_db(
            add_watchlist_item,
            model,
            int(memory_gb),
            tier=tier,
            source=source,
            last_snapshot_id=last_snapshot_id,
            next_refresh_at=nxt,
        )

    async def delete(self, item_id: int) -> bool:
        return bool(await run_db(delete_watchlist_item, int(item_id)))

    async def set_tier(self, item_id: int, tier: str) -> Optional[dict]:
        item = await self.get_item(item_id)
        if not item:
            return None
        nxt = compute_next_refresh_at(
            tier,
            last_refreshed_at=item.get("last_refreshed_at") or item.get("fetched_at"),
        )
        return await run_db(update_watchlist_item, int(item_id), tier=tier, next_refresh_at=nxt)

    async def set_enabled(self, item_id: int, enabled: bool) -> Optional[dict]:
        return await run_db(update_watchlist_item, int(item_id), enabled=bool(enabled))

    async def list_import_candidates(self) -> list[dict]:
        snapshots = await run_db(list_success_snapshot_configs)
        existing = await run_db(list_watchlist_keys)
        rows = [
            row
            for row in snapshots
            if (str(row.get("model") or ""), int(row.get("memory_gb") or 0)) not in existing
        ]
        return sort_watchlist_rows(rows)

    async def import_snapshots(self, snapshot_ids: list[int], *, tier: str = "daily") -> int:
        candidates = await self.list_import_candidates()
        wanted = {int(item_id) for item_id in snapshot_ids}
        added = 0
        for row in candidates:
            snap_id = int(row.get("id") or 0)
            if snap_id not in wanted:
                continue
            await self.add(
                str(row["model"]),
                int(row["memory_gb"]),
                tier=tier,
                source="import",
                last_snapshot_id=snap_id,
                fetched_at=row.get("fetched_at"),
            )
            added += 1
        return added

    async def list_catalog_suggestions(self) -> list[dict]:
        configs = await run_db(list_used_catalog_configs)
        existing = await run_db(list_watchlist_keys)
        rows = [
            row
            for row in configs
            if (str(row.get("model") or ""), int(row.get("memory_gb") or 0)) not in existing
            and not is_vintage_market_model(str(row.get("model") or ""))
        ]
        return sort_watchlist_rows(rows)

    async def add_catalog_suggestion(
        self,
        model: str,
        memory_gb: int,
        *,
        tier: str = "daily",
    ) -> dict:
        return await self.add(model, int(memory_gb), tier=tier, source="catalog")

    async def refresh_item(
        self,
        item_id: int,
        *,
        source: str = "watchlist",
    ) -> tuple[Optional[dict], Optional[MarketPriceEstimate], str]:
        """Обновить одну позицию. Возвращает (item, estimate, outcome).

        outcome: live | cache | stale | missing
        """
        item = await self.get_item(item_id)
        if not item:
            return None, None, "missing"
        query = IphoneMarketQuery(model=str(item["model"]), memory_gb=int(item["memory_gb"]))
        now = _utcnow()
        await run_db(update_watchlist_item, int(item_id), last_attempt_at=now)
        estimate = await get_iphone_market_price_service().estimate(query, source=source)
        if estimate.live_fetched and not estimate.is_stale:
            nxt = compute_next_refresh_at(str(item.get("tier") or "daily"), now=now)
            updated = await run_db(
                update_watchlist_item,
                int(item_id),
                last_snapshot_id=estimate.snapshot_id,
                last_refreshed_at=now,
                next_refresh_at=nxt,
            )
            return updated, estimate, "live"
        if estimate.is_stale:
            nxt = now + timedelta(seconds=AVITO_MARKET_WL_BLOCK_PAUSE_SEC)
            updated = await run_db(
                update_watchlist_item,
                int(item_id),
                next_refresh_at=nxt,
            )
            return updated, estimate, "stale"
        nxt = compute_next_refresh_at(
            str(item.get("tier") or "daily"),
            now=now,
            last_refreshed_at=estimate.fetched_at,
        )
        updated = await run_db(
            update_watchlist_item,
            int(item_id),
            last_snapshot_id=estimate.snapshot_id,
            next_refresh_at=nxt,
        )
        return updated, estimate, "cache"


_service: Optional[IphoneMarketWatchlistService] = None


def get_iphone_market_watchlist_service() -> IphoneMarketWatchlistService:
    global _service
    if _service is None:
        _service = IphoneMarketWatchlistService()
    return _service
