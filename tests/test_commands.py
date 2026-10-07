"""تست تشخیص دستورها: «ai cod» و «کدرز»."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Config  # noqa: E402
from core import match_command, normalize_text  # noqa: E402


class TestCommandMatching(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config()

    def test_owner_command_variants(self):
        for text in ["ai cod", "AI COD", "Ai Cod", "  ai   cod  ", "ai cod\n"]:
            self.assertEqual(match_command(text, self.cfg), "owner", msg=repr(text))

    def test_owner_command_negatives(self):
        for text in ["aicod", "ai code", "cod ai", "ai codx", "سلام ai cod", "کدرز"]:
            self.assertNotEqual(match_command(text, self.cfg), "owner", msg=repr(text))

    def test_unknown_text_matches_nothing(self):
        for text in ["aicod", "ai code", "cod ai", "ai codx", "سلام ai cod", "hi"]:
            self.assertIsNone(match_command(text, self.cfg), msg=repr(text))

    def test_kodrez_command(self):
        self.assertEqual(match_command("کدرز", self.cfg), "kodrez")

    def test_kodrez_with_half_space(self):
        # نیم‌فاصله («کدرز») هم باید همان دستور شناخته شود
        self.assertEqual(match_command("ک\u200cدرز", self.cfg), "kodrez")

    def test_kodrez_with_arabic_letters(self):
        # «ك» عربی == «ک» فارسی
        self.assertEqual(match_command("كدرز", self.cfg), "kodrez")

    def test_kodrez_negatives(self):
        for text in ["کدرزها", "کد رز", "سلام کدرز", "...", ""]:
            self.assertIsNone(match_command(text, self.cfg), msg=repr(text))

    def test_normalize_collapses_spaces(self):
        self.assertEqual(normalize_text("  AI \u200c COD  "), "ai cod")


if __name__ == "__main__":
    unittest.main(verbosity=2)
