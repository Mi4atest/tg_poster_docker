"""Тесты матчера пакетного обновления цен."""
from app.utils.bulk_price_matcher import MatchStatus, match_bulk_lines
from app.utils.bulk_price_parser import BulkPriceLine, parse_bulk_price_text
from tests.test_bulk_price_parser import SAMPLE_LIST


def _iphone_product(
    pid: int,
    name: str,
    price: str,
) -> dict:
    return {
        "id": pid,
        "name": name,
        "display_label": None,
        "price": price,
        "collection_name": "iPhone новые",
        "custom_button_id": None,
    }


def _ipad_product(pid: int, name: str, price: str) -> dict:
    return {
        "id": pid,
        "name": name,
        "display_label": None,
        "price": price,
        "collection_name": "iPad",
        "custom_button_id": None,
    }


CATALOG = [
    _iphone_product(1, "iPhone 15 128Gb Blue", "50900₽"),
    _iphone_product(2, "iPhone 17 256Gb Black eSim", "67900₽"),
    _iphone_product(3, "iPhone 17 256Gb White (1+1)", "69900₽"),
    _iphone_product(4, "iPhone 17 Pro 256Gb Blue eSim", "88900₽"),
    _iphone_product(5, "iPhone Air 256Gb Yellow eSim", "70900₽"),
    _ipad_product(10, "iPad 11 128Gb Blue", "34500₽"),
    _ipad_product(11, "iPad Air 11 M4 128Gb Blue WiFi", "56900₽"),
]


def test_match_iphone_15_blue():
    line = BulkPriceLine("15 128 🔵 -", 50900, 50500, 1)
    results = match_bulk_lines([line], CATALOG)
    assert len(results) == 1
    r = results[0]
    assert r.status == MatchStatus.MATCHED
    assert r.product_id == 1
    assert r.line.new_rub == 50500


def test_match_iphone_17_esim():
    line = BulkPriceLine("17 256 ⚫️(esim) -", 67900, 68500, 1)
    results = match_bulk_lines([line], CATALOG)
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].product_id == 2


def test_match_price_mismatch():
    line = BulkPriceLine("17 256 ⚫️(esim) -", 67000, 68500, 1)
    results = match_bulk_lines([line], CATALOG)
    assert results[0].status == MatchStatus.PRICE_MISMATCH
    assert results[0].product_id == 2


def test_match_ipad_blue():
    line = BulkPriceLine("iPad 11 (A16 ) 128 - blue", 34500, 33900, 1)
    results = match_bulk_lines([line], CATALOG)
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].product_id == 10


def test_match_ipad_air():
    line = BulkPriceLine("iPad Air 11 m4 128 wifi - blue", 56900, 54900, 1)
    results = match_bulk_lines([line], CATALOG)
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].product_id == 11


def test_match_not_found():
    line = BulkPriceLine("99 999 🔵 -", 100, 200, 1)
    results = match_bulk_lines([line], CATALOG)
    assert results[0].status == MatchStatus.NOT_FOUND


def test_match_sample_list_parses_all():
    lines = parse_bulk_price_text(SAMPLE_LIST)
    assert len(lines) == 45


def test_match_air_yellow_esim():
    line = BulkPriceLine("Air 256 🟡(esim) -", 70900, 70500, 1)
    results = match_bulk_lines([line], CATALOG)
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].product_id == 5


def test_match_airpods():
    catalog = [
        {
            "id": 20,
            "name": "Apple AirPods 4",
            "display_label": None,
            "price": "10500₽",
            "collection_name": "Airpods",
            "custom_button_id": None,
        },
        {
            "id": 21,
            "name": "Apple AirPods 4 ANC",
            "display_label": None,
            "price": "14400₽",
            "collection_name": "Airpods",
            "custom_button_id": None,
        },
    ]
    line = BulkPriceLine("Airpods 4 anc -", 14400, 14901, 1)
    results = match_bulk_lines([line], catalog)
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].product_id == 21


def test_match_watch_se3():
    catalog = [
        {
            "id": 30,
            "name": "Apple Watch SE 3 44mm Midnight",
            "display_label": None,
            "price": "22900₽",
            "collection_name": "Apple Watch",
            "custom_button_id": None,
        },
    ]
    line = BulkPriceLine("Se3 44 - midnight", 22900, 25501, 1)
    results = match_bulk_lines([line], catalog)
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].product_id == 30


def test_match_ipad_air_gray():
    catalog = [
        {
            "id": 12,
            "name": "iPad Air 11 M4 128Gb Space Gray WiFi",
            "display_label": None,
            "price": "55000₽",
            "collection_name": "iPad",
            "custom_button_id": None,
        },
    ]
    line = BulkPriceLine("iPad Air 11 m4 128 wifi - gray", 55000, 62501, 1)
    results = match_bulk_lines([line], catalog)
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].product_id == 12


def test_match_16e():
    catalog = [
        {
            "id": 40,
            "name": "Apple iPhone 16e 128Gb Black",
            "display_label": None,
            "price": "44500₽",
            "collection_name": "iPhone новые",
            "custom_button_id": None,
        },
    ]
    line = BulkPriceLine("16e 128 ⚫️ -", 44500, 47501, 1)
    results = match_bulk_lines([line], catalog)
    assert results[0].status == MatchStatus.MATCHED
    assert results[0].product_id == 40
