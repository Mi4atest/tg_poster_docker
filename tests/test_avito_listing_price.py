"""Тесты resolve_avito_listing_price: N−3000 только для новых iPhone при тоггле."""
from app.utils.avito_listing_price import resolve_avito_listing_price


def _product(*, name: str, collection: str) -> dict:
    return {"name": name, "collection_name": collection}


def test_toggle_off_keeps_price():
    product = _product(name="Apple iPhone 15 128Gb Black", collection="iPhone новые")
    assert resolve_avito_listing_price(product, 68500, discount_enabled=False) == 68500


def test_used_iphone_keeps_price():
    product = _product(name="Apple iPhone 13 128Gb Midnight", collection="iPhone б/у")
    assert resolve_avito_listing_price(product, 42000, discount_enabled=True) == 42000


def test_new_non_iphone_keeps_price():
    product = _product(name="AirPods Pro 2", collection="Airpods")
    assert resolve_avito_listing_price(product, 22000, discount_enabled=True) == 22000


def test_new_iphone_applies_discount():
    product = _product(name="Apple iPhone 15 128Gb Black", collection="iPhone новые")
    assert resolve_avito_listing_price(product, 68500, discount_enabled=True) == 65500


def test_new_iphone_clamp_to_one():
    product = _product(name="Apple iPhone 11 64Gb", collection="iPhone новые")
    assert resolve_avito_listing_price(product, 2000, discount_enabled=True) == 1


def test_custom_without_button_keeps_price():
    product = _product(name="Apple iPhone 16 128Gb", collection="custom")
    assert resolve_avito_listing_price(product, 80000, discount_enabled=True) == 80000


def test_custom_menu_iphone_applies_discount():
    """Конструктор кнопок: collection=custom + custom_button_id — тоже «новые» iPhone."""
    product = {
        "name": "iPhone 17 Pro Max 256Gb Silver eSim",
        "collection_name": "custom",
        "custom_button_id": 15,
    }
    assert resolve_avito_listing_price(product, 103990, discount_enabled=True) == 100990


def test_custom_menu_non_iphone_keeps_price():
    product = {
        "name": "AirPods Pro 2",
        "collection_name": "custom",
        "custom_button_id": 3,
    }
    assert resolve_avito_listing_price(product, 22000, discount_enabled=True) == 22000
