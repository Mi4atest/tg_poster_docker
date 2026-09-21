"""Клавиатуры роли директора (viewer)."""
import unittest

from app.bot.keyboards.main_keyboard import get_main_keyboard
from app.bot.keyboards.new_products_keyboard import get_new_product_detail_keyboard
from app.bot.keyboards.product_keyboard import (
    get_product_detail_keyboard,
    get_products_menu_keyboard,
)


def _callbacks(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _labels(markup) -> list[str]:
    return [btn.text for row in markup.inline_keyboard for btn in row]


class ViewerKeyboardTest(unittest.TestCase):
    def test_main_keyboard_readonly_only_products(self):
        kb = get_main_keyboard(readonly=True)
        self.assertEqual(_callbacks(kb), ["products_menu"])
        self.assertEqual(_labels(kb), ["📦 Товары"])

    def test_main_keyboard_operator_keeps_create_post(self):
        kb = get_main_keyboard()
        cbs = _callbacks(kb)
        self.assertIn("create_post", cbs)
        self.assertIn("products_menu", cbs)
        self.assertIn("open_settings", cbs)

    def test_products_menu_readonly_hides_ops(self):
        kb = get_products_menu_keyboard(
            avito_market_enabled=True,
            avito_unlinked_count=4,
            readonly=True,
        )
        labels = _labels(kb)
        cbs = _callbacks(kb)
        self.assertIn("📦 Список б/у товаров", labels)
        self.assertIn("🆕 Список новых", labels)
        self.assertIn("🔍 Поиск товара", labels)
        self.assertIn("📁 Архив товаров", labels)
        self.assertIn("📊 Оценка рынка Avito", labels)
        self.assertIn("avito_market_start", cbs)
        self.assertNotIn("sync_telegram_links", cbs)
        self.assertNotIn("avito_match_queue", cbs)
        self.assertNotIn("back_to_main", cbs)
        self.assertNotIn("price_stale_list", cbs)

    def test_products_menu_readonly_puts_stale_before_archive(self):
        kb = get_products_menu_keyboard(
            avito_market_enabled=True,
            readonly=True,
            stale_badge_count=4,
        )
        cbs = _callbacks(kb)
        labels = _labels(kb)
        self.assertIn("price_stale_list", cbs)
        self.assertIn("🕰 Застой (4)", labels)
        self.assertLess(cbs.index("price_stale_list"), cbs.index("products_archive"))
        self.assertGreater(cbs.index("price_stale_list"), cbs.index("avito_market_start"))
        self.assertTrue(all("Авито без ссылки" not in text for text in labels))
        self.assertTrue(all("Обновление постов" not in text for text in labels))
        self.assertTrue(all("Назад" not in text for text in labels))

    def test_used_product_card_readonly_nav_only(self):
        kb = get_product_detail_keyboard(42, status="active", readonly=True)
        cbs = _callbacks(kb)
        self.assertIn("products_list", cbs)
        self.assertIn("back_to_main", cbs)
        self.assertFalse(
            any(
                cb.startswith(prefix)
                for prefix in (
                    "product_unavailable_",
                    "product_restore_",
                    "product_delete_",
                    "product_price_",
                    "product_avito_",
                )
                for cb in cbs
            )
        )

    def test_used_product_card_operator_keeps_mutations(self):
        kb = get_product_detail_keyboard(42, status="active")
        cbs = _callbacks(kb)
        self.assertIn("product_unavailable_42", cbs)
        self.assertIn("product_price_42", cbs)

    def test_new_product_card_readonly_nav_only(self):
        kb = get_new_product_detail_keyboard(
            7,
            status="active",
            availability_status="available",
            readonly=True,
        )
        cbs = _callbacks(kb)
        labels = _labels(kb)
        self.assertNotIn("new_product_toggle_avail_7", cbs)
        self.assertNotIn("new_product_price_7", cbs)
        self.assertNotIn("new_product_avito_7", cbs)
        self.assertNotIn("new_product_more_7", cbs)
        self.assertIn("back_to_main", cbs)
        self.assertNotIn("🟢 В наличии", labels)

    def test_new_product_card_operator_keeps_mutations(self):
        kb = get_new_product_detail_keyboard(
            7, status="active", availability_status="available"
        )
        cbs = _callbacks(kb)
        self.assertIn("new_product_toggle_avail_7", cbs)
        self.assertIn("new_product_price_7", cbs)
        self.assertIn("new_product_more_7", cbs)


if __name__ == "__main__":
    unittest.main()
