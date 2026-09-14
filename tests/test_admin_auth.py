"""Тесты ADMIN_USER_IDS / VIEWER_USER_IDS."""
import unittest
from unittest.mock import patch

from app.bot.utils.admin_auth import (
    get_user_role,
    is_admin_user,
    is_viewer_callback_allowed,
    is_viewer_fsm_state_allowed,
    is_viewer_user,
)


def _parse_user_lists(allowed_raw: str, admin_raw: str) -> tuple[list[int], list[int]]:
    """Логика из app.config.settings (без load_dotenv)."""
    allowed = [int(user_id) for user_id in allowed_raw.split(",") if user_id]
    admin_raw = admin_raw.strip()
    admin = (
        [int(user_id) for user_id in admin_raw.split(",") if user_id]
        if admin_raw
        else list(allowed)
    )
    return allowed, admin


class AdminUserIdsTest(unittest.TestCase):
    def test_admin_subset(self):
        allowed, admin = _parse_user_lists("111,222", "111")
        self.assertEqual(allowed, [111, 222])
        self.assertEqual(admin, [111])

    def test_admin_empty_falls_back_to_allowed(self):
        allowed, admin = _parse_user_lists("111,222", "")
        self.assertEqual(admin, [111, 222])

    def test_is_admin_user(self):
        with patch("app.bot.utils.admin_auth.ADMIN_USER_IDS", [111]):
            self.assertTrue(is_admin_user(111))
            self.assertFalse(is_admin_user(222))
            self.assertFalse(is_admin_user(None))


class ViewerRoleTest(unittest.TestCase):
    def test_viewer_not_admin(self):
        with (
            patch("app.bot.utils.admin_auth.ADMIN_USER_IDS", [111]),
            patch("app.bot.utils.admin_auth.VIEWER_USER_IDS", [222, 111]),
        ):
            self.assertTrue(is_viewer_user(222))
            self.assertFalse(is_viewer_user(111))
            self.assertFalse(is_viewer_user(333))
            self.assertFalse(is_viewer_user(None))
            self.assertEqual(get_user_role(111), "admin")
            self.assertEqual(get_user_role(222), "viewer")
            self.assertEqual(get_user_role(333), "operator")

    def test_viewer_callbacks_allow_showcase_and_market(self):
        self.assertTrue(is_viewer_callback_allowed("products_menu"))
        self.assertTrue(is_viewer_callback_allowed("products_list"))
        self.assertTrue(is_viewer_callback_allowed("new_products_menu"))
        self.assertTrue(is_viewer_callback_allowed("product_42"))
        self.assertTrue(is_viewer_callback_allowed("new_product_7"))
        self.assertTrue(is_viewer_callback_allowed("iphone_version_13"))
        self.assertTrue(is_viewer_callback_allowed("new_iphone_ver_13"))
        self.assertTrue(is_viewer_callback_allowed("avito_market_start"))
        self.assertTrue(is_viewer_callback_allowed("avito_market_history"))
        self.assertTrue(is_viewer_callback_allowed("avito_market_open:12"))
        self.assertTrue(is_viewer_callback_allowed("avito_market_wl:run:1"))
        self.assertTrue(is_viewer_callback_allowed("products_search"))
        self.assertTrue(is_viewer_callback_allowed("products_archive"))
        self.assertTrue(is_viewer_callback_allowed("price_stale_item_3"))
        self.assertTrue(is_viewer_callback_allowed("back_to_main"))

    def test_viewer_callbacks_block_mutations(self):
        self.assertFalse(is_viewer_callback_allowed("create_post"))
        self.assertFalse(is_viewer_callback_allowed("open_settings"))
        self.assertFalse(is_viewer_callback_allowed("sync_telegram_links"))
        self.assertFalse(is_viewer_callback_allowed("avito_match_queue"))
        self.assertFalse(is_viewer_callback_allowed("product_unavailable_42"))
        self.assertFalse(is_viewer_callback_allowed("product_restore_42"))
        self.assertFalse(is_viewer_callback_allowed("product_delete_42"))
        self.assertFalse(is_viewer_callback_allowed("product_price_42"))
        self.assertFalse(is_viewer_callback_allowed("product_avito_link_42"))
        self.assertFalse(is_viewer_callback_allowed("new_product_toggle_avail_7"))
        self.assertFalse(is_viewer_callback_allowed("new_product_price_7"))
        self.assertFalse(is_viewer_callback_allowed("new_product_more_7"))
        self.assertFalse(is_viewer_callback_allowed("new_product_sell_7"))
        self.assertFalse(is_viewer_callback_allowed("bulk_price_start"))
        self.assertFalse(is_viewer_callback_allowed("iphone_print_price_pdf"))
        self.assertFalse(is_viewer_callback_allowed("price_tags_select"))
        self.assertFalse(is_viewer_callback_allowed("evening_report_start"))
        self.assertFalse(is_viewer_callback_allowed("note_add"))
        self.assertFalse(is_viewer_callback_allowed(""))
        self.assertFalse(is_viewer_callback_allowed(None))

    def test_viewer_fsm_states(self):
        self.assertTrue(is_viewer_fsm_state_allowed("ProductSearch:waiting_for_query"))
        self.assertTrue(is_viewer_fsm_state_allowed("IphoneMarketPriceState:waiting_for_query"))
        self.assertFalse(is_viewer_fsm_state_allowed("ProductPriceEdit:waiting_for_price"))
        self.assertFalse(is_viewer_fsm_state_allowed(None))


if __name__ == "__main__":
    unittest.main()
