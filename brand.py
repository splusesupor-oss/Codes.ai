"""
متن و «قالب‌بندی واقعی» پیام معرفی.

نکته‌ی مهم درباره‌ی فرمت‌ها — همه بر اساس TL Schema واقعی سروش پلاس (لایه ۱۸۲)
که داخل کتابخانه‌ی SPlusthon موجود است:

  * Bold  → سازنده‌ی واقعی MTProto:  messageEntityBold#bd610bc9 offset:int length:int
  * نقل‌قول (Quote / Glass Quote)
        → سازنده‌ی واقعی MTProto:  messageEntityBlockquote#20df5d0 offset:int length:int
        این همان چیزی است که کلاینت‌های سروش پلاس به‌شکل «قاب نقل‌قول» (شیشه‌ای) رندر می‌کنند.
  * حالت دوم نقل‌قول (اختیاری) → ریپلای واقعی + نقل‌قول متن:
        inputReplyToMessage#22c0f6d5 ... quote_text:flags.2?string quote_entities:flags.3?Vector<MessageEntity>

  ⚠️ offset/length در MTProto بر اساس «واحد کد UTF-16» حساب می‌شود، نه تعداد
     کاراکترهای پایتون. چون این پیام ایموجی‌های بیرون از BMP دارد (🦊 و 🧑‍💻)،
     محاسبه‌ی ساده‌ی len() غلط می‌شود و entity جابه‌جا می‌افتد. برای همین همه‌ی
     آفست‌ها با تابع utf16_len محاسبه شده‌اند.
"""

from __future__ import annotations

from typing import List

# ---------------------------------------------------------------------------
# متن دقیق پیام (هیچ کاراکتری نباید تغییر کند؛ لینک باید عیناً حفظ شود)
# ---------------------------------------------------------------------------
QUOTE_LINE = "🦊 ❂ | 𝗮𝗰𝗼𝗱 .𝗮𝗶 #plus"          # خط تیتر → نقل‌قول شیشه‌ای
BODY_LINES = [
    "🧑‍💻 : انجمن برنامه نویسی روباه در سروش پلاس",
    "ساخت و طراحی سایت و برنامه",
    "ساخت ربات های سروش پلاس",
    "برای دریافت خدمات و محصولات بیشتر",
    "از کانال زیر برنامه را نصب کنید",
]                                                # خطوط توضیحی → Bold
LINK_LINE = "https://splus.ir/Orderawebsite"     # لینک → دست‌نخورده (بدون تغییر)

SEPARATOR = "\n\n"
NL = "\n"


def build_text(include_quote_line: bool = True) -> str:
    """متن کامل پیام (با یا بدون خط تیتر)."""
    body = NL.join(BODY_LINES) + NL + LINK_LINE
    if not include_quote_line:
        return body
    return QUOTE_LINE + SEPARATOR + body


def build_html() -> str:
    """همان پیام به‌شکل HTML (برای مسیر parse_mode='html' کتابخانه)."""
    body = "".join(f"<b>{line}</b>{NL}" for line in BODY_LINES)
    return f"<blockquote>{QUOTE_LINE}</blockquote>{SEPARATOR}{body}{LINK_LINE}"


FULL_TEXT = build_text(True)


# ---------------------------------------------------------------------------
# ابزار محاسبه‌ی آفست بر اساس UTF-16
# ---------------------------------------------------------------------------
def utf16_len(text: str) -> int:
    """طول متن بر حسب واحد کد UTF-16 (استاندارد MTProto)."""
    return len(text.encode("utf-16-le")) // 2


def utf16_offset(full_text: str, char_index: int) -> int:
    """تبدیل اندیس کاراکتری پایتون به آفست UTF-16."""
    return utf16_len(full_text[:char_index])


# ---------------------------------------------------------------------------
# ساخت entityهای واقعی MTProto
# ---------------------------------------------------------------------------
def _entities():
    # ایمپورت تنبل تا ماژول متن در تست‌های بدون کتابخانه هم قابل استفاده باشد
    from splusthon.tl.types import MessageEntityBlockquote, MessageEntityBold

    return MessageEntityBlockquote, MessageEntityBold


