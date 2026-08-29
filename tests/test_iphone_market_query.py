import pytest

from app.utils.iphone_market_query import MarketQueryError, parse_iphone_market_query


@pytest.mark.parametrize(
    ("raw", "model", "memory"),
    [
        ("13 mini 128", "iPhone 13 mini", 128),
        ("13 мини 128", "iPhone 13 mini", 128),
        ("iPhone 13 mini 128 ГБ", "iPhone 13 mini", 128),
        ("APPLE 15 ПРО МАКС 512 gb", "iPhone 15 Pro Max", 512),
        ("iphone 16e 1 ТБ", "iPhone 16E", 1024),
    ],
)
def test_parse_market_query_variants(raw, model, memory):
    query = parse_iphone_market_query(raw)
    assert query.model == model
    assert query.memory_gb == memory


@pytest.mark.parametrize("raw", ["", "13 mini", "Samsung S25 256", "13 mini 777"])
def test_parse_market_query_rejects_incomplete_or_unknown(raw):
    with pytest.raises(MarketQueryError):
        parse_iphone_market_query(raw)
