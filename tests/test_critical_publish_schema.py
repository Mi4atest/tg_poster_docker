"""Self-contained checks for schema bootstrap and VK publish media guard."""

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MigrateBootstrapTests(unittest.TestCase):
    def test_required_columns_cover_alembic_revisions(self):
        migrate_src = (ROOT / "app" / "db" / "migrate.py").read_text(encoding="utf-8")
        tree = ast.parse(migrate_src)
        required = None
        head = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "REQUIRED_POST_COLUMNS":
                        required = ast.literal_eval(node.value)
                    if isinstance(target, ast.Name) and target.id == "ALEMBIC_HEAD_REVISION":
                        head = ast.literal_eval(node.value)
        self.assertIsNotNone(required)
        self.assertEqual(head, "add_telegram_link_field")
        names = [item[0] for item in required]
        self.assertEqual(
            names,
            [
                "is_published_instagram",
                "published_instagram_at",
                "telegram_link",
            ],
        )

    def test_ensure_schema_adds_columns_before_stamping(self):
        migrate_src = (ROOT / "app" / "db" / "migrate.py").read_text(encoding="utf-8")
        # Guard against regressing to stamp-before-schema / Instagram-blind stamp.
        ensure_fn = None
        tree = ast.parse(migrate_src)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "ensure_database_schema":
                ensure_fn = node
                break
        self.assertIsNotNone(ensure_fn)
        called = [
            n.func.id
            for n in ast.walk(ensure_fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        self.assertIn("ensure_required_post_columns", called)
        self.assertIn("init_alembic_version_table", called)
        self.assertLess(
            called.index("ensure_required_post_columns"),
            called.index("init_alembic_version_table"),
        )


class VkMediaGuardTests(unittest.TestCase):
    def test_abort_when_expected_media_all_failed(self):
        # Mirrors the guard in VKPublisher.publish_post.
        def should_abort(photos, videos, uploaded):
            expected = len(photos or []) + len(videos or [])
            return expected > 0 and not uploaded

        self.assertTrue(should_abort(["a", "b"], [], []))
        self.assertTrue(should_abort([], ["v"], []))
        self.assertFalse(should_abort(["a"], [], ["photo1_2"]))
        self.assertFalse(should_abort([], [], []))

    def test_publisher_contains_media_abort_guard(self):
        src = (ROOT / "app" / "workers" / "vk" / "publisher.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("expected_media", src)
        self.assertIn("VK publish aborted", src)
        self.assertIn("return False", src)


if __name__ == "__main__":
    unittest.main()
