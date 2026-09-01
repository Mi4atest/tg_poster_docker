"""Сухой прогон: авто-ретрай ВК, backoff загрузки фото, разнесение площадок."""
import asyncio
import inspect
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.scheduler.orchestrator import PublicationOrchestrator
from app.scheduler.platform_worker import PlatformWorker
from app.scheduler.publish_stagger import PublishStagger, STAGGERED_PLATFORMS
from app.scheduler.queue_manager import format_retry_marker, parse_retry_attempt
from app.workers.vk.publisher import VKPublisher
from app.workers.vk.product_publisher import VKProductPublisher
from app.workers.vk.upload_retry import (
    VK_WALL_UPLOAD_ATTEMPTS,
    vk_upload_backoff_seconds,
)


class RetryMarkerTest(unittest.TestCase):
    def test_first_attempt_without_marker(self):
        self.assertEqual(parse_retry_attempt(None), 1)
        self.assertEqual(parse_retry_attempt(""), 1)
        self.assertEqual(parse_retry_attempt("Failed to publish"), 1)

    def test_new_marker_increments(self):
        self.assertEqual(parse_retry_attempt("[retry=1/5] boom"), 2)
        self.assertEqual(parse_retry_attempt("[retry=4/5] boom"), 5)

    def test_legacy_ig_marker_still_counts(self):
        self.assertEqual(
            parse_retry_attempt("[ig_retry=3/5] Failed to publish post x to instagram"),
            4,
        )

    def test_format_retry_marker(self):
        self.assertEqual(format_retry_marker(2, 5), "[retry=2/5]")


class VkUploadBackoffTest(unittest.TestCase):
    def test_attempts_are_six(self):
        self.assertEqual(VK_WALL_UPLOAD_ATTEMPTS, 6)

    def test_backoff_sequence_5_to_15(self):
        self.assertEqual(vk_upload_backoff_seconds(1), 5)
        self.assertEqual(vk_upload_backoff_seconds(2), 8)
        self.assertEqual(vk_upload_backoff_seconds(3), 11)
        self.assertEqual(vk_upload_backoff_seconds(4), 13)
        self.assertEqual(vk_upload_backoff_seconds(5), 15)
        self.assertEqual(vk_upload_backoff_seconds(6), 15)

    def test_flood_codes_add_extra_delay(self):
        flood8 = SimpleNamespace(code=8)
        flood29 = SimpleNamespace(code=29)
        photo100 = SimpleNamespace(code=100)
        self.assertEqual(vk_upload_backoff_seconds(1, flood8), 10)
        self.assertEqual(vk_upload_backoff_seconds(5, flood29), 20)
        self.assertEqual(vk_upload_backoff_seconds(1, photo100), 5)

    def test_publisher_uses_shared_backoff(self):
        src = inspect.getsource(VKPublisher._upload_photo_with_retry)
        self.assertIn("vk_upload_backoff_seconds", src)
        self.assertIn("VK_WALL_UPLOAD_ATTEMPTS", src)
        self.assertNotIn("attempt * 2", src)

    def test_market_post_timeout_is_capped(self):
        from app.workers.vk.upload_retry import VK_MARKET_POST_TIMEOUT, VK_MARKET_UPLOAD_ATTEMPTS

        self.assertEqual(VK_MARKET_POST_TIMEOUT[1], 30)
        self.assertEqual(VK_MARKET_UPLOAD_ATTEMPTS, 6)
        self.assertTrue(
            VKProductPublisher._is_retryable_upload_error(TimeoutError("timed out"))
        )


