"""Regression tests for Telegram MarkdownV2 formatting."""

import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path


def _load_formatter():
    """Load app.utils.text_formatter with a stubbed signatures module."""
    repo_root = Path(__file__).resolve().parents[1]
    sys.modules.setdefault("app", types.ModuleType("app"))
    sys.modules.setdefault("app.utils", types.ModuleType("app.utils"))

    signatures = types.ModuleType("app.utils.signatures")
    signatures.get_telegram_signature = lambda enabled=True: ""
    signatures.get_vk_signature = lambda enabled=True: ""
    signatures.get_instagram_signature = lambda enabled=True: ""
    sys.modules["app.utils.signatures"] = signatures

    path = repo_root / "app" / "utils" / "text_formatter.py"
    spec = importlib.util.spec_from_file_location("text_formatter_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FormatForTelegramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tf = _load_formatter()

    def test_literal_underscores_stay_escaped(self):
        out = self.tf.format_for_telegram(
            "MacBook_Air_M2\nS/N: ABC_123\n💵 Цена: 50000 руб"
        )
        self.assertIn(r"ABC\_123", out)
        self.assertIn(r"MacBook\_Air\_M2", out)
        # Intentional bold markers around the model name are restored.
        self.assertRegex(out, r"(?<!\\)\*MacBook\\_Air\\_M2(?<!\\)\*")

    def test_old_blanket_unescape_would_break_serial(self):
        """Guard against regressing to replace('\\*', '*').replace('\\_', '_')."""
        out = self.tf.format_for_telegram("Title\nS/N: F2LX3ABC_123")
        self.assertNotIn("F2LX3ABC_123", out.replace(r"\_", ""))


if __name__ == "__main__":
    unittest.main()
