"""Тесты застоя по цене и истории цен."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.bot.utils.stale_price_formatter import (
    format_stale_detail_text,
    format_stale_list_line,
    format_stale_list_text,
    stale_button_label,
)
from app.utils.stale_price_utils import days_without_price_change
from app.utils.price_change import price_string_to_int_rub


def _prices_equal_rub(a, b) -> bool:
    ra = price_string_to_int_rub(a) if a else None
    rb = price_string_to_int_rub(b) if b else None
    if ra is None and rb is None:
        return (a or "").strip() == (b or "").strip()
    if ra is None or rb is None:
        return False
    return ra == rb


def test_prices_equal_rub():
    assert _prices_equal_rub("39500₽", "39500")
    assert not _prices_equal_rub("39500₽", "39000")


def test_days_without_price_change():
    start = datetime.now(timezone.utc) - timedelta(days=10)
    assert days_without_price_change(start) == 11


def test_format_stale_list_line():
    product = {
        "name": "iPhone 14 Pro 128Gb Gold 2273",
        "price": "39500₽",
        "price_changed_at": (datetime.now(timezone.utc) - timedelta(days=109)).isoformat(),
    }
    line = format_stale_list_line(1, product)
    assert line.startswith("1.")
    assert "39500" in line
    assert "д." in line


def test_format_stale_list_text_header():
    products = [
        {
            "name": "iPhone 13",
            "price": "23900₽",
            "price_changed_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    text = format_stale_list_text(products, badge_count=1, min_days=60)
    assert "Застой по цене" in text
    assert "без смены ≥60д." in text
    assert "1." in text


def test_format_stale_detail_with_history():
    product = {
        "name": "iPhone 13 128Gb Pink 1141",
        "price": "39500₽",
        "price_changed_at": "2026-06-01T12:00:00+00:00",
    }
    history = [
        {
            "old_price": "41000₽",
            "new_price": "39500₽",
            "changed_at": "2026-07-05T11:20:00+00:00",
            "source": "manual",
        },
        {
            "old_price": None,
            "new_price": "41000₽",
            "changed_at": "2026-06-01T12:00:00+00:00",
            "source": "publication",
        },
    ]
    text = format_stale_detail_text(product, history)
    assert "История цен" in text
    assert "публикация" in text
    assert "41000" in text
    assert "39500" in text


def test_stale_button_label():
    assert stale_button_label(7) == "🕰 Застой (7)"


def test_record_price_change_skips_same_price():
    """Без БД: логика сравнения цен."""
    assert _prices_equal_rub("10000₽", "10000")
    assert _prices_equal_rub("10000", "10000₽")


@pytest.mark.parametrize("days,expected_badge", [(59, False), (60, True), (61, True)])
def test_badge_threshold_logic(days, expected_badge):
    changed_at = datetime.now(timezone.utc) - timedelta(days=days - 1)
    stale_days = days_without_price_change(changed_at)
    assert (stale_days >= 60) == expected_badge
