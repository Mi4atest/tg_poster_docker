"""Тесты сводки поиска товаров (агрегаты + компактные строки архива)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bot.utils.product_search_formatter import (
    archive_sale_price_rub,
    format_archive_compact_line,
    format_product_search_text,
    format_search_aggregates,
    sort_archive_for_search,
)


def _iso(days_ago: int, *, hour: int = 12) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def test_archive_sale_price_strips_payment_emoji():
    assert archive_sale_price_rub({"final_price": "46900₽💳", "price": "45000₽"}) == 46900
    assert archive_sale_price_rub({"price": "45000₽"}) == 45000
    assert archive_sale_price_rub({}) is None


def test_compact_line_uses_project_color_emoji_not_squares():
    product = {
        "name": "iPhone 16 128Gb Black 4123",
        "final_price": "46900₽💳",
        "price": "45000₽",
        "archived_at": _iso(3),
        "created_at": _iso(14),
        "archive_kind": "sale",
    }
    line = format_archive_compact_line(product)
    assert "🟦" not in line
    assert "⬛" not in line
    assert "⬜" not in line
    assert "💳" not in line
    assert "⚫️" in line
    assert "46900₽" in line
    assert "·" in line
    assert line.startswith("• ")


def test_compact_line_transfer():
    product = {
        "name": "iPhone 16 128Gb Blue 4001",
        "price": "50000₽",
        "archived_at": _iso(1),
        "created_at": _iso(10),
        "archive_kind": "transfer",
    }
    line = format_archive_compact_line(product)
    assert "📦 не прод." in line
    assert "🔵" in line
    assert "50000₽" not in line


def test_sort_sales_before_transfers_newest_first():
    older_sale = {
        "id": 1,
        "name": "A",
        "archived_at": _iso(10),
        "created_at": _iso(20),
        "archive_kind": "sale",
        "price": "40000",
    }
    newer_sale = {
        "id": 2,
        "name": "B",
        "archived_at": _iso(1),
        "created_at": _iso(5),
        "archive_kind": "sale",
        "price": "45000",
    }
    transfer = {
        "id": 3,
        "name": "C",
        "archived_at": _iso(0),
        "created_at": _iso(2),
        "archive_kind": "transfer",
        "price": "1",
    }
    sorted_list = sort_archive_for_search([older_sale, transfer, newer_sale])
    assert [p["id"] for p in sorted_list] == [2, 1, 3]


def test_aggregates_and_collapsed_header():
    archive = [
        {
            "id": 1,
            "name": "iPhone 16 128Gb Black 1",
            "final_price": "46900₽💳",
            "price": "45000",
            "archived_at": _iso(2),
            "created_at": _iso(12),
            "archive_kind": "sale",
        },
        {
            "id": 2,
            "name": "iPhone 16 128Gb White 2",
            "final_price": "51000₽",
            "price": "50000",
            "archived_at": _iso(20),
            "created_at": _iso(30),
            "archive_kind": "sale",
        },
        {
            "id": 3,
            "name": "iPhone 16 128Gb Blue 3",
            "price": "1",
            "archived_at": _iso(1),
            "created_at": _iso(3),
            "archive_kind": "transfer",
        },
    ]
    archive = sort_archive_for_search(archive)
    agg = "\n".join(format_search_aggregates(archive))
    assert "продаж: 2" in agg
    assert "перемещ.: 1" in agg
    assert "💰 Продажи:" in agg
    assert "мед." in agg
    assert "⏱" in agg
    assert "💳" not in agg

    text = format_product_search_text(
        "16 128gb",
        active=[{"id": 9, "name": "live"}],
        archive=archive,
        archive_expanded=False,
    )
    assert "🔍 Результаты поиска: «16 128gb»" in text
    assert "📦 В наличии: 1" in text
    assert "📁 В архиве: 3" in text
    assert "Архив · стр." not in text
    assert "• " not in text


def test_expanded_archive_shows_page_lines_full_header():
    archive = []
    for i in range(12):
        archive.append(
            {
                "id": i + 1,
                "name": f"iPhone 16 128Gb Black {4000 + i}",
                "final_price": f"{40000 + i * 100}₽",
                "price": f"{40000 + i * 100}",
                "archived_at": _iso(i),
                "created_at": _iso(i + 10),
                "archive_kind": "sale",
            }
        )
    archive = sort_archive_for_search(archive)
    text = format_product_search_text(
        "16 128gb",
        active=[],
        archive=archive,
        archive_expanded=True,
        arc_page=0,
        per_page=10,
    )
    assert "📦 В наличии: ничего не найдено" in text
    assert "📁 В архиве: 12 (продаж: 12)" in text
    assert "💰 Продажи:" in text
    assert "Архив · стр. 1/2:" in text
    assert text.count("\n• ") == 10

    text2 = format_product_search_text(
        "16 128gb",
        active=[],
        archive=archive,
        archive_expanded=True,
        arc_page=1,
        per_page=10,
    )
    assert "Архив · стр. 2/2:" in text2
    assert text2.count("\n• ") == 2
