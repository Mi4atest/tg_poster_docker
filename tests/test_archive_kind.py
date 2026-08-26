"""Режим продажи / перемещения при снятии б/у с витрины."""
from __future__ import annotations

from app.bot.handlers.product_management import (
    _archive_product_title,
    format_product_card_html,
)
from app.bot.keyboards.product_keyboard import get_product_status_confirmation_keyboard
from app.utils.archive_kind import (
    ARCHIVE_KIND_SALE,
    ARCHIVE_KIND_TRANSFER,
    archive_kind_toggle_answer,
    format_unavailable_confirm_text,
    is_transfer_archive,
    normalize_archive_kind,
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


def test_unavailable_confirm_text_sale_and_transfer():
    sale = format_unavailable_confirm_text("iPhone 13 Pro 128Gb", ARCHIVE_KIND_SALE)
    assert sale.startswith("💰 Продажа")
    assert "сводку месяца" in sale
    assert "нажмите «Продажа» ниже" in sale

    transfer = format_unavailable_confirm_text("iPhone 13 Pro 128Gb", ARCHIVE_KIND_TRANSFER)
    assert transfer.startswith("📦 Перемещение")
    assert "не попадёт" in transfer
    assert "Отчет" not in transfer


def test_toggle_answer():
    assert archive_kind_toggle_answer(ARCHIVE_KIND_SALE) == "Режим: продажа"
    assert archive_kind_toggle_answer(ARCHIVE_KIND_TRANSFER) == "Режим: перемещение"


def test_sale_keyboard_default():
    kb = get_product_status_confirmation_keyboard(42, "unavailable")
    labels = _labels(kb)
    assert "💰 Продажа" in labels
    assert "🔴 Отчет Иван/Саша" in labels
    assert "🟢 Пометить ТГ/IG/Max" in labels
    assert "✅ В архив как продажу" in labels
    assert "product_toggle_archive_kind_42" in _callbacks(kb)
    assert "product_confirm_unavailable_42_0_1_0" in _callbacks(kb)


def test_transfer_keyboard_hides_report():
    kb = get_product_status_confirmation_keyboard(
        42, "unavailable", archive_kind=ARCHIVE_KIND_TRANSFER
    )
    labels = _labels(kb)
    assert "📦 Перемещение" in labels
    assert "✅ В архив как перемещение" in labels
    assert all("Отчет Иван/Саша" not in t for t in labels)
    assert "product_confirm_unavailable_42_0_1_1" in _callbacks(kb)


def test_transfer_forces_report_flag_off_in_confirm():
    kb = get_product_status_confirmation_keyboard(
        7,
        "unavailable",
        report_enabled=True,
        archive_kind=ARCHIVE_KIND_TRANSFER,
    )
    assert "product_confirm_unavailable_7_0_1_1" in _callbacks(kb)


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
