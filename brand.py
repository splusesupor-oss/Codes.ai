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

import datetime as _dt
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Union

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
    "تو «روباه» (acod fox) هستی؛ دستیار هوش مصنوعی فارسی روان، باهوش، منطقی و خوش‌صحبت در سروش پلاس.\n\n"
    "اصول رفتاری و قابلیت‌ها:\n"
    "۱) فارسی روان و طبیعی: با ادبیات شیوا، محترمانه و به دور از کلیشه‌ها، تعارفات زائد یا ترجمه خشک صحبت کن.\n"
    "۲) فهم اصطلاحات عامیانه و پاسخ متین: اصطلاحات عامیانه (مثل «دمت گرم» یا «سیکتیر») را درک کن و با متانت پاسخ بده؛ از توجیهات فنی یا معرفی آنها به عنوان یک نرم‌افزار ساختگی خودداری کن.\n"
    "۳) پرهیز از توهم: هرگز پاسخ ساختگی یا توهم نده؛ اگر موضوعی را نمی‌دانی بگو نمی‌دانم.\n"
    "۴) پاسخ مستقیم انسانی: مستقیماً جواب بده و از تولید افکار درونی، مونولوگ ذهنی یا تگ‌های <think> پرهیز کن.\n"
    "۵) تحلیل کاربران و Display Name: با تحلیل هوشمندانه رفتار، لحن و نام نمایشی (Display Name) نظر بده.\n"
    "۶) ایده‌پردازی برای رشد و بهبود گروه: پیشنهادات جذاب، چالش‌ها و ایده‌های کاربردی برای رشد و بهبود گروه ارائه بده.\n"
    "۷) ساخت انواع فونت‌های زیبای انگلیسی: تبدیل متن انگلیسی به زیباترین فونت و استایل‌های یونیکد متنوع برای کپی آسان.\n"
    "۸) ترجمه دقیق و کدنویسی کامل: ترجمه روان متون و کدنویسی کامل، حرفه‌ای، بهینه، تمیز و بدون باگ.\n"
    "۹) نگارش پرامپت قوی برای ساخت عکس: نوشتن پرامپت انگلیسی پرجزئیات و حرفه‌ای برای ساخت عکس همراه با توضیح مفهوم به زبان فارسی.\n"
    "۱۰) پیوستگی گفت‌وگو بدون تکرار یا گیر کردن روی سوال قبلی: حافظه گفت‌وگو تا ۵۰ دقیقه حفظ می‌شود، اما برای هر سوال جدید دقیقاً به همان موضوع پاسخ بده و جواب قبل را تکرار نکن (مثلاً اگر بعد از «الکل» پرسید «شیشه چیه»، اختصاصی شیشه را توضیح بده در حالی که پیوستگی مکالمه حفظ است).\n"
    "۱۱) تکلم ۱۰۰٪ به زبان فارسی درست و بدون حروف چینی یا انگلیسی نامرتبط: اکیداً و مطلقاً هیچ حرف یا کاراکتر چینی/شرقی (مانند 剛才) استفاده نکن و پاسخ را به فارسی صحیح و خوانا بنویس.\n"
    "۱۲) درک عمیق پیام‌های ریپلای‌شده و تشخیص مخاطب: اگر کاربر روی پیامی ریپلای کرد و گفت «این کاربر چی گفت»، «منظورش چیه» یا «اینو میگم»، مقصود او پیام ریپلای‌شده و فرستنده آن است، نه خود کاربر سوال‌کننده."
)


