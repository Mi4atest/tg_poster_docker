"""Режим продажи / перемещения при снятии б/у с витрины."""
from __future__ import annotations

from app.bot.handlers.product_management import (
    _archive_product_title,
    format_product_card_html,
)
from app.bot.keyboards.product_keyboard import get_product_status_confirmation_keyboard
from app.utils.archive_kind import (
    format_unavailable_confirm_text,
    is_transfer_archive,
    normalize_archive_kind,
    ARCHIVE_KIND_SALE,
    ARCHIVE_KIND_TRANSFER,
)


def _labels(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


def _callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_normalize_archive_kind():
    assert normalize_archive_kind(None) == ARCHIVE_KIND_SALE
    assert normalize_archive_kind("") == ARCHIVE_KIND_SALE
    assert normalize_archive_kind("sale") == ARCHIVE_KIND_SALE
    assert normalize_archive_kind("transfer") == ARCHIVE_KIND_TRANSFER
    assert not is_transfer_archive({"archive_kind": None})
    assert is_transfer_archive({"archive_kind": "transfer"})


def test_unavailable_confirm_text():
    text = format_unavailable_confirm_text("iPhone 13 Pro 128Gb")
    assert "🚨" in text
    assert "ПОСМОТРИТЕ ВНИЗ" in text
    assert "Зелёная" in text
    assert "Красная" in text
    assert "iPhone 13 Pro 128Gb" in text
    assert "переключите" not in text


def test_unavailable_confirm_escapes_html_in_name():
    text = format_unavailable_confirm_text("A <B> & C")
    assert "A &lt;B&gt; &amp; C" in text
    assert "A <B>" not in text


def test_unavailable_keyboard_sale_and_transfer_row():
    kb = get_product_status_confirmation_keyboard(42, "unavailable")
    labels = _labels(kb)
    assert "💰 Продажа" in labels
    assert "📦 Перемещение" in labels
    assert "🔴 Отчет Иван/Саша" in labels
    assert "🟢 Пометить ТГ/IG/Max" in labels
    assert all("В архив как" not in t for t in labels)
    assert all("product_toggle_archive_kind" not in cb for cb in _callbacks(kb))
    assert "product_confirm_unavailable_42_0_1_0" in _callbacks(kb)
    assert "product_confirm_unavailable_42_0_1_1" in _callbacks(kb)

    rows = kb.inline_keyboard
    action_row = next(r for r in rows if any("Продажа" in b.text for b in r))
    cancel_row = next(r for r in rows if r and r[0].text == "❌ Отмена")
    assert len(action_row) == 2
    assert action_row[0].text == "💰 Продажа"
    assert action_row[1].text == "📦 Перемещение"
    assert getattr(action_row[0], "style", None) == "success"
    assert getattr(action_row[1], "style", None) == "danger"
    assert action_row is not cancel_row
    assert len(cancel_row) == 1


def test_unavailable_keyboard_report_visible_and_encoded():
    kb = get_product_status_confirmation_keyboard(
        7,
        "unavailable",
        report_enabled=True,
    )
    labels = _labels(kb)
    assert "🟢 Отчет Иван/Саша" in labels
    assert "product_confirm_unavailable_7_1_1_0" in _callbacks(kb)
    assert "product_confirm_unavailable_7_1_1_1" in _callbacks(kb)


def test_archive_product_title_and_card():
    sale = {"name": "iPhone 12 Pro", "archive_kind": "sale"}
    transfer = {"name": "iPhone 12 Pro", "archive_kind": "transfer"}
    assert _archive_product_title(sale) == "iPhone 12 Pro"
    assert _archive_product_title(transfer) == "📦 iPhone 12 Pro"

    card = format_product_card_html(
        {
            "name": "iPhone 12 Pro",
            "status": "unavailable",
            "archive_kind": "transfer",
            "archived_at": "2026-08-20T10:00:00+00:00",
            "created_at": "2026-08-01T10:00:00+00:00",
        }
    )
    assert "📦 Архив · не продажа" in card
