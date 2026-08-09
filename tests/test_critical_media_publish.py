"""Unit tests for critical media/publish correctness bugs."""
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_merge_photos_for_skip():
    """Load merge_photos_for_skip without importing aiogram-heavy module."""
    source = (REPO_ROOT / "app/bot/handlers/post_creation.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == "merge_photos_for_skip":
            ns = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "post_creation.py", "exec"), ns)
            return ns["merge_photos_for_skip"]
    raise AssertionError("merge_photos_for_skip not found")


class TestMergePhotosForSkip(unittest.TestCase):
    def setUp(self):
        self.merge = _load_merge_photos_for_skip()

    def test_media_group_temp_photos_are_merged(self):
        temp = [{"file_id": "A"}, {"file_id": "B"}]
        self.assertEqual(self.merge([], temp), ["A", "B"])

    def test_single_photos_preserved_when_no_temp(self):
        self.assertEqual(self.merge(["X", "Y"], []), ["X", "Y"])

    def test_does_not_wipe_existing_when_temp_present(self):
        temp = [{"file_id": "A"}]
        self.assertEqual(self.merge(["B"], temp), ["A", "B"])

    def test_deduplicates(self):
        temp = [{"file_id": "A"}, {"file_id": "A"}]
        self.assertEqual(self.merge(["A"], temp), ["A"])


class TestIntervalPublishSkipsPublishedPlatforms(unittest.TestCase):
    def test_handler_guards_each_platform(self):
        source = (REPO_ROOT / "app/bot/handlers/post_management.py").read_text(encoding="utf-8")
        self.assertIn('if not post.get("is_published_vk"):', source)
        self.assertIn('if not post.get("is_published_telegram"):', source)
        self.assertIn('if not post.get("is_published_instagram"):', source)
        # Ensure the old always-publish block is gone from the interval helper.
        start = source.index("async def publish_posts_with_interval")
        end = source.index("async def show_archived_posts", start)
        interval_body = source[start:end]
        self.assertIn('if not post.get("is_published_vk"):', interval_body)
        self.assertNotIn(
            'vk_result = await publish_post_api(\n                    post_id,\n                    "vk"',
            interval_body,
        )


class TestInstagramMediaCacheInvalidation(unittest.TestCase):
    def test_publisher_always_redownloads(self):
        source = (REPO_ROOT / "app/workers/instagram/publisher.py").read_text(encoding="utf-8")
        self.assertIn("Always re-download against current file_ids", source)
        self.assertNotIn("if not os.path.exists(photo_path):", source)

    def test_update_post_removes_stale_cached_files(self):
        source = (REPO_ROOT / "app/api/endpoints/posts.py").read_text(encoding="utf-8")
        self.assertIn("media_changed", source)
        self.assertIn('name.startswith(("photo_", "video_"))', source)


class TestSkipPhotosShadowingRemoved(unittest.TestCase):
    def test_only_one_skip_photos_handler(self):
        source = (REPO_ROOT / "app/bot/handlers/post_creation.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("async def skip_photos"), 1)
        self.assertNotIn('await state.update_data(videos=[])', source.split("async def skip_photos", 1)[1].split("async def skip_videos", 1)[0])


if __name__ == "__main__":
    unittest.main()
