"""Тесты расчёта «цены без скидки» для ценников."""
from app.utils.price_tag_pricing import calc_strike_price, format_price_tag_amount


def test_calc_strike_price_5_percent_rounds_up_to_100():
    assert calc_strike_price(43500, 5) == 45700  # 45675 → 45700
    assert calc_strike_price(10000, 5) == 10500
    assert calc_strike_price(10001, 5) == 10600


def test_calc_strike_price_10_percent():
    assert calc_strike_price(43500, 10) == 47900
    assert calc_strike_price(10000, 10) == 11000


def test_calc_strike_price_invalid_percent_defaults_to_5():
    assert calc_strike_price(10000, 7) == 10500


def test_calc_strike_price_zero():
    assert calc_strike_price(0, 5) == 0


def test_format_price_tag_amount():
    assert format_price_tag_amount(43500) == "43 500"
    assert format_price_tag_amount(900) == "900"