class PlatformRetryPolicyTest(unittest.TestCase):
    def test_vk_failure_requeues(self):
        qm = MagicMock()
        qm.requeue_for_retry.return_value = True
        worker = PlatformWorker("vk", qm)
        with patch.object(worker, "_platform_interval_seconds", return_value=180):
            worker._handle_publish_failure(17, "post-vk", "Failed to publish")
        qm.requeue_for_retry.assert_called_once()
        kwargs = qm.requeue_for_retry.call_args
        self.assertEqual(kwargs.args[0], 17)
        self.assertEqual(kwargs.kwargs["max_attempts"], 5)
        self.assertEqual(kwargs.kwargs["delay_seconds"], 180)
        qm.mark_as_failed.assert_not_called()

    def test_vk_retry_delay_clamped_to_2_3_min(self):
        qm = MagicMock()
        qm.requeue_for_retry.return_value = True
        worker = PlatformWorker("vk", qm)
        with patch.object(worker, "_platform_interval_seconds", return_value=60):
            worker._handle_publish_failure(1, "p", "err")
        self.assertEqual(
            qm.requeue_for_retry.call_args.kwargs["delay_seconds"], 120
        )
        with patch.object(worker, "_platform_interval_seconds", return_value=600):
            worker._handle_publish_failure(1, "p", "err")
        self.assertEqual(
            qm.requeue_for_retry.call_args.kwargs["delay_seconds"], 180
        )

    def test_vk_retries_exhausted_marks_failed(self):
        qm = MagicMock()
        qm.requeue_for_retry.return_value = False
        worker = PlatformWorker("vk", qm)
        worker._handle_publish_failure(9, "post-vk", "Failed")
        qm.mark_as_failed.assert_called_once_with(9, "Failed")

    def test_instagram_still_retries(self):
        qm = MagicMock()
        qm.requeue_for_retry.return_value = True
        worker = PlatformWorker("instagram", qm)
        with patch.object(worker, "_platform_interval_seconds", return_value=1800):
            worker._handle_publish_failure(3, "post-ig", "ig fail")
        self.assertEqual(
            qm.requeue_for_retry.call_args.kwargs["delay_seconds"], 1800
        )
        qm.mark_as_failed.assert_not_called()

    def test_telegram_still_fails_immediately(self):
        qm = MagicMock()
        worker = PlatformWorker("telegram", qm)
        worker._handle_publish_failure(4, "post-tg", "tg fail")
        qm.requeue_for_retry.assert_not_called()
        qm.mark_as_failed.assert_called_once_with(4, "tg fail")


class PublishStaggerTest(unittest.TestCase):
    def test_staggered_platforms(self):
        self.assertEqual(
            STAGGERED_PLATFORMS, frozenset({"vk", "telegram", "max", "instagram"})
        )

    def test_avito_skips_gap(self):
        stagger = PublishStagger(gap_seconds=30)

        async def _run():
            t0 = time.monotonic()
            ok = await stagger.wait_turn("avito")
            return ok, time.monotonic() - t0

        ok, elapsed = asyncio.run(_run())
        self.assertTrue(ok)
        self.assertLess(elapsed, 0.2)

    def test_second_platform_waits_gap(self):
        stagger = PublishStagger(gap_seconds=0.08)

        async def _run():
            t0 = time.monotonic()
            self.assertTrue(await stagger.wait_turn("vk"))
            self.assertTrue(await stagger.wait_turn("telegram"))
            return time.monotonic() - t0

        elapsed = asyncio.run(_run())
        self.assertGreaterEqual(elapsed, 0.07)

    def test_abort_does_not_claim_slot(self):
        stagger = PublishStagger(gap_seconds=5)

        async def _run():
            ok = await stagger.wait_turn("vk", should_abort=lambda: True)
            return ok, stagger._last_start_monotonic

        ok, last = asyncio.run(_run())
        self.assertFalse(ok)
        self.assertIsNone(last)

    def test_orchestrator_has_stagger(self):
        svc = MagicMock()
        svc.is_global_publication_pause.return_value = False
        svc.get_platform_publication_pauses.return_value = {}
        for target in (
            "app.scheduler.orchestrator.QueueManager",
            "app.scheduler.orchestrator.AvitoArchiveWorker",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        gs = patch(
            "app.scheduler.orchestrator.get_settings_service",
            return_value=svc,
        )
        gs.start()
        self.addCleanup(gs.stop)
        orch = PublicationOrchestrator()
        self.assertIsInstance(orch.publish_stagger, PublishStagger)
        orch.start()
        self.assertIs(orch.workers["vk"].orchestrator.publish_stagger, orch.publish_stagger)


if __name__ == "__main__":
    unittest.main()