def clean_model_output(text: str) -> str:
    """پاکسازی تگ‌های تفکر مدل‌های استدلالی مانند DeepSeek-R1 (<think>...</think>)، کاراکترهای چینی/شرقی و متن‌های پردازشی داخلی."""
    if not text:
        return ""
    # ۱) حذف کامل بلوک‌های بسته شده <think>...</think> یا <thought>...</thought>
    cleaned = re.sub(r"(?is)<\s*(?:think|thought)\s*>.*?<\s*/\s*(?:think|thought)\s*>", "", text).strip()

    # ۲) اگر هنوز تگ باز یا بسته در متن باقی مانده باشد:
    if re.search(r"(?is)<\s*(?:think|thought)\s*>", cleaned) or re.search(r"(?is)<\s*/\s*(?:think|thought)\s*>", cleaned):
        parts = re.split(r"(?is)<\s*/\s*(?:think|thought)\s*>", cleaned)
        if len(parts) > 1 and parts[-1].strip():
            cleaned = parts[-1].strip()
        else:
            # اگر هیچ پاسخی بعد از تفکر نبوده (فقط مونولوگ ناقص داخل think بوده)
            cleaned = re.sub(r"(?is)^\s*<\s*(?:think|thought)\s*>", "", cleaned).strip()

    # ۳) حذف مونوگ‌های فکری احتمالی در آغاز پاسخ («خب، اول باید...»)
    cleaned = re.sub(r"(?is)^خب،?\s*اول\s*باید\s*(?:بفهمم|بدانم|بررسی\s*کنم).*?(?:\n\n|\n|$)", "", cleaned).strip()

    # ۴) حذف کامل هرگونه حروف، کاراکترها و نشانه‌های چینی، ژاپنی، کره‌ای و خطوط شرقی (مانند 剛才 و غیره)
    cleaned = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+", "", cleaned)

    return cleaned.strip()

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

HELP_BLOCKS: List[Tuple[str, str]] = [
    ("مجاز کردن یک کاربر بنویسید:", "ai list"),
    ("برای لغو یک کاربر بنویسید:", "ai list x"),
    ("برای خاموش کردن هوش مصنوعی:", "ai of"),
    ("برای روشن کردن هوش مصنوعی:", "ai online"),
    ("برای دیدن لیست مجازهای هوش مصنوعی:", "ai L"),
    ("برای دیدن سهمیه باقی‌مانده:", "ai plun"),
    ("برای صدا زدن ربات:", "ai"),
    ("برای فیلتر کردن کلمات تبلیغاتی:", "Flter بعد پیام رو بنویسید\nFlter بیو چک"),
    ("برای دیدن لیست فیلتر ها:", "list flter"),
    ("برای برداشتن جمله از فیلتر ها:", "x بعد جمله رو بنویسید\nx بیوچک"),
]


def build_help_message() -> Tuple[str, List[object]]:
    """ساخت متن و entityهای پیام راهنما:

    * خط تیتر «🔸 تمام دستورات به صورت انگلیسی هست» داخل نقل‌قول شیشه‌ای (Blockquote)
    * هر خط توضیحی فارسی به‌صورت Bold
    * هر بلوک دستور انگلیسی داخل نقل‌قول شیشه‌ای (Blockquote) با یکپارچگی خطوط نمونه
    """
    MessageEntityBlockquote, MessageEntityBold = _entities()

    sections = [HELP_TITLE]
    for desc, cmd in HELP_BLOCKS:
        sections.append(f"{desc}\n{cmd}")

    full_text = "\n\n".join(sections)
    entities: List[object] = []
    cursor = 0

    # ۱) تیتر راهنما
    entities.append(
        MessageEntityBlockquote(
            offset=utf16_offset(full_text, cursor),
            length=utf16_len(HELP_TITLE),
        )
    )
    cursor += len(HELP_TITLE) + 2

    # ۲) هر بخش دستور
    for desc, cmd in HELP_BLOCKS:
        desc_start = cursor
        entities.append(
            MessageEntityBold(
                offset=utf16_offset(full_text, desc_start),
                length=utf16_len(desc),
            )
        )

        cmd_start = desc_start + len(desc) + 1
        entities.append(
            MessageEntityBlockquote(
                offset=utf16_offset(full_text, cmd_start),
                length=utf16_len(cmd),
            )
        )

        cursor = cmd_start + len(cmd) + 2

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


# ---------------------------------------------------------------------------
# قالب‌بندی بلاک‌های کد بومی (Native Code-Blocks) برای پیام‌های هوش مصنوعی
# ---------------------------------------------------------------------------
LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "shell": "bash",
    "zsh": "bash",
    "yml": "yaml",
    "md": "markdown",
    "cs": "csharp",
    "c++": "cpp",
    "htm": "html",
    "golang": "go",
    "rb": "ruby",
    "rs": "rust",
}


