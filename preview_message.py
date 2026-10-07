"""
پیش‌نمایش آفلاین پیام معرفی: متن دقیق + entityهای واقعی (بدون هیچ اتصالی).

    python preview_message.py

خروجی نشان می‌دهد کدام بخش‌ها Bold می‌شوند و خط تیتر داخل «نقل‌قول شیشه‌ای»
(MessageEntityBlockquote) قرار می‌گیرد.
"""

from __future__ import annotations

from splusthon import types

import brand

GREEN, BLUE, YELLOW, RESET = "\033[92m", "\033[94m", "\033[93m", "\033[0m"


def main() -> None:
    text = brand.FULL_TEXT
    units = text.encode("utf-16-le")

    print("=" * 70)
    print("متن دقیق پیام (همان چیزی که به سرور سروش ارسال می‌شود):")
    print("=" * 70)
    print(text)
    print("=" * 70)
    print(f"طول متن: {len(text)} کاراکتر پایتون | {brand.utf16_len(text)} واحد UTF-16")
    print("-" * 70)

    for entity in brand.build_entities():
        start, end = entity.offset * 2, (entity.offset + entity.length) * 2
        value = units[start:end].decode("utf-16-le")
        kind = type(entity).__name__
        if isinstance(entity, types.MessageEntityBlockquote):
            print(f"{BLUE}QUOTE {RESET} [{entity.offset}:{entity.length}]  «{value}»")
        elif isinstance(entity, types.MessageEntityBold):
            print(f"{YELLOW}BOLD  {RESET} [{entity.offset}:{entity.length}]  «{value}»")
        else:
            print(f"{GREEN}{kind}{RESET} [{entity.offset}:{entity.length}]  «{value}»")

    print("-" * 70)
    print("لینک ارسالی (بدون هیچ entity):", brand.LINK_LINE)
    print("متن HTML معادل (برای مسیر parse_mode='html'):")
    print(brand.build_html())


if __name__ == "__main__":
    main()
