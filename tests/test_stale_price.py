"""Тесты застоя по цене и истории цен."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.bot.utils.price_history_formatter import (
    format_price_change_line_short,
    format_price_history_expandable_html,
    real_price_changes,
)
from app.bot.utils.stale_price_formatter import (
    format_stale_detail_text,
    format_stale_list_line,
    format_stale_list_text,
    stale_button_label,
)
from app.utils.stale_price_utils import (
    STALE_SORT_SALE,
    days_in_sale,
    days_without_price_change,
)
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


def test_days_in_sale():
    created = datetime.now(timezone.utc) - timedelta(days=84)
    product = {"created_at": created.isoformat()}
    assert days_in_sale(product) == 85


def test_format_stale_list_line_without_repriced():
    product = {
        "name": "iPhone 14 Pro 128Gb Gold 2273",
        "price": "39500₽",
        "price_changed_at": (datetime.now(timezone.utc) - timedelta(days=109)).isoformat(),
        "created_at": (datetime.now(timezone.utc) - timedelta(days=109)).isoformat(),
        "price_repriced": False,
    }
    line = format_stale_list_line(1, product)
    assert line.startswith("1.")
    assert "39500" in line
    assert "д." in line
    assert "↺" not in line


def test_format_stale_list_line_with_repriced():
    now = datetime.now(timezone.utc)
    product = {
        "name": "iPhone 14 Pro Max 256Gb Black 2747",
        "price": "41900₽",
        "price_changed_at": (now - timedelta(days=5)).isoformat(),
        "created_at": (now - timedelta(days=84)).isoformat(),
        "price_repriced": True,
    }
    line = format_stale_list_line(80, product)
    assert "↺" in line
    assert "д.в" in line


def test_format_stale_list_text_header():
    products = [
        {
            "name": "iPhone 13",
            "price": "23900₽",
            "price_changed_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "price_repriced": False,
        }
    ]
    text = format_stale_list_text(products, badge_count=1, min_days=60)
    assert "Застой по цене" in text
    assert "без смены ≥60д." in text
    assert "↺ — цена менялась" in text
    assert "1." in text


def test_format_stale_list_text_sale_sort_hint():
    products = [
        {
            "name": "iPhone 13",
            "price": "23900₽",
            "price_changed_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "price_repriced": False,
        }
    ]
    text = format_stale_list_text(
        products, badge_count=1, min_days=60, sort_mode=STALE_SORT_SALE
    )
    assert "по давности в продаже" in text


def test_format_stale_detail_with_history():
    product = {
        "name": "iPhone 13 128Gb Pink 1141",
        "price": "39500₽",
        "price_changed_at": "2026-06-01T12:00:00+00:00",
        "created_at": "2026-06-01T12:00:00+00:00",
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
    assert "в продаже" in text


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


def test_real_price_changes_excludes_publication():
    history = [
        {"source": "publication", "changed_at": "2026-06-01T12:00:00+00:00", "id": 1},
        {"source": "manual", "changed_at": "2026-07-05T11:20:00+00:00", "id": 2},
    ]
    changes = real_price_changes(history)
    assert len(changes) == 1
    assert changes[0]["source"] == "manual"


def test_format_price_change_line_short():
    entry = {
        "old_price": "91900₽",
        "new_price": "89900₽",
        "changed_at": "2026-07-12T10:00:00+00:00",
        "source": "manual",
    }
    line = format_price_change_line_short(entry)
    assert "💱" in line
    assert "91900" in line
    assert "89900" in line
    assert line.endswith("↓")


def test_format_price_history_expandable_html():
    history = [
        {
            "old_price": None,
            "new_price": "91900₽",
            "changed_at": "2026-07-12T10:00:00+00:00",
            "source": "publication",
        },
        {
            "old_price": "91900₽",
            "new_price": "89900₽",
            "changed_at": "2026-07-13T10:00:00+00:00",
            "source": "manual",
        },
    ]
    html = format_price_history_expandable_html(history)
    assert "<blockquote expandable>" in html
    assert "89900" in html
    assert "публикация" not in html


def test_format_price_history_empty_for_publication_only():
    history = [
        {
            "old_price": None,
            "new_price": "91900₽",
            "changed_at": "2026-07-12T10:00:00+00:00",
            "source": "publication",
        },
    ]
    assert format_price_history_expandable_html(history) == ""
