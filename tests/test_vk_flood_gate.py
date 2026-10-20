"""Flood gate + локальный API guard."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch


class VkFloodGateTests(unittest.TestCase):
    def test_mark_flood_does_not_shorten_later_until(self):
        from app.utils import vk_flood_gate as mod

        later = datetime.now(timezone.utc) + timedelta(hours=5)
        with patch.object(mod, "_load_from_settings", return_value=later), patch.object(
            mod, "_store_to_settings"
        ) as store, patch.object(mod, "_now", return_value=datetime.now(timezone.utc)):
            until = mod.mark_flood("test")
        self.assertEqual(until, later)
        store.assert_called_once()

    def test_note_exception_code_9_arms_gate(self):
        from app.utils import vk_flood_gate as mod

        exc = MagicMock()
        exc.code = 9
        with patch.object(mod, "mark_flood") as mark, patch(
            "app.utils.vk_client.vk_api_error_code", return_value=9
        ):
            self.assertTrue(mod.note_exception(exc, "wall.post"))
        mark.assert_called_once()

    def test_raise_if_blocked(self):
        from app.utils import vk_flood_gate as mod

        future = datetime.now(timezone.utc) + timedelta(hours=1)
        with patch.object(mod, "flood_until", return_value=future):
            with self.assertRaises(mod.VkFloodBlocked):
                mod.raise_if_blocked("test")

    def test_clear_flood(self):
        from app.utils import vk_flood_gate as mod

        with patch.object(mod, "_store_to_settings") as store:
            mod.clear_flood("unit")
        store.assert_called_once()


class VkApiGuardTests(unittest.TestCase):
    def test_before_vk_call_respects_flood(self):
        from app.utils import vk_api_guard as mod
        from app.utils.vk_flood_gate import VkFloodBlocked

        with patch(
            "app.utils.vk_api_guard.raise_if_vk_flood_blocked",
            side_effect=VkFloodBlocked("blocked"),
        ):
            with self.assertRaises(VkFloodBlocked):
                mod.before_vk_call("wall.post")

    def test_before_vk_call_serializes(self):
        from app.utils import vk_api_guard as mod

        with patch("app.utils.vk_api_guard.raise_if_vk_flood_blocked"), patch(
            "app.utils.vk_api_guard._min_interval", return_value=0.05
        ), patch("app.utils.vk_api_guard.time.sleep") as sleep:
            mod.before_vk_call("a")
            mod.before_vk_call("b")
        # второй вызов должен подождать минимальный интервал
        self.assertTrue(sleep.called)


if __name__ == "__main__":
    unittest.main()
