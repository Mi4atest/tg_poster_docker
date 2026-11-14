"""Идемпотентность IG: skip уже опубликованных + recover после ложного fail (code 4)."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers.instagram.graph_publisher import InstagramGraphPublisher


class InstagramIdempotencyTest(unittest.TestCase):
    def test_likely_false_publish_failure_detects_rate_limit(self):
        pub = InstagramGraphPublisher.__new__(InstagramGraphPublisher)
        pub._last_graph_error_code = 4
        pub._last_graph_error = "Application request limit reached"
        self.assertTrue(pub._is_likely_false_publish_failure())

        pub._last_graph_error_code = 9007
        pub._last_graph_error = "Media ID is not available"
        self.assertFalse(pub._is_likely_false_publish_failure())

    def test_publish_post_skips_when_already_published(self):
        pub = InstagramGraphPublisher.__new__(InstagramGraphPublisher)
        pub._last_graph_error = None
        pub._last_published_media_id = None
        pub.access_token = "tok"
        pub.ig_user_id = "123"

        post = SimpleNamespace(
            id="p1",
            is_published_instagram=True,
            instagram_media_id="ig-media-1",
            photos=["a"],
            videos=[],
            text="hi",
            storage_path=None,
        )

        db = MagicMock()
        with patch(
            "app.workers.instagram.graph_publisher.SessionLocal", return_value=db
        ), patch(
            "app.workers.instagram.graph_publisher.fetch_post", return_value=post
        ), patch.object(
            pub, "_create_media_container", new=AsyncMock()
        ) as create_mock:
            ok = asyncio.run(pub.publish_post("p1"))

        self.assertTrue(ok)
        create_mock.assert_not_awaited()

    def test_recover_live_publish_uses_caption_match_after_code4(self):
        pub = InstagramGraphPublisher.__new__(InstagramGraphPublisher)
        pub._last_graph_error_code = 4
        pub._last_graph_error = "rate limit"
        pub._last_published_media_id = None
        pub.ig_user_id = "123"
        pub.access_token = "tok"
        pub.api_version = "v19.0"
        pub.timeout_seconds = 5

        with patch.object(
            pub, "_publish_creation_once", new=AsyncMock(return_value=False)
        ), patch.object(
            pub,
            "_find_recent_media_id_by_caption",
            new=AsyncMock(return_value="media-recovered"),
        ), patch("app.workers.instagram.graph_publisher.asyncio.sleep", new=AsyncMock()):
            mid = asyncio.run(
                pub._recover_live_publish_after_error("creation-1", "same caption")
            )

        self.assertEqual(mid, "media-recovered")
        self.assertEqual(pub._last_published_media_id, "media-recovered")

    def test_recover_skipped_for_non_rate_limit(self):
        pub = InstagramGraphPublisher.__new__(InstagramGraphPublisher)
        pub._last_graph_error_code = 9007
        pub._last_graph_error = "Media ID is not available"

        with patch.object(
            pub, "_publish_creation_once", new=AsyncMock()
        ) as once, patch.object(
            pub, "_find_recent_media_id_by_caption", new=AsyncMock()
        ) as find:
            mid = asyncio.run(
                pub._recover_live_publish_after_error("creation-1", "cap")
            )

        self.assertIsNone(mid)
        once.assert_not_awaited()
        find.assert_not_awaited()


class PlatformWorkerIgRequeueGuardTest(unittest.TestCase):
    def test_handle_failure_completes_when_already_published(self):
        from app.scheduler.platform_worker import PlatformWorker

        qm = MagicMock()
        worker = PlatformWorker("instagram", qm)
        with patch.object(worker, "_instagram_already_published", return_value=True):
            worker._handle_publish_failure(42, "post-x", "Failed to publish")

        qm.mark_as_completed.assert_called_once_with(42)
        qm.requeue_for_retry.assert_not_called()
        qm.mark_as_failed.assert_not_called()

    def test_fail_bumps_last_attempt_at_for_pacing(self):
        from datetime import datetime, timezone, timedelta

        from app.scheduler.platform_worker import PlatformWorker

        qm = MagicMock()
        qm.requeue_for_retry = MagicMock(return_value=True)
        worker = PlatformWorker("instagram", qm)
        worker.last_published_at = datetime.now(timezone.utc) - timedelta(seconds=600)

        with patch(
            "app.scheduler.platform_worker.publish_post_to_instagram",
            new=AsyncMock(return_value=False),
        ), patch.object(
            worker, "_platform_interval_seconds", return_value=180
        ), patch.object(
            worker, "_instagram_already_published", return_value=False
        ):
            ok = asyncio.run(worker.publish_post(1, "p-fail"))

        self.assertFalse(ok)
        self.assertIsNotNone(worker.last_attempt_at)
        # Якорь pacing — последняя попытка, не старый успех.
        self.assertEqual(worker._pacing_anchor(), worker.last_attempt_at)
        elapsed = (
            datetime.now(timezone.utc) - worker._pacing_anchor()
        ).total_seconds()
        self.assertLess(elapsed, 2)

    def test_ig_uri_retry_longer_than_user_interval(self):
        from app.scheduler.platform_worker import PlatformWorker

        worker = PlatformWorker("instagram", MagicMock())
        with patch.object(worker, "_platform_interval_seconds", return_value=180), patch(
            "app.workers.instagram.publisher.last_instagram_publish_error",
            return_value="Only photo or video | Не удалось извлечь медиафайл по URI",
        ):
            delay = worker._retry_delay_seconds()
        # 2×3мин = 6мин, в коридоре 5–12.
        self.assertEqual(delay, 360)

        with patch.object(worker, "_platform_interval_seconds", return_value=180), patch(
            "app.workers.instagram.publisher.last_instagram_publish_error",
            return_value="timeout talking to graph",
        ):
            delay_plain = worker._retry_delay_seconds()
        self.assertEqual(delay_plain, 180)


if __name__ == "__main__":
    unittest.main()
