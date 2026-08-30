from datetime import datetime

from app.bot.handlers.iphone_market_price import (
    _cancel_keyboard,
    _history_keyboard,
    _history_text,
    _result_keyboard,
    format_market_estimate,
    sort_market_report_rows,
)
from app.db.avito_market_watchlist_queries import ShopPriceRange
from app.integrations.avito.market_search import MarketListing
from app.services.iphone_market_price_service import MarketPriceEstimate
from app.utils.iphone_market_query import parse_iphone_market_query
from app.utils.price_stats import PriceSummary


def test_market_result_is_short_and_clear():
    query = parse_iphone_market_query("13 мини 128")
    estimate = MarketPriceEstimate(
        query=query,
        region="Россия",
        total_count=57,
        matched_count=36,
        used_count=34,
        outlier_count=2,
        summary=PriceSummary(34, 27_000, 25_000, 29_000),
        private_summary=PriceSummary(20, 26_500, 24_500, 28_000),
        business_summary=PriceSummary(14, 28_000, 26_000, 30_000),
        fetched_at=datetime(2026, 8, 28, 18, 30),
        listings=(
            MarketListing(
                "1",
                "iPhone 13 mini 128",
                25_000,
                city="Киров",
                seller_type="private",
                url="/kirov/telefony/iphone_1",
            ),
            MarketListing(
                "2",
                "iPhone 13 mini 128",
                29_000,
                city="Тула",
                seller_type="business",
                url="https://www.avito.ru/tula/telefony/iphone_2",
            ),
        ),
    )
    text = format_market_estimate(estimate)
    assert "iPhone 13 mini 128 ГБ, б/у" in text
    assert "25 000 ₽–29 000 ₽" in text
    assert "Медиана: <b>27 000 ₽</b>" in text
    assert "Учтено: 34 из 57" in text
    assert "Отсеяно: 23" in text
    assert "Частные продавцы" in text
    assert "Магазины" in text
    assert "(МСК)" in text
    assert "UTC" not in text
    assert "<blockquote expandable>" in text
    assert 'href="https://www.avito.ru/kirov/telefony/iphone_1"' in text
    assert "Киров · частник" in text
    assert "Тула · магазин" in text
    assert "Учтённые объявления" in text
    assert "Ориентир" not in text
    assert "В магазине" not in text


def test_market_result_shows_shop_range_after_typical():
    query = parse_iphone_market_query("14 pro max 128")
    estimate = MarketPriceEstimate(
        query=query,
        region="Россия",
        total_count=30,
        matched_count=20,
        used_count=20,
        outlier_count=1,
        summary=PriceSummary(20, 33_450, 32_000, 36_992),
        private_summary=None,
        business_summary=None,
        fetched_at=datetime(2026, 8, 30, 9, 10),
    )
    text = format_market_estimate(
        estimate,
        shop_range=ShopPriceRange(count=5, min_rub=38_500, max_rub=41_500),
    )
    typical = text.index("Типичный диапазон")
    shop = text.index("В магазине (5 шт): <b>38 500 ₽–41 500 ₽</b>")
    median = text.index("Медиана")
    assert typical < shop < median
    assert "Магазины:" not in text


def test_market_result_shop_single_price_and_hidden_when_empty():
    query = parse_iphone_market_query("14 pro max 128")
    estimate = MarketPriceEstimate(
        query=query,
        region="Россия",
        total_count=10,
        matched_count=8,
        used_count=8,
        outlier_count=0,
        summary=PriceSummary(8, 33_000, 32_000, 35_000),
        private_summary=None,
        business_summary=None,
        fetched_at=datetime(2026, 8, 30, 9, 10),
    )
    one = format_market_estimate(
        estimate,
        shop_range=ShopPriceRange(count=1, min_rub=38_500, max_rub=38_500),
    )
    assert "В магазине (1 шт): <b>38 500 ₽</b>" in one
    assert "38 500 ₽–38 500 ₽" not in one
    assert "В магазине" not in format_market_estimate(estimate)
    assert "В магазине" not in format_market_estimate(estimate, shop_range=None)


def test_soft_result_shows_orientir_warning():
    query = parse_iphone_market_query("15 pro max 256")
    estimate = MarketPriceEstimate(
        query=query,
        region="Россия",
        total_count=30,
        matched_count=7,
        used_count=7,
        outlier_count=0,
        summary=PriceSummary(7, 95_000, 90_000, 100_000),
        private_summary=None,
        business_summary=None,
        fetched_at=datetime(2026, 8, 28, 22, 54),
        is_soft=True,
    )
    text = format_market_estimate(estimate)
    assert "Ориентир по цене" in text
    assert "95 000 ₽" in text
    assert "Мало объявлений" in text
    assert "Отсеяно: 23" in text


def test_stale_result_has_explicit_warning():
    query = parse_iphone_market_query("13 mini 128")
    estimate = MarketPriceEstimate(
        query=query,
        region="Россия",
        total_count=20,
        matched_count=15,
        used_count=14,
        outlier_count=1,
        summary=PriceSummary(14, 27_000, 25_000, 29_000),
        private_summary=None,
        business_summary=None,
        fetched_at=datetime(2026, 8, 28, 18, 30),
        is_stale=True,
        stale_reason="Avito временно ограничил автоматические запросы. Оценка недоступна какое-то время — попробуйте позже.",
    )
    text = format_market_estimate(estimate)
    assert "Показан сохранённый результат" in text
    assert "временно ограничил" in text


