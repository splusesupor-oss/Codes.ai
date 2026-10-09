"""
متن و «قالب‌بندی واقعی» پیام معرفی و پیام‌های سیستمی ربات.

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

from typing import List, Tuple

# ---------------------------------------------------------------------------
# متن دقیق پیام معرفی
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
    from splusthon.tl.types import MessageEntityBlockquote, MessageEntityBold

    return MessageEntityBlockquote, MessageEntityBold


def build_entities(
    text: str | None = None, *, quote_line: bool = True, bold_body: bool = True
) -> List[object]:
    """entityهای واقعی پیام معرفی: نقل‌قول (blockquote) برای خط اول + Bold برای خطوط توضیحی."""
    MessageEntityBlockquote, MessageEntityBold = _entities()
    text = FULL_TEXT if text is None else text
    entities: List[object] = []

    if quote_line and text.startswith(QUOTE_LINE):
        entities.append(
            MessageEntityBlockquote(offset=0, length=utf16_len(QUOTE_LINE))
        )

    if bold_body:
        cursor = text.index(BODY_LINES[0])
        for line in BODY_LINES:
            entities.append(
                MessageEntityBold(
                    offset=utf16_offset(text, cursor),
                    length=utf16_len(line),
                )
            )
            cursor += len(line) + 1
    entities.sort(key=lambda e: e.offset)
    return entities


def build_quote_entities() -> List[object]:
    """entityهای متنِ نقل‌شده در حالت «ریپلای + quote_text» (فقط برای خط تیتر)."""
    _, MessageEntityBold = _entities()
    return [MessageEntityBold(offset=0, length=utf16_len(QUOTE_LINE))]


# ---------------------------------------------------------------------------
# منوی پیام خصوصی و پاسخ دستورهای PV
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

BOT_OFF_TEXT = "ربات خاموش شد. تا «ai cod» بعدی از مالک، به هیچ پیامی پاسخ داده نمی‌شود."
BOT_ON_TEXT = "ربات روشن شد."
BOT_EXPIRED_TEXT = "اعتبار استفاده از ربات به پایان رسیده است."

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
# ابزار قالب‌بندی عمومی
# ---------------------------------------------------------------------------
def _line_spans(text: str):
    """(خط، اندیس شروع، اندیس پایان) برای هر خط متن."""
    start = 0
    for line in text.split(NL):
        yield line, start, start + len(line)
        start += len(line) + 1


def quote_bold_entities(text: str, *, quote: bool = True, bold: bool = True) -> List[object]:
    """قالب استاندارد: کل متن داخل نقل‌قول شیشه‌ای + هر خط Bold."""
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
    return [
        {"type": type(e).__name__, "offset": e.offset, "length": e.length}
        for e in entities
    ]


# ---------------------------------------------------------------------------
# پیام‌های سیستم هوش مصنوعی و دستورات جدید
# ---------------------------------------------------------------------------
AI_SYSTEM_PROMPT = (
    "تو دستیار هوش مصنوعی هوشمند، مؤدب، آرام و مسلط انجمن برنامه‌نویسی روباه در سروش پلاس هستی. "
    "به زبان فارسی روان، طبیعی و دوستانه پاسخ بده. "
    "پاسخ‌هایت باید دقیق، متناسب با سطح پرسش و متمرکز بر اصل درخواست کاربر باشد. "
    "از تعارفات مکرر، کلیشه‌ها، مقدمه‌چینی‌های زائد و اظهارات رباتیک خودداری کن. "
    "اگر کاربر کدی خواست، کد تمیز همراه با توضیحات کوتاه و کاربردی ارائه کن. "
    "در صورت وجود ابهام، شفاف پاسخ بده و در صورت نیاز کوتاه راهنمایی کن."
)

AI_ENABLED_TEXT = "֍ 𝗢𝗡𝗟𝗜𝗡𝗘 { 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅} 🏕"
AI_DISABLED_TEXT = "֎ 𝗢𝗙𝗙𝗟𝗜𝗡𝗘 { 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅 } 🏜"
AI_ALLOWED_TEXT = "☰ 𝗔𝗜 𝗨𝗭𝗘𝗥 : 「 {user} 」\n๏ 𝗳𝗼𝘅 𝗮𝗶 𝗰𝗼𝗱𝗲 🍂"
AI_REVOKED_TEXT = "☰ 𝗢𝗙 𝗔𝗜  𝗨𝗭𝗘𝗥 : 「 {user} 」\n๏ 𝗳𝗼𝘅 𝗮𝗶 𝗰𝗼𝗱𝗲 🪴"
AI_NEED_REPLY_TEXT = "برای مجازکردن یا حذف دسترسی، باید روی پیام همان کاربر Reply کنید و دستور را بفرستید."
AI_USER_FALLBACK_PREFIX = "کاربر"
AI_DENIED_TEXT = "شما مجاز به صحبت کردن با هوش مصنوعی 𝖢𝖮︎𝖣︎𝖤︎𝖱︎  𝖠︎𝖨︎ نیستید برای صحبت بایدمالک به شما دسترسی بدهد 🦦🎊"
AI_QUOTA_TEXT = "سهمیه روزانه هوش مصنوعی به پایان رسیده است."
AI_QUEUE_BUSY_TEXT = "⚠️ در حال حاضر صف درخواست‌های هوش مصنوعی این گروه تکمیل است. لطفاً چند لحظه دیگر دوباره تلاش کنید."

# دستور «ai»
AI_CALL_RESPONSE = "جانم 👾"

# سقف اعضای مجاز
AI_MAX_USERS_REACHED_TEXT = "⚠️ سقف اعضای مجاز هوش مصنوعی برای این گروه ({limit} عضو) تکمیل شده است."

# ثبت مالک ربات
AI_REGISTERED_OWNER_TEXT = "☰ 𝗥𝗘𝗚𝗜𝗦𝗧𝗘𝗥𝗘𝗗 𝗢𝗪𝗡𝗘𝗥 : 「 {user} 」\n๏ 𝗳𝗼𝘅 𝗮𝗶 𝗰𝗼𝗱𝗲 🍁"
AI_NEED_REPLY_REG_OWNER_TEXT = "برای ثبت مالک ربات، باید روی پیام همان کاربر Reply کنید و دستور را بفرستید."

# خطاهای فنی
AI_ERROR_TEXT = "⚠️ خطا در ارتباط با هوش مصنوعی. لطفاً کمی بعد دوباره تلاش کنید."
AI_CONFIG_ERROR_TEXT = "⚠️ هوش مصنوعی تنظیم نشده است (CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN)."


def format_quota_ceiling(quota: int, members: int) -> str:
    """قالب پیکربندی سهمیه و اعضای مجاز طبق مشخصات فنی."""
    return f"𝗤𝗨𝗢𝗧𝗔 𝗖𝗘𝗜𝗟𝗜𝗡𝗚 : 「{quota}」📥\n\n𝗡𝗨𝗠𝗕𝗘𝗥 : 「{members}」"


def format_remaining_quota(remaining: int, ceiling: int, used: int) -> str:
    """قالب نمایش باقیمانده سهمیه (ai plun) طبق مشخصات فنی."""
    return (
        f"📊 سهمیه هوش مصنوعی این گروه:\n"
        f"𝗤𝗨𝗢𝗧𝗔 𝗖𝗘𝗜𝗟𝗜𝗡𝗚 : 「{remaining}」📥\n\n"
        f"• کل سهمیه روزانه: {ceiling}\n"
        f"• مصرف شده: {used}\n"
        f"• باقی‌مانده: {remaining}\n"
        f"• زمان ریست: 00:00 به وقت تهران"
    )


def format_ai_l_list(user_labels: List[str]) -> str:
    """قالب نمایش اعضای مجاز هوش مصنوعی (ai L) طبق مشخصات فنی با رعایت فاصله در براکت."""
    if not user_labels:
        return "هنوز کاربری برای هوش مصنوعی در این گروه مجاز نشده است."
    lines = ["᳆ 𝗔𝗜 𝗟𝗜𝗦𝗧 𝗟", ""]
    for label in user_labels:
        clean = label.strip()
        lines.append(f"❥「 {clean} 」")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# پیام راهنما (راهنما)
# ---------------------------------------------------------------------------
HELP_TITLE = "🔸 تمام دستورات به صورت انگلیسی هست"

HELP_ITEMS: List[Tuple[str, str]] = [
    ("مجاز کردن یک کاربر بنویسید:", "ai list"),
    ("برای لغو یک کاربر بنویسید:", "ai list x"),
    ("برای خاموش کردن هوش مصنوعی:", "ai of"),
    ("برای روشن کردن هوش مصنوعی:", "ai online"),
    ("برای دیدن لیست مجازهای هوش مصنوعی:", "ai L"),
    ("برای دیدن سهمیه باقی‌مانده:", "ai plun"),
    ("برای صدا زدن ربات:", "ai"),
]


def build_help_message() -> Tuple[str, List[object]]:
    """ساخت متن و entityهای پیام راهنما مطابق دقیق فرمت مشخصات فنی:

    * خط تیتر «🔸 تمام دستورات به صورت انگلیسی هست» داخل نقل‌قول شیشه‌ای (Blockquote)
    * هر خط توضیحی فارسی به‌صورت Bold
    * هر دستور انگلیسی داخل نقل‌قول شیشه‌ای (Blockquote)
    """
    MessageEntityBlockquote, MessageEntityBold = _entities()
    lines = [HELP_TITLE, ""]
    for desc, cmd in HELP_ITEMS:
        lines.append(desc)
        lines.append(cmd)
        lines.append("")
    if lines[-1] == "":
        lines.pop()

    full_text = NL.join(lines)
    entities: List[object] = []
    cursor = 0

    for line in lines:
        if line == HELP_TITLE:
            entities.append(
                MessageEntityBlockquote(
                    offset=utf16_offset(full_text, cursor),
                    length=utf16_len(line),
                )
            )
        elif any(line == cmd for _, cmd in HELP_ITEMS):
            entities.append(
                MessageEntityBlockquote(
                    offset=utf16_offset(full_text, cursor),
                    length=utf16_len(line),
                )
            )
        elif any(line == desc for desc, _ in HELP_ITEMS):
            entities.append(
                MessageEntityBold(
                    offset=utf16_offset(full_text, cursor),
                    length=utf16_len(line),
                )
            )
        cursor += len(line) + 1

    entities.sort(key=lambda e: e.offset)
    return full_text, entities


# ---------------------------------------------------------------------------
# پیام اعلام فعال‌سازی گروه (ai x cod)
# ---------------------------------------------------------------------------
def build_announcement_message(
    group_name: str,
    owner_name: str,
    admin_names: List[str],
    *,
    activation_date: Optional[str] = None,
    expiration_date: Optional[str] = None,
) -> Tuple[str, List[object]]:
    """ساخت پیام اعلام فعال‌سازی گروه با دستور «ai x cod».

    قالب دقیق:
      #𝗮𝗶 𝗰𝗼𝗱𝗲 𝗽𝗹𝘂𝘀 𝟓

      ☲ 𝖦𝖱︎𝖮︎𝖯︎ 「GROUP_NAME」

      ☲ 𝖮︎𝖶︎𝖤︎𝖱︎
      ๏ GROUP_OWNER

      ☲ 𝖠︎𝖣︎𝖬𝖨︎𝖭
      ๏ ADMIN_1
      ๏ ADMIN_2

      (اختیاری: زمان‌های فعال‌سازی و انقضا)
      برای آشنایی با هوش مصنوعی کلمه راهنما را بفرستید
    """
    MessageEntityBlockquote, MessageEntityBold = _entities()
    instruction = "برای آشنایی با هوش مصنوعی کلمه راهنما را بفرستید"

    lines = [
        "#𝗮𝗶 𝗰𝗼𝗱𝗲 𝗽𝗹𝘂𝘀 𝟓",
        "",
        f"☲ 𝖦𝖱︎𝖮︎𝖯︎ 「{group_name}」",
        "",
        "☲ 𝖮︎𝖶︎𝖤︎𝖱︎",
        f"๏ {owner_name}",
        "",
        "☲ 𝖠︎𝖣︎𝖬𝖨︎𝖭",
    ]
    if admin_names:
        for a in admin_names:
            lines.append(f"๏ {a}")
    else:
        lines.append("๏ مدیری یافت نشد")

    if activation_date:
        lines.extend([
            "",
            "𝗔𝗰𝘁𝗶𝘃𝗮𝘁𝗶𝗼𝗻 𝗗𝗮𝘁𝗲⏱",
            f"꧇◖ {activation_date}",
        ])
    if expiration_date is not None:
        lines.extend([
            "",
            "𝗘𝘅𝗽𝗶𝗿𝗮𝘁𝗶𝗼𝗻 𝗱𝗮𝘁𝗲⏱",
            f"꧇◖ {expiration_date or 'تنظیم نشده (نامحدود)'}",
        ])

    lines.append("")
    lines.append(instruction)

    full_text = NL.join(lines)
    instr_char_idx = len(full_text) - len(instruction)
    instr_offset = utf16_offset(full_text, instr_char_idx)
    instr_len = utf16_len(instruction)

    entities = [
        MessageEntityBlockquote(offset=instr_offset, length=instr_len),
        MessageEntityBold(offset=instr_offset, length=instr_len),
    ]
    entities.sort(key=lambda e: e.offset)
    return full_text, entities


# ---------------------------------------------------------------------------
# ابزار متن مشترک: تکه‌تکه‌کردن خطوط برای رعایت سقف طول پیام
# ---------------------------------------------------------------------------
def chunk_lines(lines, max_chars: int):
    chunks = []
    current = []
    current_len = 0

    for line in lines:
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > max_chars:
            chunks.append("\n".join(current))
            current, current_len = [], 0
            extra = len(line)
        current.append(line)
        current_len += extra

    if current:
        chunks.append("\n".join(current))
    return chunks
