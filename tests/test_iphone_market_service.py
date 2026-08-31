import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import app.services.iphone_market_price_service as service_module
from app.integrations.avito.market_search import AvitoMarketBlockedError, MarketListing
from app.services.iphone_market_price_service import (
    IphoneMarketPriceService,
    MarketTemporarilyUnavailable,
)
from app.utils.iphone_market_query import parse_iphone_market_query


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def _snapshot(query, *, fresh):
    now = _utcnow()
    return {
        "cache_key": f"киров:{query.cache_key}",
        "model": query.model,
        "memory_gb": query.memory_gb,
        "region": "Киров",
        "status": "success",
        "total_count": 20,
        "matched_count": 15,
        "used_count": 14,
        "outlier_count": 1,
        "median_rub": 27_000,
        "q25_rub": 25_000,
        "q75_rub": 29_000,
        "private_summary": None,
        "business_summary": None,
        "listing_audit": [],
        "fetched_at": now - timedelta(hours=1),
        "expires_at": now + timedelta(hours=1) if fresh else now - timedelta(minutes=1),
        "last_error_at": None,
        "last_error": None,
        "retry_after": None,
    }


def test_from_snapshot_marks_carried_quote_and_keeps_median():
    query = parse_iphone_market_query("13 mini 128")
    snap = {
        **_snapshot(query, fresh=True),
        "used_count": 2,
        "total_count": 40,
        "quote_quality": "ok",
        "quote_as_of": _utcnow() - timedelta(days=3),
    }
    result = IphoneMarketPriceService._from_snapshot(query, snap)
    assert result.quote_carried is True
    assert result.summary is not None
    assert result.summary.median_rub == 27_000
    assert result.is_soft is False


def test_fresh_cache_does_not_call_fetcher(monkeypatch):
    query = parse_iphone_market_query("13 mini 128")
    calls = 0

    async def fetcher(_query):
        nonlocal calls
        calls += 1
        return []

    async def fake_run_db(fn, *args, **kwargs):
        assert fn is service_module.get_market_snapshot
        return _snapshot(query, fresh=True)

    monkeypatch.setattr(service_module, "run_db", fake_run_db)
    result = asyncio.run(IphoneMarketPriceService(fetcher=fetcher).estimate(query))
    assert result.summary is not None
    assert result.summary.median_rub == 27_000
    assert calls == 0


