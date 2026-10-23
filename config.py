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

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, NamedTuple

log = logging.getLogger("acod.config")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("ACOD_DATA_DIR", BASE_DIR / "data"))

_UNSET: Any = object()
_CF_NUMBERED_RE = re.compile(r"^CF_ACCOUNT_(\d+)_ID$")


def mask_account_id(account_id: str | None) -> str:
    """نمایش امن و ماسک‌شده‌ی Account ID: ۴ کاراکتر اول + `...` + ۴ کاراکتر آخر."""
    clean = (account_id or "").strip()
    if not clean:
        return "—"
    if len(clean) > 8:
        return f"{clean[:4]}...{clean[-4:]}"
    if len(clean) > 4:
        return f"{clean[:2]}...{clean[-2:]}"
    return "****"


def token_fingerprint(token: str | None) -> str:
    """اثرانگشت کوتاه SHA-256 (۸ کاراکتر اول) بدون افشای هیچ بخشی از توکن اصلی."""
    clean = (token or "").strip()
    if not clean:
        return "—"
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]


def _is_placeholder_secret(value: str | None) -> bool:
    """تشخیص مقادیر خالی یا نمونه (مثل your_cloudflare_account_id)."""
    low = (value or "").strip().lower()
    return not low or low.startswith(("your_", "your-", "xxx", "changeme", "<"))


class CloudflareAccountCreds(NamedTuple):
    """جفت `(account_id, api_token)` سازگار با تاپل استاندارد پایتون و دسترسی صفتی امن.

    چون از `NamedTuple` ارث‌بری می‌کند:
      * دقیقاً معادل `(account_id, token)` است (`acc_id, tok = item` و `item == (id, tok)`).
      * هم‌زمان دسترسی `item.account_id` و `item.api_token` هم برقرار است.
      * متد `__repr__` هرگز توکن خام را چاپ نمی‌کند.
    """

    account_id: str
    api_token: str

    def __repr__(self) -> str:
        return (
            f"CloudflareAccountCreds(account_id='{mask_account_id(self.account_id)}', "
            f"token_sha256='{token_fingerprint(self.api_token)}')"
        )


def normalize_cf_accounts(accounts: Any) -> list[CloudflareAccountCreds]:
    """نرمال‌سازی ورودی‌های متنوع به لیست مرتب و بدون تکرار `[(account_id, token), ...]`."""
    if not accounts:
        return []
    result: list[CloudflareAccountCreds] = []
    seen_ids: set[str] = set()
    for item in accounts:
        acc_id = ""
        api_tok = ""
        if isinstance(item, CloudflareAccountCreds):
            acc_id = (item.account_id or "").strip()
            api_tok = (item.api_token or "").strip()
        elif isinstance(item, Mapping):
            acc_id = str(item.get("account_id") or item.get("id") or "").strip()
            api_tok = str(item.get("api_token") or item.get("token") or "").strip()
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            acc_id = str(item[0] or "").strip()
            api_tok = str(item[1] or "").strip()
        elif hasattr(item, "account_id") and hasattr(item, "api_token"):
            acc_id = str(getattr(item, "account_id", "") or "").strip()
            api_tok = str(getattr(item, "api_token", "") or "").strip()

        if _is_placeholder_secret(acc_id) or _is_placeholder_secret(api_tok):
            continue
        if acc_id in seen_ids:
            continue
        seen_ids.add(acc_id)
        result.append(CloudflareAccountCreds(account_id=acc_id, api_token=api_tok))
    return result


