"""vk_market_ops: flood → очередь, без серии коротких ретраев."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class VkMarketOpsQueueTests(unittest.TestCase):
    def test_apply_or_enqueue_queues_on_flood(self):
        from app.utils import vk_market_ops as mod
        from app.utils.vk_flood_gate import VkFloodBlocked

        with patch.object(
            mod, "_execute", side_effect=VkFloodBlocked("flood")
        ), patch.object(mod, "note_vk_flood", return_value=True), patch.object(
            mod, "enqueue"
        ) as enqueue, patch.object(
            mod, "_retry_delay", return_value=__import__("datetime").timedelta(hours=3)
        ), patch.object(
            mod, "vk_user_error_detail", return_value="flood"
        ), patch(
            "app.utils.time_msk.format_dashboard_ts_msk", return_value="12:00"
        ):
            ok, detail = mod.apply_or_enqueue(
                product_id=1,
                vk_product_id=100,
                action="price",
                payload={"price": 50000},
            )
        self.assertFalse(ok)
        self.assertIn("повтор", detail.lower())
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["action"], "price")

    def test_process_due_skips_when_flood_active(self):
        from app.utils import vk_market_ops as mod
        from datetime import datetime, timezone

        with patch.object(
            mod, "vk_flood_until", return_value=datetime.now(timezone.utc)
        ):
            self.assertIsNone(mod.process_due_operation())


if __name__ == "__main__":
    unittest.main()