def test_blocked_request_returns_stale_cache_and_enters_cooldown(monkeypatch):
    query = parse_iphone_market_query("13 mini 128")
    calls = 0
    stale = _snapshot(query, fresh=False)

    async def fetcher(_query):
        nonlocal calls
        calls += 1
        raise AvitoMarketBlockedError("captcha")

    async def fake_run_db(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "get_market_snapshot":
            return stale
        if name in {"get_active_market_block_until", "get_last_live_request_at"}:
            return None
        if name == "count_live_requests_since":
            return 0
        return None

    monkeypatch.setattr(service_module, "run_db", fake_run_db)

    async def run():
        service = IphoneMarketPriceService(fetcher=fetcher)
        first = await service.estimate(query)
        second = await service.estimate(query)
        return first, second

    first, second = asyncio.run(run())
    assert first.is_stale
    assert second.is_stale
    assert calls == 1


def test_same_concurrent_query_is_fetched_once(monkeypatch):
    query = parse_iphone_market_query("13 mini 128")
    stored = None
    calls = 0

    async def fetcher(_query):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return [
            MarketListing(str(i), "iPhone 13 mini 128GB", 25_000 + i * 100)
            for i in range(10)
        ]

    async def fake_run_db(fn, *args, **kwargs):
        nonlocal stored
        name = getattr(fn, "__name__", "")
        if name == "get_market_snapshot":
            return stored
        if name in {"get_active_market_block_until", "get_last_live_request_at"}:
            return None
        if name == "count_live_requests_since":
            return 0
        if name == "save_market_snapshot":
            analysis = args[2]
            now = _utcnow()
            stored = {
                **_snapshot(query, fresh=True),
                "id": 7,
                "total_count": analysis.total_count,
                "matched_count": analysis.matched_count,
                "used_count": analysis.used_count,
                "outlier_count": analysis.outlier_count,
                "median_rub": analysis.summary.median_rub,
                "q25_rub": analysis.summary.q25_rub,
                "q75_rub": analysis.summary.q75_rub,
                "fetched_at": now,
                "expires_at": now + timedelta(hours=12),
            }
            return stored
        return None

    monkeypatch.setattr(service_module, "run_db", fake_run_db)

    async def run():
        service = IphoneMarketPriceService(fetcher=fetcher)
        return await asyncio.gather(service.estimate(query), service.estimate(query))

    results = asyncio.run(run())
    assert calls == 1
    assert all(result.summary is not None for result in results)


def test_block_without_cache_is_reported(monkeypatch):
    query = parse_iphone_market_query("13 mini 128")

    async def fetcher(_query):
        raise AvitoMarketBlockedError("captcha")

    async def fake_run_db(fn, *args, **kwargs):
        return None

    monkeypatch.setattr(service_module, "run_db", fake_run_db)
    with pytest.raises(MarketTemporarilyUnavailable):
        asyncio.run(IphoneMarketPriceService(fetcher=fetcher).estimate(query))


def test_persisted_retry_after_prevents_request_after_restart(monkeypatch):
    query = parse_iphone_market_query("13 mini 128")
    calls = 0
    failed = {
        **_snapshot(query, fresh=False),
        "status": "error",
        "fetched_at": None,
        "expires_at": None,
        "last_error": "Avito запросил проверку или ограничил запросы",
        "retry_after": _utcnow() + timedelta(hours=1),
    }

    async def fetcher(_query):
        nonlocal calls
        calls += 1
        return []

    async def fake_run_db(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "get_market_snapshot":
            return failed
        if name in {"get_active_market_block_until", "get_last_live_request_at"}:
            return None
        if name == "count_live_requests_since":
            return 0
        return failed

    monkeypatch.setattr(service_module, "run_db", fake_run_db)
    with pytest.raises(MarketTemporarilyUnavailable):
        asyncio.run(IphoneMarketPriceService(fetcher=fetcher).estimate(query))
    assert calls == 0


def test_persisted_daily_limit_blocks_live_fetch(monkeypatch):
    query = parse_iphone_market_query("13 mini 128")
    stale = _snapshot(query, fresh=False)
    calls = 0

    async def fetcher(_query):
        nonlocal calls
        calls += 1
        return []

    async def fake_run_db(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "get_market_snapshot":
            return stale
        if name == "count_live_requests_since":
            return 40
        if name in {"get_active_market_block_until", "get_last_live_request_at"}:
            return None
        return None

    monkeypatch.setattr(service_module, "run_db", fake_run_db)
    result = asyncio.run(IphoneMarketPriceService(fetcher=fetcher).estimate(query))
    assert result.is_stale
    assert calls == 0


def test_global_block_on_success_snapshot_after_restart(monkeypatch):
    query = parse_iphone_market_query("13 mini 128")
    stale = {
        **_snapshot(query, fresh=False),
        "status": "success",
        "retry_after": None,
        "last_error": None,
    }
    calls = 0

    async def fetcher(_query):
        nonlocal calls
        calls += 1
        return []

    async def fake_run_db(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "get_market_snapshot":
            return stale
        if name == "get_active_market_block_until":
            return _utcnow() + timedelta(minutes=30)
        if name == "count_live_requests_since":
            return 0
        if name == "get_last_live_request_at":
            return None
        return None

    monkeypatch.setattr(service_module, "run_db", fake_run_db)
    result = asyncio.run(IphoneMarketPriceService(fetcher=fetcher).estimate(query))
    assert result.is_stale
    assert calls == 0