def parse_cloudflare_accounts_from_env(
    environ: Mapping[str, str] | None = None,
) -> list[CloudflareAccountCreds]:
    """استخراج لیست حساب‌های Cloudflare از متغیرهای محیطی با ترتیب اولویت مشخص:

      ۱) اگر `CLOUDFLARE_ACCOUNTS_JSON` تنظیم و معتبر بود -> parse و بازگردانی همان لیست
      ۲) وگرنه اگر `CF_ACCOUNT_1_ID`/`CF_ACCOUNT_1_TOKEN` ... موجود بودند -> جمع‌آوری به ترتیب شماره
      ۳) وگرنه `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` قدیمی به‌عنوان تک‌حساب
    """
    env = os.environ if environ is None else environ

    # ۱) الگوی JSON: CLOUDFLARE_ACCOUNTS_JSON=[{"account_id":"...","api_token":"..."}]
    raw_json = (env.get("CLOUDFLARE_ACCOUNTS_JSON") or "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, list):
                normalized_json = normalize_cf_accounts(parsed)
                if normalized_json:
                    return normalized_json
            else:
                log.warning("CLOUDFLARE_ACCOUNTS_JSON باید آرایه‌ای از اشیاء JSON باشد.")
        except Exception as exc:
            log.warning("خطا در پارس CLOUDFLARE_ACCOUNTS_JSON (محتوای حساس چاپ نمی‌شود): %s", type(exc).__name__)

    # ۲) الگوی شماره‌گذاری‌شده: CF_ACCOUNT_1_ID / CF_ACCOUNT_1_TOKEN تا CF_ACCOUNT_N_...
    numbered_indices: list[int] = []
    for key in env.keys():
        m = _CF_NUMBERED_RE.match(key)
        if m:
            numbered_indices.append(int(m.group(1)))
    if numbered_indices:
        numbered_items: list[tuple[str, str]] = []
        for idx in sorted(set(numbered_indices)):
            acc_id = (env.get(f"CF_ACCOUNT_{idx}_ID") or "").strip()
            api_tok = (env.get(f"CF_ACCOUNT_{idx}_TOKEN") or "").strip()
            if acc_id and api_tok:
                numbered_items.append((acc_id, api_tok))
        normalized_numbered = normalize_cf_accounts(numbered_items)
        if normalized_numbered:
            return normalized_numbered

    # ۳) سازگاری عقب‌رو: CLOUDFLARE_ACCOUNT_ID و CLOUDFLARE_API_TOKEN قدیمی
    legacy_id = (env.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    legacy_tok = (env.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if legacy_id and legacy_tok:
        return normalize_cf_accounts([(legacy_id, legacy_tok)])

    return []


def _env_str(*names: str) -> str | None:
    """اولین متغیر محیطی موجود و «غیرخالی» را برمی‌گرداند (وگرنه None).

    برای مقادیر Secret مثل Cloudflare استفاده می‌شود؛ رشته‌ی خالی/فقط‌فاصله
    به‌معنای «تنظیم‌نشده» است.
    """
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


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
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
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
# املاهای پذیرفته‌شده‌ی همین دستور (هر دو «ai cod» و «ai code» یک دستورند)
OWNER_COMMAND_ALIASES = ("ai cod", "ai code")
KODREZ_COMMAND = "کدرز"       # دستور معرفی؛ برای همه آزاد است

# دستورات جدید سیستم
TERY_AI_COMMAND = "Tery ai"   # ثبت مالک ربات توسط مالک سراسری
AI_XCOD_COMMAND = "ai x cod"  # فعال‌سازی ربات در گروه (فقط مالک سراسری)
AI_L_COMMAND = "ai L"         # نمایش لیست کاربران مجاز گروه
AI_PLUN_COMMAND = "ai plun"   # نمایش باقیمانده سهمیه روزانه
AI_SINGLE_COMMAND = "ai"      # صدا زدن ربات («جانم 👾»)
HELP_COMMAND = "راهنما"        # راهنمای فارسی با فرمت خاص
AI_ACCOUNTS_COMMAND = "ai accounts"              # نمایش وضعیت حساب‌های Cloudflare (مالک سراسری در PV)
AI_ACCOUNTS_RESET_COMMAND = "ai accounts reset"  # ریست دستی وضعیت exhausted حساب‌ها (مالک سراسری در PV)

# ---------------------------------------------------------------------------
# پرچم‌های رفتاری
# ---------------------------------------------------------------------------
GROUPS_ONLY = True
PRIVATE_AUTO_REPLY = True

# ---------------------------------------------------------------------------
# مدیریت کاربران PV (فقط مالک سراسری، فقط در چت خصوصی)
# ---------------------------------------------------------------------------
PV_COUNT_COMMAND = "تعداد اعضا"     # نمایش تعداد کل کاربران ثبت‌شده در PV
PV_LIST_COMMAND = "لیست اعضا"       # نمایش فهرست شماره‌گذاری‌شده‌ی کاربران PV
PV_UNKNOWN_NAME = "کاربر بدون نام"

# ---------------------------------------------------------------------------
# هوش مصنوعی گروه‌ها (فقط GROUP) — Cloudflare Workers AI + Groq
# ---------------------------------------------------------------------------
AI_ONLINE_COMMAND = "ai online"     # روشن کردن AI برای همان گروه (مالک سراسری یا مالک ثبت‌شده)
AI_OF_COMMAND = "ai of"             # خاموش کردن AI برای همان گروه (مالک سراسری یا مالک ثبت‌شده)
AI_LIST_COMMAND = "ai list"         # مجاز کردن کاربرِ Reply‌شده (مالک سراسری یا مالک ثبت‌شده)
AI_LISTX_COMMAND = "ai list x"      # حذف مجوز کاربرِ Reply‌شده (مالک سراسری یا مالک ثبت‌شده)

# مدل‌های هوش مصنوعی سه‌گانه (Groq و Cloudflare)
GROQ_API_BASE = "https://api.groq.com/openai/v1"
GROQ_CHAT_MODEL = "openai/gpt-oss-20b"
GROQ_CODE_MODEL = "openai/gpt-oss-120b"

AI_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"

# سهمیه‌ی داخلی روزانه به تفکیک هر گروه (ریست در 00:00 به وقت Asia/Tehran)
AI_DAILY_QUOTA = 5000

# حداکثر تعداد کاربران مجاز هوش مصنوعی در هر گروه
AI_MAX_ALLOWED_USERS = 3

# منطقه زمانی ریست سهمیه روزانه
AI_QUOTA_TIMEZONE = "Asia/Tehran"

# محدودیت‌های مصرف برای هر درخواست
AI_MAX_OUTPUT_TOKENS = 4096
AI_MAX_INPUT_CHARS = 800            # طول متن ورودی هر پیام کاربر (مازاد بریده می‌شود)
AI_HISTORY_PAIRS = 2                # چند جفت گفت‌وگو در حافظه نگه داشته شود (۰ = بدون تاریخچه)
AI_TIMEOUT = 22                     # ثانیه
AI_RETRY_MAX = 3                    # حداکثر تلاش مجدد برای خطاهای موقت (شبکه/ظرفیت/5xx)
AI_RETRY_BASE_DELAY = 1.2           # ثانیه — تأخیر پایه برای backoff نمایی

# مالک سراسری به‌صورت پیش‌فرض مجاز است با AI صحبت کند (قابل خاموش کردن)
AI_OWNER_ALWAYS_ALLOWED = True

# اتصال خودکار مدل Cloudflare به جستجوی زنده وب و تقویم روز تا سال ۲۰۲۶ / ۱۴۰۵
AI_WEB_SEARCH_ENABLED = True

# ارتقای خودکار شناسه‌های قدیمی/منسوخ‌شده‌ی ۲۰۲۱-۲۰۲۳ کلودفلر در فایل‌های .env قدیمی
LEGACY_DEPRECATED_CF_MODELS = frozenset({
    "@cf/meta/llama-2-7b-chat-int8",
    "@cf/meta/llama-2-7b-chat-fp16",
    "@cf/meta/llama-3-8b-instruct",
    "@cf/mistral/mistral-7b-instruct-v0.1",
    "@cf/google/gemma-7b-it",
})


def upgrade_legacy_cf_model(model_id: str | None) -> str:
    """ارتقای خودکار مدل‌های منسوخ ۲۰۲۱ کلودفلر به مدل به‌روز بدون دست‌زدن به مدل‌های سفارشی جدید."""
    clean = (model_id or "").strip()
    if not clean:
        return AI_MODEL
    if clean in LEGACY_DEPRECATED_CF_MODELS:
        log.info("مدل قدیمی کلودفلر (%s) خودکار به @cf/meta/llama-3.3-70b-instruct-fp8-fast ارتقا یافت.", clean)
        return "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    return clean

MAX_MESSAGE_CHARS = 3500
LINK_PREVIEW = False
QUOTE_MODE = "entity"
QUOTE_REPLY_FALLBACK_TO_ENTITY = True
ANNOUNCE_ON_OWNER_REPEAT = True


@dataclass(frozen=True)
class Config:
    """تنظیمات اجرای ربات (قابل بازنویسی با متغیرهای محیطی)."""

    # --- فایل سشن ---
    session_path: Path = DATA_DIR / "acod_userbot"

    # --- مقادیر عمومی کلاینت سروش پلاس ---
    api_id: int = 1030400
    api_hash: str = "6edb16cf88714a4e9a805e928c39c937"

    # --- لاگین ---
    phone: str | None = None
    twofa_password: str | None = field(default=None, repr=False)

    # --- حافظه دائمی مالک سراسری ---
    db_path: Path = DATA_DIR / "owner.sqlite3"

    # --- دستورات و پرچم‌ها ---
    owner_command: str = OWNER_COMMAND
    owner_command_aliases: tuple = OWNER_COMMAND_ALIASES
    kodrez_command: str = KODREZ_COMMAND
    tery_ai_command: str = TERY_AI_COMMAND
    ai_xcod_command: str = AI_XCOD_COMMAND
    ai_l_command: str = AI_L_COMMAND
    ai_plun_command: str = AI_PLUN_COMMAND
    ai_single_command: str = AI_SINGLE_COMMAND
    help_command: str = HELP_COMMAND
    ai_accounts_command: str = AI_ACCOUNTS_COMMAND
    ai_accounts_reset_command: str = AI_ACCOUNTS_RESET_COMMAND
    groups_only: bool = GROUPS_ONLY
    private_auto_reply: bool = PRIVATE_AUTO_REPLY
    pv_count_command: str = PV_COUNT_COMMAND
    pv_list_command: str = PV_LIST_COMMAND
    pv_unknown_name: str = PV_UNKNOWN_NAME
    max_message_chars: int = MAX_MESSAGE_CHARS

    # --- هوش مصنوعی (Cloudflare Workers AI — پشتیبانی از تک‌حساب و چندحساب) ---
    cloudflare_account_id: str | None = field(default=_UNSET, repr=False)
    cloudflare_api_token: str | None = field(default=_UNSET, repr=False)
    cloudflare_accounts: list[CloudflareAccountCreds] = field(default=_UNSET)
    groq_api_key: str | None = field(
        default_factory=lambda: _env_str("GROQ_API_KEY"),
        repr=False,
    )
    ai_model: str = AI_MODEL
    ai_online_command: str = AI_ONLINE_COMMAND
    ai_of_command: str = AI_OF_COMMAND
    ai_list_command: str = AI_LIST_COMMAND
    ai_listx_command: str = AI_LISTX_COMMAND
    ai_daily_quota: int = AI_DAILY_QUOTA
    ai_max_allowed_users: int = AI_MAX_ALLOWED_USERS
    ai_quota_timezone: str = AI_QUOTA_TIMEZONE
    ai_max_output_tokens: int = AI_MAX_OUTPUT_TOKENS
    ai_max_input_chars: int = AI_MAX_INPUT_CHARS
    ai_history_pairs: int = AI_HISTORY_PAIRS
    ai_timeout: float = AI_TIMEOUT
    ai_retry_max: int = AI_RETRY_MAX
    ai_retry_base_delay: float = AI_RETRY_BASE_DELAY
    ai_owner_always_allowed: bool = AI_OWNER_ALWAYS_ALLOWED
    ai_web_search: bool = AI_WEB_SEARCH_ENABLED
    link_preview: bool = LINK_PREVIEW
    quote_mode: str = QUOTE_MODE
    quote_reply_fallback_to_entity: bool = QUOTE_REPLY_FALLBACK_TO_ENTITY
    announce_on_owner_repeat: bool = ANNOUNCE_ON_OWNER_REPEAT

    def __post_init__(self) -> None:
        raw_acc = self.cloudflare_account_id
        raw_tok = self.cloudflare_api_token
        raw_pool = self.cloudflare_accounts

        if raw_pool is not _UNSET:
            accounts = normalize_cf_accounts(raw_pool)
            primary_id = (
                accounts[0].account_id
                if raw_acc is _UNSET and accounts
                else ((raw_acc or "").strip() or None if raw_acc is not _UNSET else None)
            )
            primary_tok = (
                accounts[0].api_token
                if raw_tok is _UNSET and accounts
                else ((raw_tok or "").strip() or None if raw_tok is not _UNSET else None)
            )
            object.__setattr__(self, "cloudflare_account_id", primary_id)
            object.__setattr__(self, "cloudflare_api_token", primary_tok)
            object.__setattr__(self, "cloudflare_accounts", accounts)
            return

        if raw_acc is _UNSET and raw_tok is _UNSET:
            env_accounts = parse_cloudflare_accounts_from_env()
            primary_id = (
                env_accounts[0].account_id
                if env_accounts
                else _env_str("CLOUDFLARE_ACCOUNT_ID")
            )
            primary_tok = (
                env_accounts[0].api_token
                if env_accounts
                else _env_str("CLOUDFLARE_API_TOKEN")
            )
            object.__setattr__(self, "cloudflare_account_id", primary_id)
            object.__setattr__(self, "cloudflare_api_token", primary_tok)
            object.__setattr__(self, "cloudflare_accounts", env_accounts)
            return

        resolved_id = (
            _env_str("CLOUDFLARE_ACCOUNT_ID")
            if raw_acc is _UNSET
            else ((raw_acc or "").strip() or None)
        )
        resolved_tok = (
            _env_str("CLOUDFLARE_API_TOKEN")
            if raw_tok is _UNSET
            else ((raw_tok or "").strip() or None)
        )
        object.__setattr__(self, "cloudflare_account_id", resolved_id)
        object.__setattr__(self, "cloudflare_api_token", resolved_tok)

        if resolved_id and resolved_tok:
            object.__setattr__(
                self,
                "cloudflare_accounts",
                normalize_cf_accounts([(resolved_id, resolved_tok)]),
            )
        else:
            object.__setattr__(self, "cloudflare_accounts", [])

    @classmethod
    def from_env(cls) -> "Config":
        env_accounts = parse_cloudflare_accounts_from_env()
        primary_id = (
            env_accounts[0].account_id
            if env_accounts
            else _env_str("CLOUDFLARE_ACCOUNT_ID")
        )
        primary_tok = (
            env_accounts[0].api_token
            if env_accounts
            else _env_str("CLOUDFLARE_API_TOKEN")
        )
        return cls(
            session_path=Path(os.environ.get("ACOD_SESSION", DATA_DIR / "acod_userbot")),
            phone=os.environ.get("ACOD_PHONE") or None,
            twofa_password=os.environ.get("ACOD_PASSWORD") or None,
            db_path=Path(os.environ.get("ACOD_DB", DATA_DIR / "owner.sqlite3")),
            owner_command=os.environ.get("ACOD_OWNER_COMMAND", OWNER_COMMAND),
            owner_command_aliases=tuple(
                a.strip() for a in os.environ.get(
                    "ACOD_OWNER_ALIASES", ",".join(OWNER_COMMAND_ALIASES)
                ).split(",") if a.strip()
            ),
            kodrez_command=os.environ.get("ACOD_KODREZ_COMMAND", KODREZ_COMMAND),
            tery_ai_command=os.environ.get("ACOD_TERY_AI_COMMAND", TERY_AI_COMMAND),
            ai_xcod_command=os.environ.get("ACOD_AI_XCOD_COMMAND", AI_XCOD_COMMAND),
            ai_l_command=os.environ.get("ACOD_AI_L_COMMAND", AI_L_COMMAND),
            ai_plun_command=os.environ.get("ACOD_AI_PLUN_COMMAND", AI_PLUN_COMMAND),
            ai_single_command=os.environ.get("ACOD_AI_SINGLE_COMMAND", AI_SINGLE_COMMAND),
            help_command=os.environ.get("ACOD_HELP_COMMAND", HELP_COMMAND),
            ai_accounts_command=os.environ.get("ACOD_AI_ACCOUNTS_COMMAND", AI_ACCOUNTS_COMMAND),
            ai_accounts_reset_command=os.environ.get(
                "ACOD_AI_ACCOUNTS_RESET_COMMAND", AI_ACCOUNTS_RESET_COMMAND
            ),
            groups_only=os.environ.get("ACOD_GROUPS_ONLY", "1") not in ("0", "false", "False"),
            private_auto_reply=os.environ.get("ACOD_PRIVATE_AUTO_REPLY", "1")
            not in ("0", "false", "False"),
            announce_on_owner_repeat=os.environ.get(
                "ACOD_ANNOUNCE_ON_OWNER_REPEAT", "1" if ANNOUNCE_ON_OWNER_REPEAT else "0"
            ) not in ("0", "false", "False"),
            pv_count_command=os.environ.get("ACOD_PV_COUNT_COMMAND", PV_COUNT_COMMAND),
            pv_list_command=os.environ.get("ACOD_PV_LIST_COMMAND", PV_LIST_COMMAND),
            pv_unknown_name=os.environ.get("ACOD_PV_UNKNOWN_NAME", PV_UNKNOWN_NAME),
            max_message_chars=int(os.environ.get("ACOD_MAX_MESSAGE_CHARS", MAX_MESSAGE_CHARS)),
            cloudflare_account_id=primary_id,
            cloudflare_api_token=primary_tok,
            cloudflare_accounts=env_accounts,
            groq_api_key=_env_str("GROQ_API_KEY"),
            ai_model=upgrade_legacy_cf_model(os.environ.get("ACOD_AI_MODEL", AI_MODEL)),
            ai_online_command=os.environ.get("ACOD_AI_ONLINE_COMMAND", AI_ONLINE_COMMAND),
            ai_of_command=os.environ.get("ACOD_AI_OF_COMMAND", AI_OF_COMMAND),
            ai_list_command=os.environ.get("ACOD_AI_LIST_COMMAND", AI_LIST_COMMAND),
            ai_listx_command=os.environ.get("ACOD_AI_LISTX_COMMAND", AI_LISTX_COMMAND),
            ai_daily_quota=int(os.environ.get("ACOD_AI_DAILY_QUOTA", AI_DAILY_QUOTA)),
            ai_max_allowed_users=int(os.environ.get("ACOD_AI_MAX_ALLOWED_USERS", AI_MAX_ALLOWED_USERS)),
            ai_quota_timezone=os.environ.get("ACOD_AI_QUOTA_TIMEZONE", AI_QUOTA_TIMEZONE),
            ai_max_output_tokens=int(os.environ.get("ACOD_AI_MAX_OUTPUT_TOKENS", AI_MAX_OUTPUT_TOKENS)),
            ai_max_input_chars=int(os.environ.get("ACOD_AI_MAX_INPUT_CHARS", AI_MAX_INPUT_CHARS)),
            ai_history_pairs=int(os.environ.get("ACOD_AI_HISTORY_PAIRS", AI_HISTORY_PAIRS)),
            ai_timeout=float(os.environ.get("ACOD_AI_TIMEOUT", AI_TIMEOUT)),
            ai_retry_max=int(os.environ.get("ACOD_AI_RETRY_MAX", AI_RETRY_MAX)),
            ai_retry_base_delay=float(os.environ.get("ACOD_AI_RETRY_BASE_DELAY", AI_RETRY_BASE_DELAY)),
            ai_owner_always_allowed=os.environ.get("ACOD_AI_OWNER_ALLOWED", "1")
            not in ("0", "false", "False"),
            ai_web_search=os.environ.get("ACOD_AI_WEB_SEARCH", "1")
            not in ("0", "false", "False"),
            link_preview=os.environ.get("ACOD_LINK_PREVIEW", "0") in ("1", "true", "True"),
            quote_mode=os.environ.get("ACOD_QUOTE_MODE", QUOTE_MODE),
        )


def ensure_data_dir(cfg: Config) -> None:
    for p in (cfg.session_path.parent, cfg.db_path.parent):
        Path(p).mkdir(parents=True, exist_ok=True)


load_env_file()
