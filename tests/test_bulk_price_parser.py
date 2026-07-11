"""Тесты парсера пакетного обновления цен."""
from app.utils.bulk_price_parser import parse_bulk_price_text, parse_label

SAMPLE_LIST = """
iPad 11 (A16 ) 128 - blue: 34500 → 33900 (-600₽)
iPad 11 (A16 ) 128 - yellow: 33500 → 33900 (+400₽)
iPad Air 11 m4 128 wifi - blue: 56900 → 54900 (-2000₽)
iPad Air 11 m4 128 wifi - gray: 55900 → 55000 (-900₽)
15 128 🔵 -: 50900 → 50500 (-400₽)
15 128 ⚫️ -: 50900 → 50500 (-400₽)
15 plus 128 - от: 54500 → 53900 (-600₽)
16 128 ⚪️ -: 59500 → 57900 (-1600₽)
16 128 🌸 -: 60900 → 60500 (-400₽)
16 128 🟢 -: 55500 → 54900 (-600₽)
17 256 ⚫️(esim) -: 67900 → 68500 (+600₽)
17 256 ⚪️(esim) -: 68900 → 68500 (-400₽)
17 256 🔵(esim) -: 66900 → 66500 (-400₽)
17 256 🟢(esim) -: 66900 → 66500 (-400₽)
17 256 🟣(esim) -: 69500 → 68900 (-600₽)
17 256 ⚫️(1+1) -: 70500 → 68900 (-1600₽)
17 256 ⚪️(1+1) -: 69900 → 68900 (-1000₽)
17 256 🔵(1+1) -: 68900 → 68500 (-400₽)
17 256 🟣(1+1) -: 70500 → 70900 (+400₽)
Air 256 ⚫️(esim) -: 71500 → 70900 (-600₽)
Air 256 ⚪️(esim) -: 72500 → 71900 (-600₽)
Air 256 🟡(esim) -: 70900 → 70500 (-400₽)
17 PRO 256 🔵(esim) -: 88900 → 87900 (-1000₽)
17 PRO 256 🟠(esim) -: 88500 → 87500 (-1000₽)
17 PRO 256 ⚪️(1+1) -: 97900 → 96900 (-1000₽)
17 PRO 256 🔵(1+1) -: 97900 → 96900 (-1000₽)
17 PRO 256 🟠(1+1) -: 97900 → 96900 (-1000₽)
17 PRO 512 ⚪️(esim) -: 107500 → 105900 (-1600₽)
17 PRO 512 🔵(esim) -: 106500 → 105500 (-1000₽)
17 PRO 512 🟠(esim) -: 106500 → 104900 (-1600₽)
17 PRO 512 ⚪️(1+1) -: 116900 → 115500 (-1400₽)
17 PRO 512 🔵(1+1) -: 114900 → 113900 (-1000₽)
17 PRO 512 🟠(1+1) -: 113500 → 112900 (-600₽)
17 PRO MAX 256 ⚪️(esim) -: 95900 → 94900 (-1000₽)
17 PRO MAX 256 🔵(esim) -: 95500 → 94500 (-1000₽)
17 PRO MAX 256 🟠(esim) -: 95500 → 93900 (-1600₽)
17 PRO MAX 256 ⚪️(1+1) -: 108900 → 107900 (-1000₽)
17 PRO MAX 256 🔵(1+1) -: 107500 → 106900 (-600₽)
17 PRO MAX 256 🟠(1+1) -: 107500 → 106500 (-1000₽)
17 PRO MAX 512 ⚪️(esim) -: 113500 → 113900 (+400₽)
17 PRO MAX 512 🔵(esim) -: 112900 → 111900 (-1000₽)
17 PRO MAX 512 🟠(esim) -: 113900 → 112900 (-1000₽)
17 PRO MAX 512 ⚪️(1+1) -: 127500 → 124900 (-2600₽)
17 PRO MAX 512 🔵(1+1) -: 123900 → 122500 (-1400₽)
17 PRO MAX 512 🟠(1+1) -: 125900 → 123900 (-2000₽)
""".strip()


def test_parse_bulk_price_text_count():
    lines = parse_bulk_price_text(SAMPLE_LIST)
    assert len(lines) == 45


def test_parse_bulk_price_text_first_line():
    lines = parse_bulk_price_text(SAMPLE_LIST)
    first = lines[0]
    assert first.raw_label == "iPad 11 (A16 ) 128 - blue"
    assert first.old_rub == 34500
    assert first.new_rub == 33900


def test_parse_label_ipad():
    p = parse_label("iPad 11 (A16 ) 128 - blue")
    assert p.category == "ipad"
    assert p.model == "iPad 11"
    assert p.memory == "128"
    assert p.color == "🔵"


def test_parse_label_ipad_air_gray():
    p = parse_label("iPad Air 11 m4 128 wifi - gray")
    assert p.category == "ipad"
    assert p.model == "iPad Air M4"
    assert p.memory == "128"
    assert p.color == "🔘"


def test_parse_label_ipad_air():
    p = parse_label("iPad Air 11 m4 128 wifi - gray")
    assert p.category == "ipad"
    assert p.model == "iPad Air M4"
    assert p.memory == "128"


def test_parse_label_iphone_15():
    p = parse_label("15 128 🔵 -")
    assert p.category == "iphone"
    assert p.model == "15"
    assert p.memory == "128"
    assert p.color == "🔵"


def test_parse_label_iphone_17_esim():
    p = parse_label("17 256 ⚫️(esim) -")
    assert p.model == "17"
    assert p.memory == "256"
    assert p.color == "⚫️"
    assert p.storage == "esim"


def test_parse_label_iphone_17_pro_max():
    p = parse_label("17 PRO MAX 256 ⚪️(1+1) -")
    assert p.model == "17 Pro Max"
    assert p.memory == "256"
    assert p.storage == "1+1"


def test_parse_label_air():
    p = parse_label("Air 256 🟡(esim) -")
    assert p.model == "Air"
    assert p.memory == "256"
    assert p.color == "🟡"
    assert p.storage == "esim"


def test_parse_label_15_plus():
    p = parse_label("15 plus 128 - от")
    assert p.model == "15 Plus"
    assert p.memory == "128"


def test_parse_label_airpods():
    p = parse_label("Airpods 4 anc -")
    assert p.category == "airpods"
    assert p.model == "AirPods 4 ANC"


def test_parse_label_airpods_pro2():
    p = parse_label("Airpods pro 2 (usb-c) -")
    assert p.category == "airpods"
    assert p.model == "AirPods Pro 2"


def test_parse_label_watch_se3():
    p = parse_label("Se3 44 - midnight")
    assert p.category == "watch"
    assert p.model == "AW SE 3"
    assert p.size == "44mm"
    assert p.color == "⚫️"


def test_parse_label_watch_11():
    p = parse_label("11 42 - black")
    assert p.category == "watch"
    assert p.model == "AW 11"
    assert p.size == "42mm"
    assert p.color == "⚫️"


def test_parse_label_17e_cyrillic():
    p = parse_label("17е 256 ⚫️ (esim) -")
    assert p.model == "17E"
    assert p.memory == "256"
    assert p.storage == "esim"


def test_parse_label_16e():
    p = parse_label("16e 128 ⚫️ -")
    assert p.model == "16E"
    assert p.memory == "128"


def test_parse_new_item_line():
    from app.utils.bulk_price_parser import parse_bulk_price_input

    text = "Pencil 1 -: Новый элемент, цена: 8501₽"
    lines, new_items, skipped = parse_bulk_price_input(text)
    assert not lines
    assert len(new_items) == 1
    assert new_items[0].new_rub == 8501