def normalize_code_language(lang: str) -> str:
    """استانداردسازی شناسه زبان برنامه‌نویسی برای syntax highlighting و نشانگر زبان."""
    cleaned = (lang or "").strip().lower()
    return LANGUAGE_ALIASES.get(cleaned, cleaned)


@dataclass
class CodeSegment:
    """بخش‌های مجزای متن هوش مصنوعی: متن عادی یا بلاک کد با زبان مشخص."""
    content: str
    is_code: bool = False
    language: str = ""


def parse_code_segments(text: str) -> list[CodeSegment]:
    """تفکیک دقیق متن پاسخ هوش مصنوعی به قطعات متن معمولی و بلاک‌های کد حصاردار (Fenced Code Blocks)."""
    if not text:
        return []
    segments: list[CodeSegment] = []
    pat = re.compile(r"```([a-zA-Z0-9_+#.-]*)[ \t]*\r?\n([\s\S]*?)\r?\n?```")
    last_idx = 0
    for m in pat.finditer(text):
        start, end = m.span()
        if start > last_idx:
            pre_text = text[last_idx:start]
            if pre_text:
                segments.append(CodeSegment(content=pre_text, is_code=False))
        lang = normalize_code_language(m.group(1))
        code = m.group(2)
        segments.append(CodeSegment(content=code, is_code=True, language=lang))
        last_idx = end

    if last_idx < len(text):
        remaining = text[last_idx:]
        unclosed_match = re.search(r"```([a-zA-Z0-9_+#.-]*)[ \t]*\r?\n([\s\S]*)$", remaining)
        if unclosed_match:
            u_start, _ = unclosed_match.span()
            if u_start > 0:
                segments.append(CodeSegment(content=remaining[:u_start], is_code=False))
            lang = normalize_code_language(unclosed_match.group(1))
            code = unclosed_match.group(2)
            segments.append(CodeSegment(content=code, is_code=True, language=lang))
        elif remaining:
            segments.append(CodeSegment(content=remaining, is_code=False))
    return segments


def split_large_segment(seg: CodeSegment, max_chars: int) -> list[CodeSegment]:
    """تقسیم قطعات بزرگتر از سقف طول پیام با حفظ پیوستگی کد و خطوط."""
    if utf16_len(seg.content) <= max_chars:
        return [seg]
    lines = seg.content.splitlines(keepends=True)
    result: list[CodeSegment] = []
    curr_content = []
    curr_len = 0
    for line in lines:
        line_len = utf16_len(line)
        if curr_content and curr_len + line_len > max_chars:
            result.append(CodeSegment(content="".join(curr_content), is_code=seg.is_code, language=seg.language))
            curr_content = []
            curr_len = 0
        if line_len > max_chars:
            if curr_content:
                result.append(CodeSegment(content="".join(curr_content), is_code=seg.is_code, language=seg.language))
                curr_content = []
                curr_len = 0
            for i in range(0, len(line), max_chars):
                result.append(CodeSegment(content=line[i:i + max_chars], is_code=seg.is_code, language=seg.language))
            continue
        curr_content.append(line)
        curr_len += line_len
    if curr_content:
        result.append(CodeSegment(content="".join(curr_content), is_code=seg.is_code, language=seg.language))
    return result


def build_chunk_message(chunk_segments: list[CodeSegment]) -> tuple[str, list[object]]:
    """تولید متن تمیز پیام و entityهای MessageEntityPre متناظر با آفست و طول دقیق UTF-16."""
    from splusthon.tl.types import MessageEntityPre

    chunk_text_parts: list[str] = []
    entities: list[object] = []

    for seg in chunk_segments:
        if not seg.content:
            continue
        start_char_idx = len("".join(chunk_text_parts))
        chunk_text_parts.append(seg.content)
        if seg.is_code:
            current_full = "".join(chunk_text_parts)
            off = utf16_offset(current_full, start_char_idx)
            length = utf16_len(seg.content)
            if length > 0:
                entities.append(MessageEntityPre(offset=off, length=length, language=seg.language or ""))

    full_text = "".join(chunk_text_parts)
    return full_text, entities