def build_entities(
    text: str | None = None, *, quote_line: bool = True, bold_body: bool = True
) -> List[object]:
    """entityهای واقعی پیام: نقل‌قول (blockquote) برای خط اول + Bold برای خطوط توضیحی.

    ``text`` همان متنی است که ارسال می‌شود؛ اگر داده نشود، متن کامل در نظر گرفته
    می‌شود. آفست‌ها همیشه بر اساس UTF-16 و نسبت به همان متنِ ارسالی محاسبه می‌شوند.
    """
    MessageEntityBlockquote, MessageEntityBold = _entities()
    text = FULL_TEXT if text is None else text
    entities: List[object] = []

    if quote_line and text.startswith(QUOTE_LINE):
        entities.append(
            MessageEntityBlockquote(offset=0, length=utf16_len(QUOTE_LINE))
        )

    if bold_body:
        cursor = text.index(BODY_LINES[0])          # اندیس کاراکتری شروع بدنه
        for line in BODY_LINES:
            entities.append(
                MessageEntityBold(
                    offset=utf16_offset(text, cursor),
                    length=utf16_len(line),
                )
            )
            cursor += len(line) + 1                 # +1 برای کاراکتر \n
    # مرتب‌سازی بر اساس offset (استاندارد MTProto)
    entities.sort(key=lambda e: e.offset)
    return entities


def build_quote_entities() -> List[object]:
    """entityهای متنِ نقل‌شده در حالت «ریپلای + quote_text» (فقط برای خط تیتر)."""
    _, MessageEntityBold = _entities()
    return [MessageEntityBold(offset=0, length=utf16_len(QUOTE_LINE))]


# ---------------------------------------------------------------------------
# منوی پیام خصوصی و پاسخ دستورهای PV
# (فقط متن و قالب؛ خودِ منطق در core.py است)
# ---------------------------------------------------------------------------
MENU_TITLE = "برای انتخاب فقط دستورات زیر را ارسال کنید"
MENU_OPTIONS = [
    "سازنده",
    "کانال روباه",
    "ربات پشتیبانی",
    "سایت خرید",
    "کانال دانلود",
    "ربات روباه",
]
MENU_TEXT = MENU_TITLE + "\n\n" + "\n".join(MENU_OPTIONS)

PV_REPLIES = {
    "سازنده": "@osine2",
    "کانال روباه": "@ai_fox",
    "ربات پشتیبانی": "@Aifox_bot",
    "سایت خرید": "https://foxbot.osine2.workers.dev/",
    "کانال دانلود": "https://splus.ir/Orderawebsite",
    "ربات روباه": (
        "ربات های روباه"
        "\n\n"
        "نسخه یک🔹 @fox_bot\n"
        "نسخه دو🔹 @aifox\n"
        "نسخه سه🔹 @bot_fox"
    ),
}


# ---------------------------------------------------------------------------
# ابزار قالب‌بندی عمومی (همان نقل‌قول شیشه‌ای + Bold پروژه، برای هر متنی)
# ---------------------------------------------------------------------------
def _line_spans(text: str):
    """(خط، اندیس شروع، اندیس پایان) برای هر خط متن."""
    start = 0
    for line in text.split(NL):
        yield line, start, start + len(line)
        start += len(line) + 1


def quote_bold_entities(text: str, *, quote: bool = True, bold: bool = True) -> List[object]:
    """قالب استاندارد پروژه برای یک متن دلخواه:

    * کل متن داخل یک «نقل‌قول شیشه‌ای» (MessageEntityBlockquote)
    * هر خط غیرخالی، Bold (MessageEntityBold)

    آفست‌ها مثل بقیه‌ی پروژه بر حسب واحد UTF-16 محاسبه می‌شوند (ایموجی‌ها ایمن هستند).
    """
    if not text:
        return []
    MessageEntityBlockquote, MessageEntityBold = _entities()
    entities: List[object] = []

    if quote:
        entities.append(MessageEntityBlockquote(offset=0, length=utf16_len(text)))
    if bold:
        for line, start, _end in _line_spans(text):
            if line.strip():
                entities.append(
                    MessageEntityBold(
                        offset=utf16_offset(text, start), length=utf16_len(line)
                    )
                )
    entities.sort(key=lambda e: e.offset)
    return entities


def entity_summary(entities: List[object]) -> List[dict]:
    """خلاصه‌ی خوانا از entityها (برای لاگ و تست)."""
    return [
        {"type": type(e).__name__, "offset": e.offset, "length": e.length}
        for e in entities
    ]
