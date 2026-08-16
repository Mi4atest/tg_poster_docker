"""Unit-тесты для app.utils.vk_urls."""
from app.utils.vk_urls import (
    VK_API_ORIGIN,
    VK_WEB_ORIGIN,
    api_method_url,
    market_product_url,
    rewrite_vk_com_to_ru,
    story_url,
    wall_post_url,
)


def test_builders_use_vk_ru():
    assert market_product_url(1, 2) == "https://vk.ru/market-1?w=product-1_2"
    assert wall_post_url(-1, 9) == "https://vk.ru/wall-1_9"
    assert story_url(-5, 3) == "https://vk.ru/stories-5_3"
    assert api_method_url("wall.getById") == f"{VK_API_ORIGIN}/method/wall.getById"
    assert VK_WEB_ORIGIN == "https://vk.ru"


def test_rewrite_hosts():
    assert (
        rewrite_vk_com_to_ru("https://vk.com/market-1?w=product-1_2")
        == "https://vk.ru/market-1?w=product-1_2"
    )
    assert rewrite_vk_com_to_ru("https://www.vk.com/wall-1_2") == "https://vk.ru/wall-1_2"
    assert rewrite_vk_com_to_ru("https://m.vk.com/x") == "https://m.vk.ru/x"
    assert (
        rewrite_vk_com_to_ru("https://api.vk.com/method/x")
        == "https://api.vk.ru/method/x"
    )
    assert rewrite_vk_com_to_ru("vk.com/market-1") == "vk.ru/market-1"
    assert rewrite_vk_com_to_ru("https://vk.ru/ok") == "https://vk.ru/ok"
    assert rewrite_vk_com_to_ru(None) is None
