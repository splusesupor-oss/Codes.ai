"""
تنظیمات پروژه «ربات ai cod» برای سروش پلاس.

این پروژه هیچ Bot Token و هیچ اتصالی به Telegram Bot API ندارد.
اتصال، از طریق کتابخانه‌ی واقعی SPlusthon انجام می‌شود که خودش یک کلاینت
MTProto (فورک Telethon) برای سروش پلاس است و با WebSocket به سرور سروش
(wss://im-server.splus.ir:443/apiws) وصل می‌شود و با «حساب کاربری» لاگین می‌کند.

مقادیر api_id / api_hash زیر «توکن ربات» نیستند؛ اینها مقادیر عمومی کلاینت
وبِ خودِ سروش پلاس هستند که به‌صورت پیش‌فرض داخل SPlusthon هم قرار دارند
(فایل splusthon/client/telegrambaseclient.py) و برای انجام هندشیک MTProto
لازم‌اند. چون پروژه Bot Token ندارد، تنها راه اتصال همین است.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("ACOD_DATA_DIR", BASE_DIR / "data"))

# ---------------------------------------------------------------------------
# دستورات
# ---------------------------------------------------------------------------
OWNER_COMMAND = "ai cod"      # دستور فعال‌سازی مالک سراسری (اولین کاربر = مالک دائمی)
KODREZ_COMMAND = "کدرز"       # دستور معرفی؛ برای همه آزاد است

# ---------------------------------------------------------------------------
# پرچم‌های رفتاری
# ---------------------------------------------------------------------------
# «اولین کاربری که در هر گروهی این دستور را ارسال کند» → پیش‌فرض: فقط گروه‌ها
# توجه: این پرچم فقط روی «دستورها» (ai cod و کدرز) اثر دارد.
# مسیر پیام خصوصی (PV) از این پرچم مستقل است و با PRIVATE_AUTO_REPLY کنترل می‌شود.
GROUPS_ONLY = True

# پیام خصوصی: هر PV ورودی (بدون نیاز به دستور) → پاسخ با همان پیام معرفی
PRIVATE_AUTO_REPLY = True

# پیش‌نمایش لینک برای پیام برند نمایش داده شود؟
LINK_PREVIEW = False

# حالت ارسال «نقل قول شیشه‌ای»:
#   "entity"  → نقل‌قول به‌صورت entity واقعی سروش/تلگرام (MessageEntityBlockquote)
#               روی خط اول پیام؛ کاملاً مستقل و بدون نیاز به پیام مرجع.
#   "reply"   → نقل‌قول واقعی MTProto یعنی «reply + quote_text» (همان قابلیتِ
#               «انتخاب متن → سه‌نقطه → نقل قول» در خود سروش پلاس). برای این حالت
#               باید پیام مرجعی وجود داشته باشد که خط تیتر داخلش باشد
#               (پارامتر quote_from_msg_id).
#   "off"     → بدون نقل‌قول (فقط Bold).
QUOTE_MODE = "entity"

# در حالت "reply": اگر سرور سروش این حالت را نپذیرفت، به حالت "entity" برگردد؟
QUOTE_REPLY_FALLBACK_TO_ENTITY = True

# اگر همان مالکِ قبلی دوباره «ai cod» بزند:
#   False → کاملاً نادیده گرفته شود (مالک جدیدی ساخته نمی‌شود)  [پیش‌فرض]
#   True  → پیام معرفی برای او ارسال شود (ولی باز هم مالک جدید ساخته نمی‌شود)
ANNOUNCE_ON_OWNER_REPEAT = False


@dataclass(frozen=True)
class Config:
    """تنظیمات اجرای ربات (قابل بازنویسی با متغیرهای محیطی)."""

    # --- فایل سشن (این فایل = دسترسی کامل به اکانت؛ هرگز به کسی ندهید) ---
    session_path: Path = DATA_DIR / "acod_userbot"

    # --- مقادیر عمومی کلاینت سروش پلاس (Bot Token نیستند) ---
    api_id: int = 1030400
    api_hash: str = "6edb16cf88714a4e9a805e928c39c937"

    # --- لاگین (اختیاری؛ اگر خالی باشند به‌صورت تعاملی پرسیده می‌شود) ---
    phone: str | None = None
    twofa_password: str | None = None

    # --- حافظه دائمی مالک سراسری ---
    db_path: Path = DATA_DIR / "owner.sqlite3"

    # --- دستورات و پرچم‌ها ---
    owner_command: str = OWNER_COMMAND
    kodrez_command: str = KODREZ_COMMAND
    groups_only: bool = GROUPS_ONLY
    private_auto_reply: bool = PRIVATE_AUTO_REPLY
    link_preview: bool = LINK_PREVIEW
    quote_mode: str = QUOTE_MODE
    quote_reply_fallback_to_entity: bool = QUOTE_REPLY_FALLBACK_TO_ENTITY
    announce_on_owner_repeat: bool = ANNOUNCE_ON_OWNER_REPEAT

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            session_path=Path(os.environ.get("ACOD_SESSION", DATA_DIR / "acod_userbot")),
            phone=os.environ.get("ACOD_PHONE") or None,
            twofa_password=os.environ.get("ACOD_PASSWORD") or None,
            db_path=Path(os.environ.get("ACOD_DB", DATA_DIR / "owner.sqlite3")),
            owner_command=os.environ.get("ACOD_OWNER_COMMAND", OWNER_COMMAND),
            kodrez_command=os.environ.get("ACOD_KODREZ_COMMAND", KODREZ_COMMAND),
            groups_only=os.environ.get("ACOD_GROUPS_ONLY", "1") not in ("0", "false", "False"),
            private_auto_reply=os.environ.get("ACOD_PRIVATE_AUTO_REPLY", "1")
            not in ("0", "false", "False"),
            link_preview=os.environ.get("ACOD_LINK_PREVIEW", "0") in ("1", "true", "True"),
            quote_mode=os.environ.get("ACOD_QUOTE_MODE", QUOTE_MODE),
        )


def ensure_data_dir(cfg: Config) -> None:
    for p in (cfg.session_path.parent, cfg.db_path.parent):
        Path(p).mkdir(parents=True, exist_ok=True)
