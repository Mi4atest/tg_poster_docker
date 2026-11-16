"""Шапка кастомных папок «Список новых» показывает всё поддерево."""
from app.utils.custom_branch_header import build_custom_branch_header


def test_custom_folder_header_lists_subtree_not_only_stock():
    products = [
        {
            "id": 1,
            "name": "Apple iPhone 18 Pro 256Gb White eSim Новый",
            "price": "111500₽",
            "collection_name": "custom",
            "custom_button_id": 10,
            "vk_product_link": "https://vk.com/1",
            "availability_status": "on_order",
        },
        {
            "id": 2,
            "name": "Apple iPhone 18 Pro Max 256Gb Black eSim Новый",
            "price": "123000₽",
            "collection_name": "custom",
            "custom_button_id": 11,
            "vk_product_link": None,
            "availability_status": "available",
        },
    ]
    text = build_custom_branch_header(
        "iPhone 18",
        subtree_products=products,
        has_child_nodes=True,
        has_leaf_products=False,
    )
    assert text.startswith("🆕 iPhone 18\n\n")
    assert "18 Pro" in text
    assert "18 Pro Max" in text
    assert "111500₽" in text
    assert "123000₽" in text
    assert "🟢 В наличии" not in text
    assert "Подменю:" not in text
    assert text.rstrip().endswith("Выберите модель:")


def test_custom_folder_header_empty_without_subtree():
    text = build_custom_branch_header(
        "AW 12",
        subtree_products=[],
        has_child_nodes=True,
        has_leaf_products=False,
    )
    # Без товаров в поддереве остаётся только призыв (кнопки детей всё равно есть).
    assert "🆕 AW 12" in text
    assert "Выберите модель:" in text
    assert "Пока пусто" not in text


def test_custom_leaf_header_prompt():
    products = [
        {
            "id": 3,
            "name": "Apple Watch 12 42mm Dark Bronze",
            "price": "35900₽",
            "collection_name": "custom",
            "custom_button_id": 20,
            "availability_status": "available",
        }
    ]
    text = build_custom_branch_header(
        "AW 12 42mm",
        subtree_products=products,
        has_child_nodes=False,
        has_leaf_products=True,
    )
    assert "Dark Bronze" in text or "🟤" in text or "AW 12" in text
    assert text.rstrip().endswith("Выберите позицию:")
