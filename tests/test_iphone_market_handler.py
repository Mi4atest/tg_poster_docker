from datetime import datetime

from app.bot.handlers.iphone_market_price import format_market_estimate
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