def format_ai_response_chunks(text: str, max_chars: int = 3500) -> list[tuple[str, list[object]]]:
    """فرمت‌بندی کامل پاسخ هوش مصنوعی و تبدیل بلاک‌های کد مارک‌داون به بلاک‌های بومی Soroush Plus / MTProto."""
    if not text:
        return []
    if "```" not in text:
        return [(c, []) for c in chunk_lines(text.split("\n"), max_chars)]

    raw_segments = parse_code_segments(text)
    if not any(s.is_code for s in raw_segments):
        return [(c, []) for c in chunk_lines(text.split("\n"), max_chars)]

    refined_segments: list[CodeSegment] = []
    for s in raw_segments:
        refined_segments.extend(split_large_segment(s, max_chars))

    chunks_of_segments: list[list[CodeSegment]] = []
    current_chunk: list[CodeSegment] = []
    current_chunk_len = 0

    for s in refined_segments:
        s_len = utf16_len(s.content)
        if current_chunk and current_chunk_len + s_len > max_chars:
            chunks_of_segments.append(current_chunk)
            current_chunk = []
            current_chunk_len = 0
        current_chunk.append(s)
        current_chunk_len += s_len

    if current_chunk:
        chunks_of_segments.append(current_chunk)

    result = []
    for c_segs in chunks_of_segments:
        msg_text, ents = build_chunk_message(c_segs)
        if msg_text:
            result.append((msg_text, ents))
    return result


