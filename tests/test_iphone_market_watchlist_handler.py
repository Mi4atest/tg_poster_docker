from datetime import datetime, timedelta

from app.bot.keyboards.iphone_market_watchlist_keyboard import (
    watchlist_import_keyboard,
    watchlist_item_keyboard,
    watchlist_main_keyboard,
    watchlist_suggest_keyboard,
)
from app.db.avito_market_watchlist_queries import catalog_memory_to_gb, is_vintage_market_model
from app.services.iphone_market_watchlist_service import (
    compute_next_refresh_at,
    sort_watchlist_rows,
    tier_interval_sec,
)


def test_vintage_and_memory_helpers():
    assert is_vintage_market_model("iPhone XR")
    assert is_vintage_market_model("iPhone X")
    assert not is_vintage_market_model("iPhone 13 mini")
    assert catalog_memory_to_gb("128") == 128
    assert catalog_memory_to_gb("1Tb") == 1024
    assert catalog_memory_to_gb(None) is None


def test_tier_intervals_and_next_refresh():
    assert tier_interval_sec("daily") >= 86400
    assert tier_interval_sec("slow") >= 172800
    now = datetime(2026, 8, 29, 12, 0, 0)
    nxt = compute_next_refresh_at("daily", now=now, last_refreshed_at=now)
    assert nxt == now + timedelta(seconds=tier_interval_sec("daily"))


def test_sort_watchlist_rows_daily_first_then_old_models():
    rows = [
        {"id": 2, "model": "iPhone 15", "memory_gb": 128, "tier": "slow", "enabled": True},
        {"id": 1, "model": "iPhone 11", "memory_gb": 64, "tier": "daily", "enabled": True},
        {"id": 3, "model": "iPhone 13 mini", "memory_gb": 128, "tier": "daily", "enabled": True},
    ]
    ordered = sort_watchlist_rows(rows)
    assert [row["id"] for row in ordered] == [1, 3, 2]


def test_watchlist_keyboards_use_numeric_callbacks():
    rows = [
        {
            "id": 4,
            "model": "iPhone 13 Pro Max",
            "memory_gb": 256,
            "tier": "daily",
            "enabled": True,
            "median_rub": 50_000,
            "last_snapshot_id": 9,
        }
    ]
    main = watchlist_main_keyboard(rows)
    callbacks = [btn.callback_data for row in main.inline_keyboard for btn in row]
    assert "avito_market_wl:i:4" in callbacks
    assert "avito_market_wl:imp" in callbacks
    assert "avito_market_start" in callbacks
    assert all(len(cb) <= 64 for cb in callbacks)

    item = watchlist_item_keyboard(rows[0])
    item_cb = [btn.callback_data for row in item.inline_keyboard for btn in row]
    assert "avito_market_open:9:0" in item_cb
    assert "avito_market_wl:run:4" in item_cb
    assert all(len(cb) <= 64 for cb in item_cb)


def test_import_keyboard_toggles_selection():
    rows = [{"id": 11, "model": "iPhone 14", "memory_gb": 128, "median_rub": 30_000}]
    markup = watchlist_import_keyboard(rows, selected={11}, tier="slow")
    texts = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any(text.startswith("✅") for text in texts)
    assert any("🕐 72 ч" in text for text in texts)


def test_suggest_keyboard_uses_index_not_model_name():
    rows = [
        {"model": "iPhone 13 Pro Max", "memory_gb": 1024, "product_count": 2},
        {"model": "iPhone 16", "memory_gb": 128, "product_count": 1},
    ]
    markup = watchlist_suggest_keyboard(rows)
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "avito_market_wl:sug:a:0" in callbacks
    assert all("iPhone" not in cb for cb in callbacks)
    assert all(len(cb) <= 64 for cb in callbacks)
