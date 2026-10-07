"""تست تشخیص دستورها: «ai cod» / «ai code» و «کدرز»."""

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

    def test_owner_command_accepts_ai_code_spelling(self):
        """«ai code» (املای دیگری که کاربر خواسته) همان دستور مالک است."""
        for text in ["ai code", "AI CODE", "AI  code ", "  ai   code\n"]:
            self.assertEqual(match_command(text, self.cfg), "owner", msg=repr(text))

    def test_owner_command_aliases_are_configurable(self):
        import dataclasses
        cfg = dataclasses.replace(self.cfg, owner_command_aliases=("ai code",))
        self.assertEqual(match_command("ai code", cfg), "owner")
        self.assertEqual(match_command("ai cod", cfg), "owner")   # دستور اصلی همیشه پذیرفته است

    def test_owner_command_negatives(self):
        for text in ["aicod", "cod ai", "ai codx", "ai codes", "ai cod e", "سلام ai cod", "کدرز"]:
            self.assertNotEqual(match_command(text, self.cfg), "owner", msg=repr(text))

    def test_unknown_text_matches_nothing(self):
        for text in ["aicod", "cod ai", "ai codx", "ai codes", "سلام ai cod", "hi"]:
            self.assertIsNone(match_command(text, self.cfg), msg=repr(text))

    def test_env_overrides_for_owner_command(self):
        """کلیدهای .env واقعاً اثر دارند (ACOD_OWNER_ALIASES / ACOD_ANNOUNCE_ON_OWNER_REPEAT)."""
        import os
        from config import Config as Cfg

        old = {k: os.environ.get(k) for k in
               ("ACOD_OWNER_ALIASES", "ACOD_ANNOUNCE_ON_OWNER_REPEAT")}
        try:
            os.environ["ACOD_OWNER_ALIASES"] = "ai codex"
            os.environ["ACOD_ANNOUNCE_ON_OWNER_REPEAT"] = "0"
            cfg = Cfg.from_env()
            self.assertEqual(match_command("ai cod", cfg), "owner")      # دستور اصلی همیشه
            self.assertEqual(match_command("ai codex", cfg), "owner")    # نام دلخواه از env
            self.assertEqual(match_command("ai code", cfg), None)
            self.assertFalse(cfg.announce_on_owner_repeat)
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

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
