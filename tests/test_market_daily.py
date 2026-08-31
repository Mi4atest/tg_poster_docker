from datetime import date

from app.bot.utils.market_daily_formatter import (
    format_market_daily_html,
    format_market_daily_line,
)
from app.utils.market_daily import (
    QUALITY_GAP,
    QUALITY_OK,
    QUALITY_SOFT,
    QUALITY_THIN,
    classify_sample_quality,
    quote_is_carried,
    should_replace_daily,
)


def test_classify_sample_quality_thresholds():
    assert classify_sample_quality(10) == QUALITY_OK
    assert classify_sample_quality(9) == QUALITY_SOFT
    assert classify_sample_quality(3) == QUALITY_SOFT
    assert classify_sample_quality(2) == QUALITY_THIN
    assert classify_sample_quality(0) == QUALITY_THIN


def test_weaker_daily_point_does_not_replace_stronger():
    assert should_replace_daily(None, QUALITY_GAP) is True
    assert should_replace_daily(QUALITY_OK, QUALITY_OK) is True
    assert should_replace_daily(QUALITY_SOFT, QUALITY_OK) is True
    assert should_replace_daily(QUALITY_OK, QUALITY_SOFT) is False
    assert should_replace_daily(QUALITY_OK, QUALITY_THIN) is False
    assert should_replace_daily(QUALITY_OK, QUALITY_GAP) is False
    assert should_replace_daily(QUALITY_THIN, QUALITY_GAP) is False
    assert should_replace_daily(QUALITY_GAP, QUALITY_THIN) is True


def test_quote_is_carried_when_today_is_thin_but_numbers_remain():
    assert quote_is_carried(quote_quality=QUALITY_OK, used_count=2, has_summary=True) is True
    assert quote_is_carried(quote_quality=QUALITY_OK, used_count=14, has_summary=True) is False
    assert quote_is_carried(quote_quality=QUALITY_THIN, used_count=2, has_summary=True) is False
    assert quote_is_carried(quote_quality=QUALITY_OK, used_count=2, has_summary=False) is False


def test_daily_line_and_html_newest_first_with_delta():
    points = [
        {
            "observed_on": date(2026, 8, 28),
            "quality": QUALITY_OK,
            "median_rub": 8_200,
            "q25_rub": 7_500,
            "q75_rub": 9_000,
            "used_count": 34,
        },
        {
            "observed_on": date(2026, 8, 29),
            "quality": QUALITY_THIN,
            "used_count": 2,
        },
        {
            "observed_on": date(2026, 8, 30),
            "quality": QUALITY_OK,
            "median_rub": 7_900,
            "q25_rub": 7_000,
            "q75_rub": 8_800,
            "used_count": 28,
        },
        {
            "observed_on": date(2026, 8, 31),
            "quality": QUALITY_GAP,
            "used_count": 0,
        },
    ]
    html = format_market_daily_html(points)
    assert html.index("31.08") < html.index("30.08") < html.index("29.08")
    assert "7 900 ₽ · 7 000 ₽–8 800 ₽  (28) −300" in html
    assert "нет выборки (2)" in html
    assert "нет данных" in html
    assert format_market_daily_html([]) == ""
    assert "≈" in format_market_daily_line(
        {
            "observed_on": date(2026, 8, 30),
            "quality": QUALITY_SOFT,
            "median_rub": 8_000,
            "q25_rub": 7_000,
            "q75_rub": 9_000,
            "used_count": 5,
        }
    )
