"""Regression tests for single-media Telegram and incomplete IG media."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SingleMediaPublishTests(unittest.TestCase):
    def test_telegram_publisher_uses_send_photo_for_single_item(self) -> None:
        source = (
            ROOT / "app" / "workers" / "telegram" / "publisher.py"
        ).read_text(encoding="utf-8")
        self.assertIn("len(media) == 1", source)
        self.assertIn("send_photo", source)
        self.assertIn("send_video", source)
        # Single-item path must not call send_media_group (Telegram requires 2-10).
        single_idx = source.index("len(media) == 1")
        group_idx = source.index("elif len(media) > 1")
        single_window = source[single_idx:group_idx]
        self.assertIn("send_photo", single_window)
        self.assertIn("send_video", single_window)
        self.assertNotIn("send_media_group", single_window)

    def test_instagram_publisher_rejects_incomplete_downloads(self) -> None:
        source = (
            ROOT / "app" / "workers" / "instagram" / "publisher.py"
        ).read_text(encoding="utf-8")
        self.assertIn("expected_media_count", source)
        self.assertIn("Неполные медиа для Instagram", source)
        marker = "len(media_paths) < expected_media_count"
        idx = source.index(marker)
        window = source[idx : idx + 800]
        self.assertIn("return False", window)
        self.assertNotIn("is_published_instagram = True", window)

    def test_instagram_publisher_uploads_all_video_only_items(self) -> None:
        source = (
            ROOT / "app" / "workers" / "instagram" / "publisher.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Пост содержит только видео", source)
        self.assertIn("for video_path in video_paths:", source)
        # Must not hard-code only the first video without looping remaining ones.
        self.assertNotIn(
            "Пробуем загрузить первое видео",
            source,
        )
        marker = "Пост содержит только видео"
        idx = source.index(marker)
        window = source[idx : idx + 2500]
        self.assertIn("video_errors", window)
        self.assertIn("return False", window)
        self.assertNotIn("is_published_instagram = True", window)


if __name__ == "__main__":
    unittest.main()
