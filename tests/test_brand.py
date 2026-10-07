"""تست متن و قالب‌بندی: Bold واقعی + نقل‌قول شیشه‌ای (blockquote) + لینک دست‌نخورده."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brand  # noqa: E402
from splusthon import types  # noqa: E402


def utf16_units(text: str) -> str:
    """نمایش متن به‌صورت واحدهای UTF-16 (برای بررسی دقیق آفست‌ها)."""
    return text.encode("utf-16-le").decode("utf-16-le")


class TestBrandText(unittest.TestCase):
    def test_full_text_is_exactly_the_requested_layout(self):
        expected = (
            "🦊 ❂ | 𝗮𝗰𝗼𝗱 .𝗮𝗶 #plus"
            "\n\n"
            "🧑‍💻 : انجمن برنامه نویسی روباه در سروش پلاس\n"
            "ساخت و طراحی سایت و برنامه\n"
            "ساخت ربات های سروش پلاس\n"
            "برای دریافت خدمات و محصولات بیشتر\n"
            "از کانال زیر برنامه را نصب کنید\n"
            "https://splus.ir/Orderawebsite"
        )
        self.assertEqual(brand.FULL_TEXT, expected)

    def test_link_is_untouched(self):
        self.assertIn("https://splus.ir/Orderawebsite", brand.FULL_TEXT)
        self.assertEqual(brand.FULL_TEXT.count("https://splus.ir/Orderawebsite"), 1)
        # لینک در هیچ entityی قرار نمی‌گیرد (بدون تغییر می‌ماند)
        link_start = brand.FULL_TEXT.index(brand.LINK_LINE)
        for entity in brand.build_entities():
            end = entity.offset + entity.length
            self.assertFalse(
                entity.offset <= brand.utf16_offset(brand.FULL_TEXT, link_start) < end,
                "لینک نباید داخل entity باشد",
            )

    def test_quote_line_is_ascii_safe_after_normalization_not_needed(self):
        # خط تیتر دقیقاً همان چیزی است که خواسته شده (با حروف یونیکد بولد)
        self.assertEqual(brand.QUOTE_LINE, "🦊 ❂ | 𝗮𝗰𝗼𝗱 .𝗮𝗶 #plus")


class TestEntities(unittest.TestCase):
    def test_utf16_lengths_are_used_not_python_len(self):
        naive = len(brand.QUOTE_LINE)
        real = brand.utf16_len(brand.QUOTE_LINE)
        self.assertGreater(real, naive, "ایموجی‌ها/حروف یونیکد بیش از یک واحد UTF-16 هستند")

    def test_offsets_are_correct_in_utf16_space(self):
        text = brand.FULL_TEXT
        entities = brand.build_entities()
        units = text.encode("utf-16-le")

        for entity in entities:
            start = entity.offset * 2
            end = start + entity.length * 2
            decoded = units[start:end].decode("utf-16-le")
            if isinstance(entity, types.MessageEntityBlockquote):
                self.assertEqual(decoded, brand.QUOTE_LINE)
            elif isinstance(entity, types.MessageEntityBold):
                self.assertIn(decoded, brand.BODY_LINES)

    def test_entity_structure(self):
        entities = brand.build_entities()
        blockquotes = [e for e in entities if isinstance(e, types.MessageEntityBlockquote)]
        bolds = [e for e in entities if isinstance(e, types.MessageEntityBold)]

        self.assertEqual(len(blockquotes), 1)
        self.assertEqual(blockquotes[0].offset, 0)
        self.assertEqual(blockquotes[0].length, brand.utf16_len(brand.QUOTE_LINE))
        self.assertEqual(len(bolds), len(brand.BODY_LINES))
        # ترتیب صعودی آفست‌ها (استاندارد MTProto)
        offsets = [e.offset for e in entities]
        self.assertEqual(offsets, sorted(offsets))

    def test_matches_library_html_parser(self):
        """اعتبارسنجی متقابل: entityهای دستی باید با پارسر خودِ کتابخانه یکی باشند."""
        from splusthon.extensions.html import parse

        text, entities = parse(brand.build_html())
        self.assertEqual(text, brand.FULL_TEXT)

        manual = {
            (type(e).__name__, e.offset, e.length) for e in brand.build_entities()
        }
        parsed = {
            (type(e).__name__, e.offset, e.length) for e in entities
        }
        self.assertEqual(manual, parsed)

    def test_no_quote_line_variant(self):
        text = brand.build_text(include_quote_line=False)
        entities = brand.build_entities(text, quote_line=False, bold_body=True)
        self.assertNotIn(brand.QUOTE_LINE, text)
        self.assertTrue(all(isinstance(e, types.MessageEntityBold) for e in entities))
        self.assertEqual(len(entities), len(brand.BODY_LINES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
