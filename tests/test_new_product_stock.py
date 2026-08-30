"""Карточка новых: тумблер наличия, «Ещё», журнал продаж в сводке."""
from __future__ import annotations

from app.bot.keyboards.new_products_keyboard import (
    get_new_catalog_hide_keyboard,
    get_new_product_detail_keyboard,
    get_new_product_more_keyboard,
    get_new_product_stock_off_keyboard,
)
from app.db.monthly_sales_queries import combine_month_summary_rows
from app.utils.monthly_sales_formatter import format_monthly_sales_html
from app.utils.new_product_stock import (
    availability_label,
    format_catalog_hide_confirm_text,
    format_stock_off_confirm_text,
)


def _labels(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


def _callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_detail_keyboard_hides_sale_and_unavailable():
    kb = get_new_product_detail_keyboard(7, status="active", availability_status="available")
    labels = _labels(kb)
    assert "🟢 В наличии" in labels
    assert "⋯ Ещё" in labels
    assert "💰 Продажа" not in labels
    assert "🚫 Товар недоступен" not in labels
    assert "new_product_more_7" in _callbacks(kb)
    assert "new_product_toggle_avail_7" in _callbacks(kb)


def test_detail_keyboard_on_order_and_no_more_when_unavailable():
    kb = get_new_product_detail_keyboard(
        3, status="unavailable", availability_status="on_order"
    )
    labels = _labels(kb)
    assert "🔴 На заказ" in labels
    assert "⋯ Ещё" not in labels


def test_more_keyboard_has_sale_and_hide():
    kb = get_new_product_more_keyboard(7)
    labels = _labels(kb)
    cbs = _callbacks(kb)
    assert "💰 Продажа" in labels
    assert "🚫 Товар недоступен" in labels
    assert "new_product_sell_7" in cbs
    assert "new_product_unavail_7" in cbs
    assert "new_product_7" in cbs


def test_stock_off_keyboard_sale_and_transfer():
    kb = get_new_product_stock_off_keyboard(16)
    labels = _labels(kb)
    cbs = _callbacks(kb)
    assert "💰 Продажа" in labels
    assert "📦 Перемещение" in labels
    assert "new_product_off_sale_16" in cbs
    assert "new_product_off_xfer_16" in cbs
    assert "new_product_16" in cbs
    rows = kb.inline_keyboard
    action_row = next(r for r in rows if any("Продажа" in b.text for b in r))
    assert getattr(action_row[0], "style", None) == "success"
    assert getattr(action_row[1], "style", None) == "danger"


def test_catalog_hide_keyboard():
    kb = get_new_catalog_hide_keyboard(9)
    cbs = _callbacks(kb)
    assert "new_product_unavail_ok_9" in cbs
    assert "new_product_9" in cbs
    assert "🚫 Скрыть из каталога" in _labels(kb)


def test_stock_off_confirm_text():
    text = format_stock_off_confirm_text("iPhone 16 128Gb Pink")
    assert "iPhone 16 128Gb Pink" in text
    assert "в наличии" in text
    assert "на заказ" in text
    assert "Продажа" in text
    assert "Перемещение" in text


def test_catalog_hide_confirm_text_not_a_sale():
    text = format_catalog_hide_confirm_text("A <B>")
    assert "A &lt;B&gt;" in text
    assert "не продажа" in text.lower()
    assert "каталог" in text.lower()


def test_availability_label():
    assert availability_label("available") == "🟢 В наличии"
    assert availability_label("on_order") == "🔴 На заказ"
    assert availability_label(None) == "—"


def test_combine_month_summary_excludes_new_archive_includes_sales():
    archived = [
        {"name": "iPhone 13 Pro", "collection_name": "iPhone б/у"},
        {"name": "iPhone 16 128 Pink", "collection_name": "iPhone новые"},
        {"name": "AirPods Pro 2", "collection_name": "Airpods"},
    ]
    sales = [
        {"name": "iPhone 16 128Gb Pink", "collection_name": "iPhone новые"},
        {"name": "iPhone 16 128Gb Pink", "collection_name": "iPhone новые"},
    ]
    rows = combine_month_summary_rows(archived, sales)
    names = [r["name"] for r in rows]
    assert "iPhone 13 Pro" in names
    assert "iPhone 16 128 Pink" not in names
    assert names.count("iPhone 16 128Gb Pink") == 2
    html = format_monthly_sales_html(rows, "Август")
    assert "—— новые ——" in html
    assert "16" in html
    assert "AirPods" not in html
