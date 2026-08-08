"""Regression tests for publish correctness fixes."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PublishCorrectnessTests(unittest.TestCase):
    def test_instagram_publisher_fails_on_video_errors(self) -> None:
        source = (
            ROOT / "app" / "workers" / "instagram" / "publisher.py"
        ).read_text(encoding="utf-8")
        self.assertIn("video_errors", source)
        self.assertIn("Частичная публикация в Instagram", source)
        # Ensure the mixed-media path returns False instead of falling through
        # to is_published_instagram = True after a video failure.
        marker = "video_errors.append"
        idx = source.index(marker)
        window = source[idx : idx + 2500]
        self.assertIn("return False", window)
        self.assertNotIn("is_published_instagram = True", window)

    def test_telegram_story_uses_buffered_input_file(self) -> None:
        source = (
            ROOT / "app" / "workers" / "telegram" / "story_publisher.py"
        ).read_text(encoding="utf-8")
        self.assertIn("BufferedInputFile", source)
        self.assertNotIn("from aiogram.types import InputFile", source)
        self.assertNotIn("InputFile(story_image_buffer", source)


if __name__ == "__main__":
    unittest.main()
