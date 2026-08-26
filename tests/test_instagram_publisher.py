"""Graph-only публикация в Instagram: без instagrapi fallback."""
import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers.instagram import publisher as ig_publisher

PUBLISHER_PATH = Path(ig_publisher.__file__)


class InstagramPublisherGraphOnlyTest(unittest.TestCase):
    def test_publisher_source_has_no_instagrapi(self):
        tree = ast.parse(PUBLISHER_PATH.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.split(".", 1)[0])
        self.assertNotIn("instagrapi", imported)
        class_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        self.assertNotIn("InstagramPublisher", class_names)

    def test_publish_uses_graph_when_enabled(self):
        with patch("app.workers.instagram.publisher.InstagramGraphPublisher") as cls:
            pub = MagicMock()
            pub.enabled = True
            pub.publish_post = AsyncMock(return_value=True)
            cls.return_value = pub
            with patch(
                "app.workers.instagram.story_publisher.maybe_auto_publish_instagram_story",
                new=AsyncMock(),
            ) as story:
                ok = asyncio.run(ig_publisher.publish_post_to_instagram("post-1"))
            self.assertTrue(ok)
            pub.publish_post.assert_awaited_once_with("post-1")
            story.assert_awaited_once_with("post-1")

    def test_publish_does_not_fallback_when_graph_disabled(self):
        with patch("app.workers.instagram.publisher.InstagramGraphPublisher") as cls:
            pub = MagicMock()
            pub.enabled = False
            pub.publish_post = AsyncMock(return_value=False)
            cls.return_value = pub
            with patch(
                "app.workers.instagram.story_publisher.maybe_auto_publish_instagram_story",
                new=AsyncMock(),
            ) as story:
                ok = asyncio.run(ig_publisher.publish_post_to_instagram("post-1"))
            self.assertFalse(ok)
            pub.publish_post.assert_awaited_once_with("post-1")
            story.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