def test_market_reports_sorted_old_to_new_then_memory():
    rows = [
        {"id": 3, "model": "iPhone 15 Pro", "memory_gb": 256, "median_rub": 90_000},
        {"id": 1, "model": "iPhone 11", "memory_gb": 256, "median_rub": 18_000},
        {"id": 2, "model": "iPhone 11", "memory_gb": 64, "median_rub": 12_000},
        {"id": 4, "model": "iPhone 13 mini", "memory_gb": 128, "median_rub": 27_000},
    ]
    ordered = sort_market_report_rows(rows)
    assert [row["id"] for row in ordered] == [2, 1, 4, 3]


def test_history_keyboard_paginates_and_stays_inside_block():
    rows = [
        {
            "id": i,
            "model": "iPhone 11" if i < 10 else "iPhone 16",
            "memory_gb": 128,
            "median_rub": 10_000 + i,
        }
        for i in range(1, 14)
    ]
    markup = _history_keyboard(rows, page=0)
    pairs = [(btn.text, btn.callback_data) for row in markup.inline_keyboard for btn in row]
    texts = [text for text, _ in pairs]
    callbacks = [cb for _, cb in pairs]
    assert "▶️" in texts
    assert "◀️" not in texts
    assert ("⬅️ Назад", "avito_market_start") in pairs
    assert "avito_market_cancel" not in callbacks
    assert any(cb == "avito_market_hist:1" for cb in callbacks)
    assert any(cb.startswith("avito_market_open:") and cb.endswith(":0") for cb in callbacks)

    page2 = _history_keyboard(rows, page=1)
    page2_pairs = [(btn.text, btn.callback_data) for row in page2.inline_keyboard for btn in row]
    page2_texts = [text for text, _ in page2_pairs]
    assert "◀️" in page2_texts
    assert ("⬅️ Назад", "avito_market_start") in page2_pairs
    assert any("16 128ГБ" in text for text in page2_texts)
    assert any(cb.startswith("avito_market_open:") and cb.endswith(":1") for _, cb in page2_pairs)


def test_result_keyboard_one_step_back():
    from_search = [
        (btn.text, btn.callback_data)
        for row in _result_keyboard().inline_keyboard
        for btn in row
    ]
    assert ("⬅️ Назад", "avito_market_start") in from_search
    assert ("🗂 Последние отчёты", "avito_market_history") in from_search
    assert all(cb != "avito_market_cancel" for _, cb in from_search)

    offered = [
        (btn.text, btn.callback_data)
        for row in _result_keyboard(offer_watchlist=True).inline_keyboard
        for btn in row
    ]
    assert ("➕ В автообновление", "avito_market_wl:fromr") in offered

    from_history = [
        (btn.text, btn.callback_data)
        for row in _result_keyboard(history_page=2).inline_keyboard
        for btn in row
    ]
    assert from_history == [("⬅️ Назад", "avito_market_hist:2")]


def test_intro_keyboard_has_watchlist_and_products_exit():
    pairs = [
        (btn.text, btn.callback_data)
        for row in _cancel_keyboard().inline_keyboard
        for btn in row
    ]
    assert ("📋 Список автообновления", "avito_market_wl") in pairs
    assert ("⬅️ В товары", "avito_market_cancel") in pairs


def test_history_header_lists_all_reports_with_time():
    rows = sort_market_report_rows(
        [
            {
                "id": 2,
                "model": "iPhone 11",
                "memory_gb": 256,
                "median_rub": None,
                "fetched_at": datetime(2026, 8, 29, 0, 24),
            },
            {
                "id": 1,
                "model": "iPhone 11",
                "memory_gb": 64,
                "median_rub": 9_990,
                "fetched_at": datetime(2026, 8, 29, 8, 40),
            },
            {
                "id": 3,
                "model": "iPhone 12 Pro Max",
                "memory_gb": 256,
                "median_rub": 23_000,
                "fetched_at": datetime(2026, 8, 29, 10, 0),
            },
        ]
    )
    text = _history_text(rows)
    assert "<pre>" in text
    assert "blockquote expandable" not in text
    assert "11" in text and "64ГБ" in text and "9 990 ₽" in text
    assert "29.08.26 11:40" in text
    assert "11" in text and "256ГБ" in text and "—" in text
    assert "29.08.26 03:24" in text
    assert "12 Pro Max" in text and "23 000 ₽" in text
    assert "29.08.26 13:00" in text
    assert "Свежие" not in text
    assert "Показано" not in text


def test_history_header_highlights_latest_and_collapses_catalog():
    rows = sort_market_report_rows(
        [
            {
                "id": i,
                "model": f"iPhone {10 + i}",
                "memory_gb": 128,
                "median_rub": 10_000 * i,
                "fetched_at": datetime(2026, 8, 29, i, 0),
            }
            for i in range(1, 5)
        ]
    )
    text = _history_text(rows)
    assert "🕒 Свежие:" in text
    assert "<blockquote expandable><pre>" in text
    assert "14 128ГБ: 40 000 ₽ · 29.08.26 07:00" in text
    assert "13 128ГБ: 30 000 ₽ · 29.08.26 06:00" in text
    assert "12 128ГБ: 20 000 ₽ · 29.08.26 05:00" in text


def _product_menu_labels(*, avito_market_enabled: bool) -> list[str]:
    from app.bot.keyboards.product_keyboard import get_products_menu_keyboard

    markup = get_products_menu_keyboard(avito_market_enabled=avito_market_enabled)
    return [btn.text for row in markup.inline_keyboard for btn in row]


def test_products_menu_hides_avito_market_when_disabled():
    labels = _product_menu_labels(avito_market_enabled=False)
    assert "📊 Оценка рынка Avito" not in labels
    assert "📦 Список б/у товаров" in labels
    assert "📁 Архив товаров" in labels


def test_products_menu_shows_avito_market_when_enabled():
    labels = _product_menu_labels(avito_market_enabled=True)
    assert "📊 Оценка рынка Avito" in labels
