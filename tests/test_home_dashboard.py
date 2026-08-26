"""Главный экран: заметки и сводка архива за месяц."""
from __future__ import annotations

from datetime import datetime, timezone

from app.bot.keyboards.main_keyboard import get_main_keyboard
from app.bot.utils.home_text import (
    format_home_html,
    format_notes_html,
    truncate_note_button,
)
from app.utils.monthly_sales_formatter import (
    compact_preview_name,
    format_monthly_sales_html,
    split_used_and_new,
)
from app.utils.time_msk import msk_month_bounds_naive_utc


def test_compact_preview_name():
    assert compact_preview_name("13 Pro Max") == "13 PM"
    assert compact_preview_name("iPhone 14 Pro") == "14 Pro"
    assert compact_preview_name("12 Pro") == "12 Pro"


def test_split_used_and_new():
    used, new = split_used_and_new([
        {"name": "iPhone 13 Pro Max", "collection_name": "iPhone б/у"},
        {"name": "AirPods Pro 2", "collection_name": "Airpods"},
        {"name": "Watch", "collection_name": None},
    ])
    assert len(used) == 2
    assert len(new) == 1
    assert new[0]["collection_name"] == "Airpods"


def test_monthly_sales_html_preview_and_expand():
    products = [
        {"name": "iPhone 13 Pro Max 256Gb Black", "collection_name": "iPhone б/у"},
        {"name": "iPhone 13 Pro Max 128Gb", "collection_name": "iPhone б/у"},
        {"name": "iPhone 12 Pro 128Gb", "collection_name": None},
        {"name": "iPhone 14 Pro 128Gb", "collection_name": "iPhone б/у"},
        {"name": "AirPods Pro 2", "collection_name": "Airpods"},
        {"name": "AirPods Pro 2 white", "collection_name": "Airpods"},
        {"name": "Apple Watch SE 3 44mm", "collection_name": "Apple Watch"},
    ]
    html = format_monthly_sales_html(products, "Август")
    assert "<blockquote expandable>" in html
    assert html.startswith("<blockquote expandable>Август · 7 ·")
    assert "13 PM 2" in html.split("\n")[0]
    assert "12 Pro — 1" in html
    assert "13 Pro Max — 2" in html
    assert "—— новые ——" in html
    assert "AirPods Pro 2 — 2" in html
    assert "Watch SE 3" in html
    # полный список в каталожном порядке: 12 раньше 13
    pos_12 = html.index("12 Pro — 1")
    pos_13 = html.index("13 Pro Max — 2")
    pos_14 = html.index("14 Pro — 1")
    assert pos_12 < pos_13 < pos_14


def test_monthly_sales_html_empty_month():
    html = format_monthly_sales_html([], "Август")
    assert "Август · 0" in html
    assert "—— новые ——" not in html


def test_notes_and_home_html():
    notes = [
        {"body": "Плёнки", "category": "stationery"},
        {"body": "13 mini", "category": "assortment"},
        {"body": "Сервис", "category": "service"},
        {"body": "Без метки", "category": None},
    ]
    block = format_notes_html(notes)
    assert "📎 Плёнки" in block
    assert "📦 13 mini" in block
    assert "🔧 Сервис" in block
    assert "📌 Без метки" in block
    home = format_home_html(notes, "<blockquote expandable>Август · 1</blockquote>")
    assert home.startswith("📎 Плёнки")
    assert "<b>Сводка месяца</b>" in home
    assert home.index("📌 Без метки") < home.index("Сводка месяца")
    assert "<blockquote expandable>" in home


def test_truncate_note_button():
    assert truncate_note_button("коротко") == "коротко"
    long = "нужно заказать очень длинную партию плёнок на все модели"
    assert truncate_note_button(long, 28).endswith("…")
    assert len(truncate_note_button(long, 28)) <= 28


def test_main_keyboard_notes_buttons():
    kb0 = get_main_keyboard()
    first = [b.text for b in kb0.inline_keyboard[0]]
    assert first == ["📌"]
    kb1 = get_main_keyboard(notes_count=2)
    first = [b.text for b in kb1.inline_keyboard[0]]
    assert first == ["📌", "✅"]
    assert kb1.inline_keyboard[1][0].text.startswith("🆕")


def test_msk_month_bounds_august():
    when = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)
    start, end, name = msk_month_bounds_naive_utc(when)
    assert name == "Август"
    assert start < end
    assert start.month in (7, 8)
    assert end > start
