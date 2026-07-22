"""Smoke-тесты PDF-ценников."""
from datetime import date

from app.utils.price_tag_data import PriceTagItem, build_price_tag_item
from app.utils.price_tag_pdf import build_price_tags_pdf_bytes, pdf_page_count


def _sample_item(i: int) -> PriceTagItem:
    return PriceTagItem(
        product_id=i,
        name=f"Apple iPhone 14 128Gb Blue #{i}",
        subtitle="не_активирован",
        description=(
            "Товар бывший в употреблении, оригинал, комплект полный, "
            "не активирован, без RuStore, гарантия 14 дней"
        ),
        cash_price_rub=43500,
        cash_price_display="43 500",
        strike_price_rub=45700,
        strike_price_display="45 700",
        print_date=date(2026, 7, 22).strftime("%d.%m.%Y"),
    )


def test_build_price_tag_item_from_product():
    product = {
        "id": 1,
        "name": "Test Phone",
        "price": "43500₽",
        "collection_name": "iPhone новые",
        "price_tag_subtitle": None,
        "price_tag_description": None,
    }
    settings = {
        "default_subtitle": "",
        "default_descriptions": {
            "iPhone новые": "Товар бывший в употреблении, оригинал, комплект полный, не активирован, гарантия 14 дней",
        },
        "fixed_footer_text": "fallback",
    }
    item = build_price_tag_item(product, markup_percent=5, settings=settings)
    assert item is not None
    assert item.cash_price_rub == 43500
    assert item.strike_price_rub == 45700
    assert "употреблении" in item.description


def test_pdf_single_tag():
    items = [_sample_item(1)]
    pdf = build_price_tags_pdf_bytes([1], items=items)
    assert pdf.startswith(b"%PDF")
    assert pdf_page_count(pdf) >= 1


def test_pdf_16_tags_one_page():
    items = [_sample_item(i) for i in range(16)]
    pdf = build_price_tags_pdf_bytes(list(range(16)), items=items)
    assert pdf_page_count(pdf) == 1


def test_pdf_17_tags_two_pages():
    items = [_sample_item(i) for i in range(17)]
    pdf = build_price_tags_pdf_bytes(list(range(17)), items=items)
    assert pdf_page_count(pdf) == 2
