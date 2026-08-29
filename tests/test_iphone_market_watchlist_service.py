import asyncio
from datetime import datetime, timedelta

import app.services.iphone_market_watchlist_service as wl_module
from app.scheduler.market_watchlist_worker import MarketWatchlistWorker
from app.services.iphone_market_price_service import (
    MarketPriceEstimate,
    MarketTemporarilyUnavailable,
)
from app.services.iphone_market_watchlist_service import IphoneMarketWatchlistService
from app.utils.iphone_market_query import parse_iphone_market_query


def _estimate(*, live=False, stale=False, snapshot_id=5):
    query = parse_iphone_market_query("13 mini 128")
    now = datetime(2026, 8, 29, 12, 0, 0)
    return MarketPriceEstimate(
        query=query,
        region="Россия",
        total_count=10,
        matched_count=8,
        used_count=8,
        outlier_count=0,
        summary=None,
        private_summary=None,
        business_summary=None,
        fetched_at=now,
        is_stale=stale,
        live_fetched=live,
        snapshot_id=snapshot_id,
    )


def test_import_candidates_exclude_existing(monkeypatch):
    snapshots = [
        {"id": 1, "model": "iPhone 13 mini", "memory_gb": 128, "fetched_at": datetime(2026, 8, 29, 10, 0)},
        {"id": 2, "model": "iPhone 14", "memory_gb": 256, "fetched_at": datetime(2026, 8, 29, 11, 0)},
    ]

    async def fake_run_db(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "list_success_snapshot_configs":
            return snapshots
        if name == "list_watchlist_keys":
            return {("iPhone 13 mini", 128)}
        return []

    monkeypatch.setattr(wl_module, "run_db", fake_run_db)
    rows = asyncio.run(IphoneMarketWatchlistService().list_import_candidates())
    assert [row["id"] for row in rows] == [2]


def test_catalog_suggestions_exclude_watchlist(monkeypatch):
    async def fake_run_db(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "list_used_catalog_configs":
            return [
                {"model": "iPhone XR", "memory_gb": 64, "product_count": 1},
                {"model": "iPhone 14", "memory_gb": 128, "product_count": 3},
                {"model": "iPhone 15", "memory_gb": 256, "product_count": 1},
            ]
        if name == "list_watchlist_keys":
            return {("iPhone 14", 128)}
        return []

    monkeypatch.setattr(wl_module, "run_db", fake_run_db)
    rows = asyncio.run(IphoneMarketWatchlistService().list_catalog_suggestions())
    assert {(row["model"], row["memory_gb"]) for row in rows} == {("iPhone 15", 256)}


def test_refresh_item_live_schedules_tier(monkeypatch):
    captured = {}

    async def fake_run_db(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name in {"get_watchlist_item", "update_watchlist_item"}:
            item = {
                "id": 3,
                "model": "iPhone 13 mini",
                "memory_gb": 128,
                "tier": "daily",
            }
            if name == "update_watchlist_item":
                captured.update(kwargs)
                item.update(kwargs)
            return item
        return None

    class FakeMarket:
        async def estimate(self, query, *, source="manual"):
            assert source == "watchlist"
            return _estimate(live=True)

    monkeypatch.setattr(wl_module, "run_db", fake_run_db)
    monkeypatch.setattr(wl_module, "get_iphone_market_price_service", lambda: FakeMarket())
    _item, estimate, outcome = asyncio.run(
        IphoneMarketWatchlistService().refresh_item(3, source="watchlist")
    )
    assert outcome == "live"
    assert estimate.live_fetched
    assert captured.get("last_snapshot_id") == 5
    assert captured.get("next_refresh_at") is not None


def test_refresh_item_stale_backs_off(monkeypatch):
    captured = {}

    async def fake_run_db(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name in {"get_watchlist_item", "update_watchlist_item"}:
            item = {"id": 3, "model": "iPhone 13 mini", "memory_gb": 128, "tier": "daily"}
            if name == "update_watchlist_item":
                captured.update(kwargs)
            return item
        return None

    class FakeMarket:
        async def estimate(self, query, *, source="manual"):
            return _estimate(live=True, stale=True)

    monkeypatch.setattr(wl_module, "run_db", fake_run_db)
    monkeypatch.setattr(wl_module, "get_iphone_market_price_service", lambda: FakeMarket())
    _item, _estimate_obj, outcome = asyncio.run(IphoneMarketWatchlistService().refresh_item(3))
    assert outcome == "stale"
    assert captured.get("next_refresh_at") is not None


def test_worker_disabled_does_not_refresh(monkeypatch):
    called = {"due": 0}

    class FakeSettings:
        def is_avito_market_watchlist_enabled(self):
            return False

        def get_avito_market_watchlist_pause_until(self):
            return None

        def set_avito_market_watchlist_pause_until(self, until):
            return None

    async def fake_run_db(fn, *args, **kwargs):
        called["due"] += 1
        return {"id": 1}

    monkeypatch.setattr("app.scheduler.market_watchlist_worker.get_settings_service", lambda: FakeSettings())
    monkeypatch.setattr("app.scheduler.market_watchlist_worker.run_db", fake_run_db)
    worker = MarketWatchlistWorker()
    outcome = asyncio.run(worker._handle_due())
    assert outcome == "disabled"
    assert called["due"] == 0


def test_worker_block_pauses_pass(monkeypatch):
    paused = {}

    class FakeSettings:
        def is_avito_market_watchlist_enabled(self):
            return True

        def get_avito_market_watchlist_pause_until(self):
            return None

        def set_avito_market_watchlist_pause_until(self, until):
            paused["until"] = until

    async def fake_run_db(fn, *args, **kwargs):
        return {"id": 8, "model": "iPhone 14", "memory_gb": 128}

    class FakeWl:
        async def refresh_item(self, item_id, *, source="watchlist"):
            raise MarketTemporarilyUnavailable("Avito временно ограничил автоматические запросы.")

    monkeypatch.setattr("app.scheduler.market_watchlist_worker.get_settings_service", lambda: FakeSettings())
    monkeypatch.setattr("app.scheduler.market_watchlist_worker.run_db", fake_run_db)
    monkeypatch.setattr(
        "app.scheduler.market_watchlist_worker.get_iphone_market_watchlist_service",
        lambda: FakeWl(),
    )
    worker = MarketWatchlistWorker()
    outcome = asyncio.run(worker._handle_due())
    assert outcome == "blocked"
    assert paused.get("until") is not None


def test_worker_stop_exits(monkeypatch):
    class FakeSettings:
        def is_avito_market_watchlist_enabled(self):
            return False

        def get_avito_market_watchlist_pause_until(self):
            return None

        def set_avito_market_watchlist_pause_until(self, until):
            return None

    monkeypatch.setattr("app.scheduler.market_watchlist_worker.get_settings_service", lambda: FakeSettings())
    monkeypatch.setattr("app.scheduler.market_watchlist_worker.AVITO_MARKET_WL_TICK_SEC", 1)
    worker = MarketWatchlistWorker()

    async def run():
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        worker.stop()
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(run())