def format_ai_usage_report(summary: dict) -> str:
    """قالب‌بندی گزارش جامع سهمیه واقعی Cloudflare (۱۰٬۰۰۰ نورون) و تحلیل هزینه هر ۳ مدل."""
    rem = summary.get("remaining_neurons", 10000.0)
    con = summary.get("consumed_neurons", 0.0)
    rem_p = summary.get("percent_remaining", 100.0)
    con_p = summary.get("percent_consumed", 0.0)
    p_tok = summary.get("total_prompt_tokens", 0)
    c_tok = summary.get("total_completion_tokens", 0)
    reqs = summary.get("total_requests", 0)

    lines = [
        "📊 گزارش سهمیه واقعی Cloudflare Workers AI و تحلیل مصرف",
        "",
        "🔋 وضعیت سهمیه رایگان امروز (۱۰,۰۰۰ نورون):",
        f"• سهمیه باقیمانده: {rem:,.1f} نورون ({rem_p:.1f}٪)",
        f"• سهمیه مصرف‌شده: {con:,.1f} نورون ({con_p:.1f}٪)",
        f"• تعداد کل درخواست‌های امروز: {reqs:,}",
        f"• کل توکن‌های مصرفی امروز: ورودی {p_tok:,} | خروجی {c_tok:,}",
        "• چرخه ریست روزانه سهمیه: ساعت ۰۰:۰۰ UTC (۰۳:۳۰ بامداد به وقت ایران)",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "💰 تحلیل هزینه هر ۳ مدل در صورت نگارش ۱۵۰ جمله:",
        "",
        "۱️⃣ مدل ۱ — دستیار سریع و عمومی (Llama 3.1 8B):",
        "  • در یک پاسخ جامع: حدود ۱۲۸ نورون (۱.۳٪ از کل سهمیه روزانه)",
        "  • در ۱۵۰ پیام مجزا: حدود ۶۲۲ نورون (۶.۲٪ از کل سهمیه روزانه)",
        "  • توان نگارش با سهمیه روزانه: بیش از ۱۲,۰۰۰ جمله در روز با سهمیه رایگان",
        "",
        "۲️⃣ مدل ۲ — دستیار استدلالی متعادل (Llama 3.3 70B):",
        "  • در یک پاسخ جامع: حدود ۷۵۷ نورون (۷.۶٪ از کل سهمیه روزانه)",
        "  • در ۱۵۰ پیام مجزا: حدود ۳,۹۶۵ نورون (۳۹.۶٪ از کل سهمیه روزانه)",
        "  • توان نگارش با سهمیه روزانه: حدود ۲,۰۰۰ جمله در روز با سهمیه رایگان",
        "",
        "۳️⃣ مدل ۳ — پیشرفته استدلال و کدنویسی (Qwen 2.5 Coder 32B):",
        "  • در یک پاسخ جامع: حدود ۴۰۸ نورون (۴.۱٪ از کل سهمیه روزانه)",
        "  • در ۱۵۰ پیام مجزا: حدود ۷,۵۴۱ نورون (۷۵.۴٪ از کل سهمیه روزانه)",
        "  • توان نگارش با سهمیه روزانه: حدود ۳,۵۰۰ جمله در روز با سهمیه رایگان",
        "",
        "💡 نکته راهنما:",
        "مدل ۱ به دلیل سبک‌بودن (8B) کمترین مصرف را دارد. مدل ۲ و ۳ به دلیل پارامترهای سنگین (70B و 32B)، در صورت مکالمات چندمرحله‌ای سهمیه بیشتری مصرف می‌کنند.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# تبدیل تقویم میلادی به هجری شمسی (جلالی)
# ---------------------------------------------------------------------------
TEHRAN_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30), name="Asia/Tehran")


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> Tuple[int, int, int]:
    """تبدیل تاریخ میلادی به هجری شمسی (الگوریتم تقویم جلالی)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy if gm > 2 else gy - 1
    days = (365 * gy) + ((gy2 + 4) // 4) - ((gy2 + 100) // 100) + ((gy2 + 400) // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def format_jalali_date(dt: Optional[_dt.datetime], include_time: bool = True) -> str:
    """تبدیل شیء datetime به فرمت تاریخ هجری شمسی (YYYY/M/D HH:MM:SS) به وقت تهران."""
    if dt is None:
        return "تنظیم نشده (نامحدود)"
    if dt.tzinfo is not None:
        dt = dt.astimezone(TEHRAN_TZ)
    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    if include_time:
        time_part = dt.strftime("%H:%M:%S")
        return f"{jy}/{jm}/{jd} {time_part}"
    return f"{jy}/{jm}/{jd}"


def parse_and_format_jalali(val: Union[_dt.datetime, str, None], include_time: bool = True) -> str:
    """پارس امن ورودی و قالب‌بندی به تاریخ هجری شمسی به وقت تهران."""
    if not val:
        return "تنظیم نشده (نامحدود)"
    if isinstance(val, _dt.datetime):
        return format_jalali_date(val, include_time=include_time)
    val_clean = str(val).strip()
    try:
        dt = _dt.datetime.fromisoformat(val_clean)
        return format_jalali_date(dt, include_time=include_time)
    except Exception:
        pass
    try:
        dt = _dt.datetime.strptime(val_clean, "%Y-%m-%d %H:%M:%S")
        return format_jalali_date(dt, include_time=include_time)
    except Exception:
        return str(val)


# ---------------------------------------------------------------------------
# فیلتر کلمات تبلیغاتی و هرزنامه با الگوی ضد دور زدن (Anti-Bypass)
# ---------------------------------------------------------------------------
CHAR_EQUIVALENTS: dict[str, str] = {
    "ک": "[کك]",
    "ك": "[کك]",
    "ی": "[یيىئ]",
    "ي": "[یيىئ]",
    "ى": "[یيىئ]",
    "ا": "[اآأإ]",
    "آ": "[اآأإ]",
    "أ": "[اآأإ]",
    "إ": "[اآأإ]",
    "ه": "[هة]",
    "ة": "[هة]",
    "و": "[وؤ]",
    "ؤ": "[وؤ]",
}

FILTER_SEPARATORS: str = r"[\s\u200c\u200d\u200e\u200f\u0640\.\/\#\,\-\_\٫\*\~\!\@\|\:\;\^\\\(\)\[\]\{\}\+\=\?\>\<]*"


def build_filter_pattern(word: str) -> re.Pattern:
    r"""ساخت الگوی رگولار ضد دور زدن برای شناسایی کلمات فیلتر/تبلیغات.

    مقاوم در برابر:
    - کشیدگی حروف (تطویل/ـ)
    - علائم و نقطه‌گذاری (. / # ٫ - _ * ~ ! @ | : ; \ و غیره)
    - فاصله‌ها و کاراکترهای با عرض صفر (نیم‌فاصله \u200c و غیره)
    - تکرار مکرر حروف (ککککاااانال)
    - حروف مشابه عربی/فارسی (ک/ك، ی/ي، آ/ا، ه/ة)
    """
    clean_word = re.sub(
        r"[\s\u200c\u200d\u200e\u200f\u0640\.\/\#\,\-\_\٫\*\~\!\@\|\:\;\^\\\(\)\[\]\{\}\+\=\?\>\<]",
        "",
        word,
    )
    if not clean_word:
        return re.compile(re.escape(word), re.IGNORECASE)

    parts: list[str] = []
    for ch in clean_word:
        equiv = CHAR_EQUIVALENTS.get(ch, re.escape(ch))
        parts.append(rf"(?:{equiv}[\u0640]*)+")

    pattern_str = FILTER_SEPARATORS.join(parts)
    full_pattern = rf"(?<![\u0600-\u06FF\w]){pattern_str}(?![\u0600-\u06FF\w])"
    return re.compile(full_pattern, re.IGNORECASE)


def contains_filtered_word(text: str, filtered_words: list[str]) -> Optional[str]:
    """بررسی اینکه آیا متن شامل کلمه‌ای از لیست فیلتر هست یا خیر.

    در صورت یافتن، اولین کلمه مطابقت یافته را برمی‌گرداند؛ در غیر این صورت None.
    """
    if not text or not filtered_words:
        return None
    for word in filtered_words:
        w = word.strip()
        if not w:
            continue
        pat = build_filter_pattern(w)
        if pat.search(text):
            return w
    return None


def build_ad_warning_message(user_label: str) -> tuple[str, list[object]]:
    """ساخت پیام هشدار ارسال تبلیغات طبق مشخصات کاربر:

    خط ۱: «⚠️ کاربر  : « {user} »» در قالب Bold و نقل‌قول شیشه‌ای (Blockquote).
    خط ۲: «در حال ارسال کلمات تبلیغاتی و هرزنامه هست ؛  رفتار نامناسب و ضد قوانین گروه میتوانید اخطار یا هشدار بدهید» در قالب Bold.
    """
    MessageEntityBlockquote, MessageEntityBold = _entities()

    line1 = f"⚠️ کاربر  : « {user_label} »"
    line2 = "در حال ارسال کلمات تبلیغاتی و هرزنامه هست ؛  رفتار نامناسب و ضد قوانین گروه میتوانید اخطار یا هشدار بدهید"
    full_text = f"{line1}\n\n{line2}"

    l1_len = utf16_len(line1)
    l2_char_idx = len(line1) + 2
    l2_off = utf16_offset(full_text, l2_char_idx)
    l2_len = utf16_len(line2)

    entities = [
        MessageEntityBlockquote(offset=0, length=l1_len),
        MessageEntityBold(offset=0, length=l1_len),
        MessageEntityBold(offset=l2_off, length=l2_len),
    ]
    return full_text, entities


def build_filter_list_message(words: list[str]) -> tuple[str, list[object]]:
    """ساخت پیام لیست کلمات فیلتر شده با قالب‌بندی اسپویلر (MessageEntitySpoiler)."""
    from splusthon.tl.types import MessageEntitySpoiler

    if not words:
        return "📋 لیست کلمات فیلتر شده این گروه خالی است.", []

    header = "📋 لیست کلمات فیلتر شده این گروه:\n\n"
    lines = [header]
    spoiler_spans: list[tuple[int, int]] = []
    current_char_len = len(header)
    for idx, word in enumerate(words, 1):
        prefix = f"{idx}. "
        line = f"{prefix}{word}\n"
        word_start = current_char_len + len(prefix)
        word_char_len = len(word)
        spoiler_spans.append((word_start, word_char_len))
        current_char_len += len(line)
        lines.append(line)

    full_text = "".join(lines).rstrip("\n")
    entities = []
    for start_char, char_len in spoiler_spans:
        off = utf16_offset(full_text, start_char)
        sub = full_text[start_char : start_char + char_len]
        l = utf16_len(sub)
        entities.append(MessageEntitySpoiler(offset=off, length=l))

    return full_text, entities

