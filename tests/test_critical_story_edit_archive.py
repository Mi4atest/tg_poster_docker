"""Unit tests for story stale-reuse, edit FSM, and archive/IG filter bugs."""
import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_function(path: Path, name: str):
    """Load a single top-level function without importing heavy dependencies."""
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            ns = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), ns)
            return ns[name]
    raise AssertionError(f"{name} not found in {path}")


class TestStorySnapshotRefresh(unittest.TestCase):
    def setUp(self):
        self.apply = _load_function(
            REPO_ROOT / "app/api/endpoints/stories.py",
            "apply_post_snapshot_to_story",
        )

    def test_unpublished_story_picks_up_new_media(self):
        story = SimpleNamespace(
            media_file_id="old",
            model_name="Old Model",
            price="100₽",
            is_published=False,
            published_at="stale",
        )
        needs_republish = self.apply(story, "new", "New Model", "200₽")
        self.assertFalse(needs_republish)
        self.assertEqual(story.media_file_id, "new")
        self.assertEqual(story.model_name, "New Model")
        self.assertEqual(story.price, "200₽")
        self.assertFalse(story.is_published)

    def test_published_story_with_changed_media_clears_success_flag(self):
        story = SimpleNamespace(
            media_file_id="old",
            model_name="Old Model",
            price="100₽",
            is_published=True,
            published_at="2024-01-01",
        )
        needs_republish = self.apply(story, "new", "Old Model", "100₽")
        self.assertTrue(needs_republish)
        self.assertEqual(story.media_file_id, "new")
        self.assertFalse(story.is_published)
        self.assertIsNone(story.published_at)

    def test_unchanged_published_story_stays_published(self):
        story = SimpleNamespace(
            media_file_id="same",
            model_name="Model",
            price="100₽",
            is_published=True,
            published_at="2024-01-01",
        )
        needs_republish = self.apply(story, "same", "Model", "100₽")
        self.assertFalse(needs_republish)
        self.assertTrue(story.is_published)
        self.assertEqual(story.published_at, "2024-01-01")


class TestArchiveIncludesInstagram(unittest.TestCase):
    def setUp(self):
        self.is_full = _load_function(
            REPO_ROOT / "app/bot/handlers/post_management.py",
            "is_post_fully_published",
        )

    def test_vk_tg_only_is_not_archived(self):
        post = {
            "is_published_vk": True,
            "is_published_telegram": True,
            "is_published_instagram": False,
        }
        self.assertFalse(self.is_full(post))

    def test_all_three_platforms_is_archived(self):
        post = {
            "is_published_vk": True,
            "is_published_telegram": True,
            "is_published_instagram": True,
        }
        self.assertTrue(self.is_full(post))

    def test_get_posts_api_uses_helper(self):
        source = (REPO_ROOT / "app/bot/handlers/post_management.py").read_text(encoding="utf-8")
        start = source.index("async def get_posts_api")
        end = source.index("async def get_post_api", start)
        body = source[start:end]
        self.assertIn("is_post_fully_published(p)", body)
        self.assertNotIn(
            'p.get("is_published_vk") and p.get("is_published_telegram")]',
            body,
        )


class TestEditTextAdvancesToPhotosState(unittest.TestCase):
    def test_process_edit_text_sets_waiting_for_photos(self):
        source = (REPO_ROOT / "app/bot/handlers/post_management.py").read_text(encoding="utf-8")
        start = source.index("async def process_edit_text")
        end = source.index("async def delete_copy_message", start)
        body = source[start:end]
        self.assertIn("await state.set_state(PostEdit.waiting_for_photos)", body)


if __name__ == "__main__":
    unittest.main()
