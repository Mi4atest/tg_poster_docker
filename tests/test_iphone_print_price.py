"""Тесты печатного прайса iPhone (строки + PDF)."""
from app.utils.iphone_print_price import (
    build_print_catalog,
    dedupe_tradein_lines,
    extract_color_name_en,
    format_new_iphone_line,
    format_tradein_line,
    group_lines_with_blanks,
    print_model_display,
    split_new_into_columns,
)
from app.utils.iphone_print_pdf import build_iphone_price_pdf_bytes, pdf_page_count


def test_extract_color_space_black():
    assert extract_color_name_en("Apple iPhone Air 256Gb Space Black eSim") == "Space Black"


def test_extract_color_soft_pink():
    assert extract_color_name_en("iPhone 17e 256Gb Soft Pink eSim") == "Soft Pink"


def test_extract_color_yellow():
    assert extract_color_name_en("Apple iPhone 14 Yellow 128Gb Новый") == "Yellow"


def test_format_new_line_no_emoji():
    p = {
        "name": "Apple iPhone 14 Yellow 128Gb Новый (без RuStore)",
        "price": "42900₽",
    }
    line = format_new_iphone_line(p)
    assert line is not None
    assert "🟡" not in line.text
    assert "Yellow" in line.text
    assert "Sim+eSim" in line.text
    assert line.text.endswith("42900")
    assert line.text.startswith("14 128")


def test_format_new_line_esim():
    p = {
        "name": "Apple iPhone 17 Pro 256Gb Silver eSim Новый (без RuStore)",
        "price": "91500₽",
    }
    line = format_new_iphone_line(p)
    assert line is not None
    assert "17 PRO 256 Silver eSim - 91500" == line.text


def test_stock_marker_when_available():
    p = {
        "name": "Apple iPhone 17 Pro 256Gb Silver eSim Новый (без RuStore)",
        "price": "91500₽",
        "availability_status": "available",
    }
    line = format_new_iphone_line(p)
    assert line is not None
    assert line.in_stock is True
    assert line.text.endswith("91500•")
    assert "•" == line.text[-1]


def test_no_stock_marker_on_order():
    p = {
        "name": "Apple iPhone 17 Pro 256Gb Silver eSim Новый (без RuStore)",
        "price": "91500₽",
        "availability_status": "on_order",
    }
    line = format_new_iphone_line(p)
    assert line is not None
    assert line.in_stock is False
    assert "•" not in line.text
    assert line.text.endswith("91500")


def test_color_alias_air_and_pro():
    air = format_new_iphone_line(
        {"name": "Apple iPhone Air 256Gb Black eSim Новый", "price": "74900₽"}
    )
    assert air is not None
    assert "Space Black" in air.text

    pro = format_new_iphone_line(
        {"name": "Apple iPhone 17 Pro 256Gb Blue eSim Новый", "price": "90100₽"}
    )
    assert pro is not None
    assert "Deep Blue" in pro.text

    base = format_new_iphone_line(
        {"name": "Apple iPhone 17 256Gb Blue eSim Новый", "price": "70500₽"}
    )
    assert base is not None
    assert "Mist Blue" in base.text


def test_group_blank_between_esim_and_sim():
    from app.utils.iphone_print_price import PrintPriceLine

    lines = [
        PrintPriceLine("17 256 Black eSim - 1", "17", "256", 1, storage="eSim"),
        PrintPriceLine("17 256 White eSim - 2", "17", "256", 2, storage="eSim"),
        PrintPriceLine("17 256 Black Sim+eSim - 3", "17", "256", 3, storage="Sim+eSim"),
        PrintPriceLine("17 256 White Sim+eSim - 4", "17", "256", 4, storage="Sim+eSim"),
    ]
    grouped = group_lines_with_blanks(lines)
    texts = [L.text if not L.is_blank else "<blank>" for L in grouped]
    assert texts == [
        "17 256 Black eSim - 1",
        "17 256 White eSim - 2",
        "<blank>",
        "17 256 Black Sim+eSim - 3",
        "17 256 White Sim+eSim - 4",
    ]


def test_format_tradein_no_code_no_color():
    p = {
        "name": "iPhone 13 Pro 128Gb Green 2614",
        "price": "37900₽",
    }
    line = format_tradein_line(p)
    assert line is not None
    assert "2614" not in line.text
    assert "🟢" not in line.text
    assert "Green" not in line.text
    assert "13 PRO 128" in line.text
    assert "37900" in line.text


def test_dedupe_tradein():
    from app.utils.iphone_print_price import PrintPriceLine

    a = PrintPriceLine("13 PRO 128 Sim+eSim - 37900", "13 Pro", "128", 37900)
    b = PrintPriceLine("13 PRO 128 Sim+eSim - 37900", "13 Pro", "128", 37900)
    c = PrintPriceLine("14 PRO 128 eSim - 43500", "14 Pro", "128", 43500)
    out = dedupe_tradein_lines([a, b, c])
    assert len(out) == 2


def test_print_model_display_pro_max():
    assert print_model_display("17 Pro Max") == "17 PRO MAX"
    assert print_model_display("16E") == "16e"
    assert print_model_display("Air") == "Air"


def test_split_columns():
    from app.utils.iphone_print_price import PrintPriceLine

    lines = [
        PrintPriceLine("14 128 Yellow Sim+eSim - 1", "14", "128", 1),
        PrintPriceLine("Air 256 Black eSim - 2", "Air", "256", 2),
        PrintPriceLine("17 PRO 256 Silver eSim - 3", "17 Pro", "256", 3),
        PrintPriceLine("17 PRO MAX 256 Silver eSim - 4", "17 Pro Max", "256", 4),
    ]
    grouped = group_lines_with_blanks(lines)
    left, right = split_new_into_columns(grouped)
    left_models = {L.sort_model for L in left if not L.is_blank}
    right_models = {L.sort_model for L in right if not L.is_blank}
    assert "14" in left_models
    assert "Air" in left_models
    assert "17 Pro" in right_models
    assert "17 Pro Max" in right_models


def test_build_pdf_smoke_one_page():
    news = [
        {
            "name": "Apple iPhone 14 Yellow 128Gb Новый (без RuStore)",
            "price": "42900₽",
        },
        {
            "name": "Apple iPhone 17 256Gb Black eSim Новый (без RuStore)",
            "price": "71500₽",
        },
        {
            "name": "Apple iPhone 17 Pro 256Gb Silver eSim Новый (без RuStore)",
            "price": "91500₽",
        },
        {
            "name": "Apple iPhone 17 Pro Max 512Gb Silver Новый (без RuStore)",
            "price": "127900₽",
        },
    ]
    trades = [
        {"name": "iPhone 13 Pro 128Gb Green 2614", "price": "37900₽"},
        {"name": "iPhone 13 Pro 128Gb White 4181", "price": "37900₽"},
        {"name": "iPhone 14 Pro 128Gb White 7937", "price": "43500₽"},
        {"name": "iPhone 16 Pro Max 256Gb Natural Titanium 6935", "price": "79900₽"},
    ]
    left, right, tradein = build_print_catalog(news, trades)
    assert left
    assert right
    assert len(tradein) == 3  # два 13 Pro дедупятся

    pdf = build_iphone_price_pdf_bytes(news, trades)
    assert pdf[:4] == b"%PDF"
    assert pdf_page_count(pdf) == 1
