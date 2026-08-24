"""Тесты ADMIN_USER_IDS."""
import unittest
from unittest.mock import patch

from app.bot.utils.admin_auth import is_admin_user


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


if __name__ == "__main__":
    unittest.main()
