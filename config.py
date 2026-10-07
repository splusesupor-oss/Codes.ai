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


def load_env_file(path: str | Path | None = None, *, override: bool = False) -> int:
    """بارگذاری ساده‌ی فایل `.env` (بدون وابستگی بیرونی).

    * فقط خطوط `KEY=VALUE` خوانده می‌شوند؛ خطوط خالی و `#` نادیده گرفته می‌شوند.
    * به‌صورت پیش‌فرض متغیرهای محیطی موجود بازنویسی نمی‌شوند (اولویت با محیط).
    * فایل `.env` در `.gitignore` است و هرگز commit نمی‌شود.

    Returns: تعداد متغیرهایی که تنظیم شدند.
    """
    env_path = Path(path or os.environ.get("ACOD_ENV_FILE", BASE_DIR / ".env"))
    if not env_path.is_file():
        return 0

    loaded = 0
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded

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

# ---------------------------------------------------------------------------
# مدیریت کاربران PV (فقط مالک سراسری، فقط در چت خصوصی)
# ---------------------------------------------------------------------------
PV_COUNT_COMMAND = "تعداد اعضا"     # نمایش تعداد کل کاربران ثبت‌شده در PV
PV_LIST_COMMAND = "لیست اعضا"       # نمایش فهرست شماره‌گذاری‌شده‌ی کاربران PV

# نام جایگزین وقتی کاربر نه username دارد و نه نام نمایشی
PV_UNKNOWN_NAME = "کاربر بدون نام"

# ---------------------------------------------------------------------------
# هوش مصنوعی گروه‌ها (فقط GROUP) — Cloudflare Workers AI
# ---------------------------------------------------------------------------
AI_ONLINE_COMMAND = "ai online"     # روشن کردن AI برای همان گروه (فقط مالک سراسری)
AI_OF_COMMAND = "ai of"             # خاموش کردن AI برای همان گروه (فقط مالک سراسری)
AI_LIST_COMMAND = "ai list"         # مجاز کردن کاربرِ Reply‌شده (فقط مالک سراسری)
AI_LISTX_COMMAND = "ai list x"      # حذف مجوز کاربرِ Reply‌شده (فقط مالک سراسری)

# مدل واقعی Cloudflare Workers AI
AI_MODEL = "@cf/zai-org/glm-4.7-flash"

# سهمیه‌ی داخلی روزانه به تفکیک هر گروه (بر اساس روز UTC، هماهنگ با ریست Cloudflare)
AI_DAILY_QUOTA = 30

# محدودیت‌های مصرف برای هر درخواست
AI_MAX_OUTPUT_TOKENS = 256          # سقف توکن خروجی مدل
AI_MAX_INPUT_CHARS = 800            # طول متن ورودی هر پیام کاربر (مازاد بریده می‌شود)
AI_HISTORY_PAIRS = 2                # چند جفت گفت‌وگو در حافظه نگه داشته شود (۰ = بدون تاریخچه)
AI_TIMEOUT = 45                     # ثانیه

# مالک سراسری به‌صورت پیش‌فرض مجاز است با AI صحبت کند (قابل خاموش کردن)
AI_OWNER_ALWAYS_ALLOWED = True

# سقف طول هر پیام ارسالی. حد واقعی سرور در کتابخانه `utils.split_text`
# با limit=4096 تعریف شده است؛ برای حاشیه‌ی امن 3500 انتخاب شده تا لیست‌های
# بلند در چند پیام تکه‌تکه شوند و خطای طول پیام رخ ندهد.
MAX_MESSAGE_CHARS = 3500

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
    pv_count_command: str = PV_COUNT_COMMAND
    pv_list_command: str = PV_LIST_COMMAND
    pv_unknown_name: str = PV_UNKNOWN_NAME
    max_message_chars: int = MAX_MESSAGE_CHARS

    # --- هوش مصنوعی (Cloudflare Workers AI) ---
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    ai_model: str = AI_MODEL
    ai_online_command: str = AI_ONLINE_COMMAND
    ai_of_command: str = AI_OF_COMMAND
    ai_list_command: str = AI_LIST_COMMAND
    ai_listx_command: str = AI_LISTX_COMMAND
    ai_daily_quota: int = AI_DAILY_QUOTA
    ai_max_output_tokens: int = AI_MAX_OUTPUT_TOKENS
    ai_max_input_chars: int = AI_MAX_INPUT_CHARS
    ai_history_pairs: int = AI_HISTORY_PAIRS
    ai_timeout: float = AI_TIMEOUT
    ai_owner_always_allowed: bool = AI_OWNER_ALWAYS_ALLOWED
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
            pv_count_command=os.environ.get("ACOD_PV_COUNT_COMMAND", PV_COUNT_COMMAND),
            pv_list_command=os.environ.get("ACOD_PV_LIST_COMMAND", PV_LIST_COMMAND),
            pv_unknown_name=os.environ.get("ACOD_PV_UNKNOWN_NAME", PV_UNKNOWN_NAME),
            max_message_chars=int(os.environ.get("ACOD_MAX_MESSAGE_CHARS", MAX_MESSAGE_CHARS)),
            cloudflare_account_id=os.environ.get("CLOUDFLARE_ACCOUNT_ID") or None,
            cloudflare_api_token=os.environ.get("CLOUDFLARE_API_TOKEN") or None,
            ai_model=os.environ.get("ACOD_AI_MODEL", AI_MODEL),
            ai_online_command=os.environ.get("ACOD_AI_ONLINE_COMMAND", AI_ONLINE_COMMAND),
            ai_of_command=os.environ.get("ACOD_AI_OF_COMMAND", AI_OF_COMMAND),
            ai_list_command=os.environ.get("ACOD_AI_LIST_COMMAND", AI_LIST_COMMAND),
            ai_listx_command=os.environ.get("ACOD_AI_LISTX_COMMAND", AI_LISTX_COMMAND),
            ai_daily_quota=int(os.environ.get("ACOD_AI_DAILY_QUOTA", AI_DAILY_QUOTA)),
            ai_max_output_tokens=int(os.environ.get("ACOD_AI_MAX_OUTPUT_TOKENS", AI_MAX_OUTPUT_TOKENS)),
            ai_max_input_chars=int(os.environ.get("ACOD_AI_MAX_INPUT_CHARS", AI_MAX_INPUT_CHARS)),
            ai_history_pairs=int(os.environ.get("ACOD_AI_HISTORY_PAIRS", AI_HISTORY_PAIRS)),
            ai_timeout=float(os.environ.get("ACOD_AI_TIMEOUT", AI_TIMEOUT)),
            ai_owner_always_allowed=os.environ.get("ACOD_AI_OWNER_ALLOWED", "1")
            not in ("0", "false", "False"),
            link_preview=os.environ.get("ACOD_LINK_PREVIEW", "0") in ("1", "true", "True"),
            quote_mode=os.environ.get("ACOD_QUOTE_MODE", QUOTE_MODE),
        )


def ensure_data_dir(cfg: Config) -> None:
    for p in (cfg.session_path.parent, cfg.db_path.parent):
        Path(p).mkdir(parents=True, exist_ok=True)


# در زمان import، فایل .env (اگر وجود داشته باشد) بارگذاری می‌شود.
load_env_file()
