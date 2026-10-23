"""
کلاینت واقعی Cloudflare Workers AI (REST API) با پشتیبانی از چند حساب و چرخش خودکار سهمیه.

endpoint واقعی (تأییدشده از مستندات رسمی Cloudflare):

    POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}
    Authorization: Bearer {API_TOKEN}
    body: {"messages": [{"role": "...", "content": "..."}], "max_completion_tokens": N}

قابلیت چند حساب (AccountPool):
  * نگهداری فهرست مرتب حساب‌ها با وضعیت‌های `active` / `exhausted_today` / `disabled`.
  * انتخاب همیشگی اولین حساب `active`.
  * چرخش خودکار و فوری به حساب بعدی هنگام اتمام سهمیه‌ی روزانه (HTTP 429 سهمیه‌ای،
    کدهای 4006 / 3036 / 3037، یا پیام شامل `daily free allocation`).
  * عدم مسدودسازی حساب در خطاهای غیرسهمیه‌ای (مانند 3040 ظرفیت موقت یا 5007 مدل نامعتبر).
  * ریست خودکار وضعیت `exhausted_today` در ساعت `00:00 UTC` بدون نیاز به ری‌استارت ربات.
  * همگام‌سازی و ماندگاری کامل وضعیت حساب‌ها در SQLite (`cf_accounts`) در صورت اتصال `OwnerStore`.

هیچ Token یا Secretی در کد نیست؛ همه از متغیرهای محیطی/.env خوانده می‌شوند و در هیچ
پیام خطا یا لاگی چاپ نمی‌شوند (فقط شماره حساب و شناسه‌ی ماسک‌شده درج می‌گردد).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from config import mask_account_id, normalize_cf_accounts, token_fingerprint

log = logging.getLogger("acod.ai.client")

API_BASE = "https://api.cloudflare.com/client/v4/accounts"
DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"
QUOTA_EXHAUSTED_MESSAGE = "سهمیه روزانه هوش مصنوعی به پایان رسیده است."

VALID_ACCOUNT_STATUSES = ("active", "exhausted_today", "disabled")

# --- دسته‌بندی کدهای خطا (طبق جدول رسمی Workers AI + کدهای احراز هویتِ مشاهده‌شده) ---
QUOTA_ERROR_CODES = {3036, 4006, 3037}          # سهمیه‌ی روزانه تمام شده است
CAPACITY_ERROR_CODES = {3040}                   # ظرفیت موقتاً پر است — سهمیه نیست؛ ری‌ترای دارد
AUTH_ERROR_CODES = {10000, 9109, 9106}          # مشکل توکن/دسترسی
PAID_PLAN_ERROR_CODES = {5035}                  # نیاز به Workers Paid
MODEL_ERROR_CODES = {5007, 3042}                # مدل ناموجود / شناسه نامعتبر
REQUEST_ERROR_CODES = {3006, 5004, 3003}        # درخواست بزرگ/ناقص/نامعتبر
TIMEOUT_ERROR_CODES = {3007, 3008}              # timeout/aborted سمت سرور
# خطاهایی که موقت هستند و ارزش تلاش مجدد دارند (شبکه، ظرفیت، خطای سرور، timeout)
RETRYABLE_ERROR_CODES = CAPACITY_ERROR_CODES | TIMEOUT_ERROR_CODES | {3001, 3002, 1000, 1001, 50000}

QUOTA_KEYWORDS = (
    "neurons",
    "daily free allocation",
    "quota",
    "rate limit",
    "too many requests",
    "limit exceeded",
)

# متن راهنمای هر کد خطا (بدون هیچ داده‌ی حساس) — برای لاگ/ابزار تشخیصی
ERROR_HINTS = {
    3036: "سهمیه‌ی روزانه‌ی رایگان Workers AI (۱۰٬۰۰۰ نورون) تمام شده است و ۰۰:۰۰ UTC ریست می‌شود.",
    4006: "سهمیه‌ی روزانه تمام شده است (کد 4006 که در عمل برگردانده می‌شود) — ریست در ۰۰:۰۰ UTC.",
    3037: "محدودیت نرخ/سهمیه فعال شده است؛ بعداً تلاش کنید.",
    3040: "ظرفیت سرویس موقتاً پر است (Out of capacity)؛ این خطا سهمیه نیست و با تلاش مجدد درست می‌شود.",
    10000: "خطای احراز هویت: توکن نامعتبر است، یا مربوط به همین حساب نیست، یا Client IP "
           "Address Filtering روی توکن فعال است.",
    9109: "توکن اجازه‌ی دسترسی به این منبع را ندارد: باید مجوز Workers AI (Read یا Edit) و در "
          "سطح همان حساب Cloudflare باشد.",
    5035: "این مدل روی پلن Free در دسترس نیست و به Workers Paid نیاز دارد.",
    5007: "مدل یا تسک با این نام پیدا نشد؛ نام مدل را بررسی کنید.",
    3042: "شناسه‌ی مدل نامعتبر است.",
    3006: "درخواست بزرگ‌تر از سقف مجاز است (طول پیام/تاریخچه را کم کنید).",
    3007: "سرور Cloudflare مهلت درخواست را تمام کرد (Timeout)؛ دوباره تلاش کنید.",
    3008: "درخواست سمت سرور لغو شد (Aborted)؛ دوباره تلاش کنید.",
}


def _scrub(text: str, *secrets: str) -> str:
    """حذف هر اتفاقیِ مقادیر حساس از متن (برای لاگ/خروجی تشخیصی)."""
    out = text or ""
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "***")
    return out


class AIError(Exception):
    """خطای عمومی سرویس هوش مصنوعی."""

    #: آیا این خطا «موقتی» است و تلاش مجدد می‌تواند موفق باشد؟
    retryable: bool = False

    def __init__(self, message: str = "", *args, retryable: bool = False):
        super().__init__(message, *args)
        self.retryable = bool(retryable)


class AIQuotaExceeded(AIError):
    """سهمیه‌ی روزانه‌ی Cloudflare (Neurons) تمام شده است — تلاش مجدد روی همان حساب بی‌فایده است."""


class AIConfigError(AIError):
    """تنظیمات ناقص (Account ID یا API Token تنظیم نشده)."""


@dataclass
class AIResponse:
    """پاسخ مدل.

    ``finish_reason``/``reasoning``/``used_reasoning`` برای مدل‌های استدلالی اضافه شده‌اند
    و مقدار پیش‌فرض دارند (سازگار با کدهای قبلی).
    """

    text: str
    usage: Optional[dict] = None
    finish_reason: Optional[str] = None
    reasoning: Optional[str] = None
    used_reasoning: bool = False


@dataclass
class APIStatus:
    """نتیجه‌ی خام و «غیرحساس» یک تماس با API (برای ابزار تشخیصی)."""

    status: int = 0
    codes: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    ok: bool = False
    elapsed: float = 0.0
    field_used: str = "max_completion_tokens"


@dataclass
class PoolAccount:
    """وضعیت زمان اجرای یک حساب در `AccountPool` (با نمایش امن بدون توکن خام)."""

    index: int
    account_id: str
    api_token: str = field(repr=False)
    status: str = "active"                 # active | exhausted_today | disabled
    exhausted_day: Optional[str] = None    # YYYY-MM-DD (UTC)
    request_count: int = 0
    error_count: int = 0
    last_used_at: Optional[str] = None

    @property
    def masked_id(self) -> str:
        return mask_account_id(self.account_id)

    @property
    def token_fp(self) -> str:
        return token_fingerprint(self.api_token)

    def endpoint(self, model: str) -> str:
        return f"{API_BASE}/{self.account_id}/ai/run/{model}"

    def __repr__(self) -> str:
        return (
            f"PoolAccount(#{self.index}, account_id='{self.masked_id}', status='{self.status}', "
            f"requests={self.request_count}, errors={self.error_count}, "
            f"token_sha256='{self.token_fp}')"
        )


class AccountPool:
    """استخر مدیریت و چرخش خودکار حساب‌های Cloudflare Workers AI."""

    def __init__(
        self,
        accounts: Optional[Any] = None,
        *,
        store=None,
        now_provider: Optional[Callable[..., _dt.datetime]] = None,
    ):
        self._store = store
        self._now_provider = now_provider
        self._custom_clock = now_provider is not None
        self._accounts: list[PoolAccount] = []
        self._last_utc_day: str = self.utc_day()

        # استخراج وضعیت‌های اولیه‌ی صریح (اگر دیکشنری با کلید status پاس داده شده باشد)
        explicit_statuses: dict[str, tuple[str, Optional[str]]] = {}
        if accounts:
            for item in accounts:
                if isinstance(item, dict) and item.get("status") in VALID_ACCOUNT_STATUSES:
                    acc_id = str(item.get("account_id") or "").strip()
                    if acc_id:
                        explicit_statuses[acc_id] = (
                            str(item["status"]),
                            item.get("exhausted_day"),
                        )
                elif isinstance(item, PoolAccount) and item.status in VALID_ACCOUNT_STATUSES:
                    explicit_statuses[item.account_id] = (item.status, item.exhausted_day)

        normalized = normalize_cf_accounts(accounts)

        if self._store is not None:
            # همگام‌سازی حساب‌های ورودی (.env) با SQLite بدون حذف حساب‌های قبلی
            db_rows = self._store.sync_cf_accounts(normalized)
            for idx, row in enumerate(db_rows, start=1):
                entry = PoolAccount(
                    index=idx,
                    account_id=row.account_id,
                    api_token=row.api_token,
                    status=row.status if row.status in VALID_ACCOUNT_STATUSES else "active",
                    exhausted_day=row.exhausted_day,
                    request_count=int(row.request_count or 0),
                    error_count=int(row.error_count or 0),
                    last_used_at=row.last_used_at,
                )
                if entry.account_id in explicit_statuses:
                    st, ex_day = explicit_statuses[entry.account_id]
                    entry.status = st
                    entry.exhausted_day = ex_day or (
                        self._last_utc_day if st == "exhausted_today" else None
                    )
                    self._store.set_cf_account_status(
                        entry.account_id, entry.status, entry.exhausted_day
                    )
                self._accounts.append(entry)
        else:
            for idx, cred in enumerate(normalized, start=1):
                st, ex_day = explicit_statuses.get(cred.account_id, ("active", None))
                if st == "exhausted_today" and not ex_day:
                    ex_day = self._last_utc_day
                self._accounts.append(
                    PoolAccount(
                        index=idx,
                        account_id=cred.account_id,
                        api_token=cred.api_token,
                        status=st,
                        exhausted_day=ex_day,
                    )
                )

        # بررسی اولیه‌ی تغییر روز UTC هنگام ساخت استخر
        self.refresh_utc_day()

    @classmethod
    def from_config(
        cls,
        cfg,
        *,
        store=None,
        now_provider: Optional[Callable[..., _dt.datetime]] = None,
    ) -> "AccountPool":
        accounts = getattr(cfg, "cloudflare_accounts", None)
        if not accounts and getattr(cfg, "cloudflare_account_id", None) and getattr(cfg, "cloudflare_api_token", None):
            accounts = [(cfg.cloudflare_account_id, cfg.cloudflare_api_token)]
        return cls(accounts=accounts, store=store, now_provider=now_provider)

    def __repr__(self) -> str:
        return f"AccountPool(accounts={self._accounts!r})"

    # ------------------------------------------------------------------ زمان UTC
    def _utc_now(self) -> _dt.datetime:
        if self._now_provider is not None:
            try:
                now = self._now_provider(_dt.timezone.utc)
            except TypeError:
                now = self._now_provider()
        else:
            now = _dt.datetime.now(_dt.timezone.utc)
        if now.tzinfo is None:
            return now.replace(tzinfo=_dt.timezone.utc)
        return now.astimezone(_dt.timezone.utc)

    def utc_day(self) -> str:
        """تاریخ روز جاری بر اساس ساعت جهانی UTC به قالب YYYY-MM-DD."""
        return self._utc_now().strftime("%Y-%m-%d")

    def refresh_utc_day(self) -> int:
        """بررسی شروع روز جدید UTC و ریست خودکار حساب‌های `exhausted_today`."""
        today = self.utc_day()
        reset_count = 0

        # اگر استخر به SQLite متصل است، وضعیت تازه را با دیتابیس نیز همگام کن
        if self._store is not None:
            db_reset = self._store.reset_cf_accounts_if_new_utc_day(today)
            if db_reset > 0:
                reset_count = max(reset_count, db_reset)

        for acc in self._accounts:
            if acc.status == "exhausted_today" and acc.exhausted_day != today:
                acc.status = "active"
                acc.exhausted_day = None
                reset_count += 1

        self._last_utc_day = today
        if reset_count > 0:
            log.info(
                "🔄 شروع روز جدید UTC (%s): وضعیت سهمیه‌ی حساب‌های Cloudflare خودکار ریست شد.",
                today,
            )
        return reset_count

    # ------------------------------------------------------------------ دسترسی به حساب‌ها
    @property
    def accounts(self) -> list[PoolAccount]:
        self.refresh_utc_day()
        return list(self._accounts)

    @property
    def configured(self) -> bool:
        return len(self._accounts) > 0

    def _find(self, account_or_id: PoolAccount | str) -> Optional[PoolAccount]:
        acc_id = (
            account_or_id.account_id
            if isinstance(account_or_id, PoolAccount)
            else str(account_or_id).strip()
        )
        for acc in self._accounts:
            if acc.account_id == acc_id:
                return acc
        return None

    def get_active_account(self) -> Optional[PoolAccount]:
        """انتخاب اولین حساب با وضعیت `active` پس از بررسی ریست روزانه‌ی UTC."""
        self.refresh_utc_day()
        for acc in self._accounts:
            if acc.status == "active":
                return acc
        return None

    def active_accounts(self) -> list[PoolAccount]:
        self.refresh_utc_day()
        return [acc for acc in self._accounts if acc.status == "active"]

    def record_request(self, account_or_id: PoolAccount | str) -> None:
        """افزایش شمارنده‌ی درخواست و ثبت زمان آخرین استفاده (UTC ISO-8601)."""
        acc = self._find(account_or_id)
        if acc is None:
            return
        used_at = self._utc_now().isoformat(timespec="seconds")
        acc.request_count += 1
        acc.last_used_at = used_at
        if self._store is not None:
            self._store.record_cf_account_request(acc.account_id, used_at=used_at)

    def record_error(self, account_or_id: PoolAccount | str) -> None:
        """افزایش شمارنده‌ی خطای حساب."""
        acc = self._find(account_or_id)
        if acc is None:
            return
        acc.error_count += 1
        if self._store is not None:
            self._store.record_cf_account_error(acc.account_id)

    def mark_exhausted(self, account_or_id: PoolAccount | str) -> None:
        """علامت‌گذاری حساب به‌عنوان `exhausted_today` تا پایان روز جاری UTC."""
        acc = self._find(account_or_id)
        if acc is None:
            return
        today = self.utc_day()
        acc.status = "exhausted_today"
        acc.exhausted_day = today
        if self._store is not None:
            self._store.mark_cf_account_exhausted(acc.account_id, today)
        log.warning(
            "⚠️ سهمیه‌ی روزانه حساب Cloudflare #%s (%s) تمام شد؛ وضعیت=exhausted_today تا 00:00 UTC.",
            acc.index,
            acc.masked_id,
        )

    def set_status(self, account_or_id: PoolAccount | str, status: str) -> None:
        """تغییر وضعیت حساب (`active` | `exhausted_today` | `disabled`)."""
        if status not in VALID_ACCOUNT_STATUSES:
            raise ValueError(f"وضعیت نامعتبر حساب: {status}")
        acc = self._find(account_or_id)
        if acc is None:
            return
        ex_day = self.utc_day() if status == "exhausted_today" else None
        acc.status = status
        acc.exhausted_day = ex_day
        if self._store is not None:
            self._store.set_cf_account_status(acc.account_id, status, ex_day)

    def reset_exhausted(self) -> int:
        """ریست دستی وضعیت `exhausted_today` همه‌ی حساب‌ها به `active`."""
        count = 0
        for acc in self._accounts:
            if acc.status == "exhausted_today":
                acc.status = "active"
                acc.exhausted_day = None
                count += 1
        if self._store is not None:
            db_count = self._store.reset_all_exhausted_cf_accounts()
            count = max(count, db_count)
        log.info("🔄 ریست دستی انجام شد: %s حساب از حالت exhausted_today به active بازگشت.", count)
        return count

    def all_secrets(self) -> tuple[str, ...]:
        """تمام مقادیر محرمانه (توکن‌ها و شناسه‌های کامل) جهت پاکسازی متن خطا/لاگ."""
        secrets: list[str] = []
        for acc in self._accounts:
            if acc.api_token:
                secrets.append(acc.api_token)
            if acc.account_id:
                secrets.append(acc.account_id)
        return tuple(secrets)

    def summary(self) -> list[dict[str, Any]]:
        """خلاصه‌ی امن و بدون توکن خام از وضعیت همه‌ی حساب‌ها (برای دستور `ai accounts`)."""
        self.refresh_utc_day()
        items: list[dict[str, Any]] = []
        for acc in self._accounts:
            items.append(
                {
                    "index": acc.index,
                    "account_masked": acc.masked_id,
                    "account_fingerprint": token_fingerprint(acc.account_id),
                    "token_fingerprint": acc.token_fp,
                    "token_length": len(acc.api_token),
                    "status": acc.status,
                    "exhausted_day": acc.exhausted_day,
                    "request_count": acc.request_count,
                    "error_count": acc.error_count,
                    "last_used_at": acc.last_used_at,
                }
            )
        return items


def error_codes(payload: Any) -> list:
    """کدهای خطای موجود در بدنه‌ی پاسخ Cloudflare."""
    if not isinstance(payload, dict):
        return []
    codes = []
    for err in payload.get("errors") or []:
        code = err.get("code") if isinstance(err, dict) else None
        if code is not None:
            codes.append(code)
    return codes


def error_messages(payload: Any) -> list:
    """پیام‌های خطای موجود در بدنه‌ی پاسخ Cloudflare (بدون هیچ داده‌ی حساس)."""
    if not isinstance(payload, dict):
        return []
    messages = []
    for err in payload.get("errors") or []:
        if isinstance(err, dict):
            message = err.get("message")
            if message:
                messages.append(str(message))
        elif err:
            messages.append(str(err))
    return messages


def hint_for(codes, status: Optional[int] = None, message: str = "") -> str:
    """راهنمای دقیق بر اساس کد خطا یا وضعیت HTTP (برای لاگ و ابزار تشخیصی)."""
    for code in codes or []:
        if code in ERROR_HINTS:
            return ERROR_HINTS[code]
    if status == 429:
        if any(code in CAPACITY_ERROR_CODES for code in codes or []):
            return ERROR_HINTS[3040]
        return ERROR_HINTS[3036]
    if status in (401, 403):
        return "دسترسی رد شد: مجوز Workers AI توکن و درستی Account ID را بررسی کنید."
    if status == 400:
        return "درخواست یا توکن مشکل دارد (کد 10000 معمولاً یعنی خطای احراز هویت)."
    if message and any(word in message.lower() for word in QUOTA_KEYWORDS):
        return ERROR_HINTS[3036]
    return ""


def _is_quota_error(status: int, codes: list, message: str) -> bool:
    """آیا این خطا به‌معنای «سهمیه‌ی روزانه تمام شد» است؟

    نکته‌ی مهم: کد 3040 (Out of capacity) هم HTTP 429 برمی‌گرداند ولی **سهمیه نیست**؛
    اگر آن را سهمیه حساب کنیم، یک خطای موقت کل روز را می‌بندد. پس فقط کدهای شناخته‌شده
    یا 429 بدون کد (و کلیدواژه‌های سهمیه) سهمیه حساب می‌شوند.
    """
    if CAPACITY_ERROR_CODES.intersection(codes or []):
        return False
    if QUOTA_ERROR_CODES.intersection(codes or []):
        return True
    if any(word in (message or "").lower() for word in QUOTA_KEYWORDS):
        return True
    if status == 429 and not (codes or []):
        return True
    return False


def extract_text(payload: dict) -> Optional[str]:
    """استخراج متن پاسخ از قالب‌های ممکن Workers AI."""
    result = payload.get("result")

    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if isinstance(result.get("response"), str):
            return result["response"]

        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] or {}
            message = first.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):       # سبک parts: [{"type":"text","text":"..."}]
                parts = [
                    part.get("text", "") for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                ]
                if parts:
                    return "".join(parts)
            if isinstance(first.get("text"), str):
                return first["text"]
    return None


def extract_reasoning(payload: dict) -> Optional[str]:
    """متن «استدلال/thinking» مدل (اگر سرور جدا برگرداند): reasoning_content یا reasoning."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        message = (choices[0] or {}).get("message") or {}
        for key in ("reasoning_content", "reasoning"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


def extract_finish_reason(payload: dict) -> Optional[str]:
    result = payload.get("result")
    if isinstance(result, dict):
        choices = result.get("choices")
        if isinstance(choices, list) and choices:
            reason = (choices[0] or {}).get("finish_reason")
            if isinstance(reason, str):
                return reason
    return None


def extract_usage(payload: dict) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        return result["usage"]
    if isinstance(payload.get("usage"), dict):
        return payload["usage"]
    return None


class CloudflareAI:
    """ارتباط با Workers AI — با پشتیبانی یکپارچه از تک‌حساب و چرخش چند حساب (`AccountPool`)."""

    #: سقف بودجه‌ای که در حالت «پاسخ خالی مدل استدلالی» یک‌بار امتحان می‌شود
    MAX_RETRY_BUDGET = 4096

    def __init__(
        self,
        account_id: Optional[str] = None,
        api_token: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = 45.0,
        max_output_tokens: int = 1024,
        session=None,
        max_retry_budget: int = MAX_RETRY_BUDGET,
        pool: Optional[AccountPool] = None,
        accounts: Optional[Any] = None,
        store=None,
        now_provider: Optional[Callable[..., _dt.datetime]] = None,
    ):
        self.account_id = (account_id or "").strip()
        self.api_token = (api_token or "").strip()
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.max_retry_budget = max_retry_budget
        self.last_status: Optional[APIStatus] = None     # آخرین وضعیت غیرحساس تماس
        self._session = session          # قابل تزریق در تست (aiohttp.ClientSession)
        self._own_session = None

        if pool is not None:
            self.pool = pool
        elif accounts is not None:
            self.pool = AccountPool(accounts=accounts, store=store, now_provider=now_provider)
        else:
            initial = (
                [(self.account_id, self.api_token)]
                if not self._is_placeholder(self.account_id) and not self._is_placeholder(self.api_token)
                else []
            )
            self.pool = AccountPool(accounts=initial, store=store, now_provider=now_provider)

        # حفظ سازگاری عقب‌رو با صفت‌های تک‌حساب در صورت ساخت از روی AccountPool
        if not self.account_id and self.pool.configured:
            first_acc = self.pool._accounts[0]
            self.account_id = first_acc.account_id
            self.api_token = first_acc.api_token

    @classmethod
    def from_config(
        cls,
        cfg,
        *,
        pool: Optional[AccountPool] = None,
        store=None,
        session=None,
        now_provider: Optional[Callable[..., _dt.datetime]] = None,
    ) -> "CloudflareAI":
        resolved_pool = pool or AccountPool.from_config(
            cfg, store=store, now_provider=now_provider
        )
        return cls(
            account_id=getattr(cfg, "cloudflare_account_id", None),
            api_token=getattr(cfg, "cloudflare_api_token", None),
            model=getattr(cfg, "ai_model", DEFAULT_MODEL),
            timeout=getattr(cfg, "ai_timeout", 45.0),
            max_output_tokens=getattr(cfg, "ai_max_output_tokens", 1024),
            session=session,
            pool=resolved_pool,
        )

    def __repr__(self) -> str:
        return (
            f"CloudflareAI(model={self.model!r}, configured={self.configured}, "
            f"pool={self.pool!r})"
        )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _is_placeholder(value: str) -> bool:
        """جای‌خالی‌های نمونه (مثل your_cloudflare_account_id) را واقعی حساب نکن."""
        low = (value or "").strip().lower()
        return not low or low.startswith(("your_", "your-", "xxx", "changeme", "<"))

    @property
    def configured(self) -> bool:
        """آیا حداقل یک حساب معتبر Cloudflare پیکربندی شده است؟"""
        return self.pool.configured

    def url(self, model: Optional[str] = None, account: Optional[PoolAccount] = None) -> str:
        m = model or self.model
        target = account or self.pool.get_active_account()
        acc_id = target.account_id if target is not None else self.account_id
        return f"{API_BASE}/{acc_id}/ai/run/{m}"

    def url_masked(self, model: Optional[str] = None) -> str:
        """همان endpoint با حساب پوشیده — برای چاپ در لاگ/ابزار تشخیصی."""
        m = model or self.model
        return f"{API_BASE}/<ACCOUNT_ID>/ai/run/{m}"

    async def _get_session(self):
        if self._session is not None:
            return self._session, False
        try:
            import aiohttp  # کتابخانه‌ی پیش‌نیاز splusthon است
        except ImportError as exc:  # pragma: no cover
            raise AIError("پکیج aiohttp نصب نیست (pip install aiohttp)") from exc
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        self._own_session = session
        return session, True

    async def close(self) -> None:
        if self._own_session is not None:
            await self._own_session.close()
            self._own_session = None

    def _all_secrets(self, account: Optional[PoolAccount] = None) -> tuple[str, ...]:
        secrets = list(self.pool.all_secrets())
        if account is not None:
            secrets.extend([account.api_token, account.account_id])
        if self.api_token:
            secrets.append(self.api_token)
        if self.account_id:
            secrets.append(self.account_id)
        return tuple(s for s in secrets if s)

    # ------------------------------------------------------------------ requests
    async def _post(
        self,
        messages: list[dict],
        budget: int,
        field_name: str,
        model: Optional[str] = None,
        *,
        account: Optional[PoolAccount] = None,
    ):
        """یک تماس خام با API روی حساب مشخص‌شده از `AccountPool`."""
        active_acc = account or self.pool.get_active_account()
        if active_acc is None:
            raise AIQuotaExceeded(QUOTA_EXHAUSTED_MESSAGE)

        body: dict[str, Any] = {"messages": messages, field_name: int(budget)}
        headers = {
            "Authorization": f"Bearer {active_acc.api_token}",
            "Content-Type": "application/json",
        }
        secrets = self._all_secrets(active_acc)
        target_url = self.url(model, account=active_acc)

        # ثبت شمارنده‌ی درخواست و زمان آخرین استفاده برای همین حساب
        self.pool.record_request(active_acc)

        loop = asyncio.get_event_loop()
        started = loop.time()
        session, owns = await self._get_session()
        try:
            async with session.post(target_url, json=body, headers=headers) as resp:
                raw = await resp.text()
                status = resp.status
        except asyncio.TimeoutError as exc:
            self.pool.record_error(active_acc)
            raise AIError(f"Timeout پس از {self.timeout} ثانیه (کلاینت)", retryable=True) from exc
        except AIError:
            self.pool.record_error(active_acc)
            raise
        except Exception as exc:  # noqa: BLE001 — خطای شبکه/اتصال
            self.pool.record_error(active_acc)
            safe_err = _scrub(f"{type(exc).__name__}: {exc}", *secrets)
            raise AIError(safe_err, retryable=True) from exc
        finally:
            if owns:
                await self.close()

        elapsed = max(0.0, loop.time() - started)
        raw_safe = _scrub(raw, *secrets)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            info = APIStatus(status=status, codes=[], messages=[], ok=status < 400,
                             elapsed=elapsed, field_used=field_name)
            self.last_status = info
            self.pool.record_error(active_acc)
            if _is_quota_error(status, [], raw):
                raise AIQuotaExceeded(f"{QUOTA_EXHAUSTED_MESSAGE} (HTTP {status})")
            if status >= 400:
                raise AIError(f"HTTP {status}: {raw_safe[:200]}")
            raise AIError(f"پاسخ نامعتبر از سرور (بدنه‌ی غیر-JSON): {raw_safe[:200]}")

        codes = error_codes(payload)
        messages_ = [_scrub(m, *secrets) for m in error_messages(payload)]
        info = APIStatus(status=status, codes=codes, messages=messages_,
                         ok=(status < 400 and payload.get("success") is not False),
                         elapsed=elapsed, field_used=field_name)
        self.last_status = info

        if not info.ok:
            self.pool.record_error(active_acc)
            joined = " | ".join(messages_)
            hint = hint_for(codes, status, joined)
            if _is_quota_error(status, codes, joined):
                raise AIQuotaExceeded(f"{QUOTA_EXHAUSTED_MESSAGE} | HTTP {status}")
            # خطاهای موقت (قابل تلاش مجدد روی همان حساب بدون سوزاندن سهمیه):
            #   * 3040 (ظرفیت موقتاً پر است) / 3007 / 3008 / خطاهای سرور
            #   * 5xx (سرور)
            #   * 408 (درخواست timeout)
            #   * 429 غیرسهمیه (rate limit موقت)
            retryable = bool(
                RETRYABLE_ERROR_CODES.intersection(codes or [])
                or (status and status >= 500)
                or status == 408
                or (status == 429 and not _is_quota_error(status, codes, joined))
            )
            # خطاهای قطعی (احراز هویت، مدل نامعتبر مثل 5007، درخواست بزرگ، پلن پولی)
            if AUTH_ERROR_CODES.intersection(codes or []) or PAID_PLAN_ERROR_CODES.intersection(codes or []) \
                    or MODEL_ERROR_CODES.intersection(codes or []) or REQUEST_ERROR_CODES.intersection(codes or []):
                retryable = False
            detail = f"HTTP {status} | کدها: {codes or '—'} | {joined or raw_safe[:160]}"
            if hint:
                detail += f" | راهنما: {hint}"
            raise AIError(_scrub(detail, *secrets), retryable=retryable)
        return info, payload

    # --------------------------------------------------------------------- chat
    async def chat(
        self,
        messages: list[dict],
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        retry_on_empty: bool = True,
        max_retries: int = 0,
        base_delay: float = 1.2,
    ) -> AIResponse:
        """ارسال مکالمه به مدل با انتخاب خودکار حساب فعال و چرخش فوری هنگام اتمام سهمیه.

        رفتار چرخش:
          * همیشه اولین حساب `active` از `AccountPool` انتخاب می‌شود.
          * در صورت خطای سهمیه (HTTP 429، کدهای 4006/3036/3037 یا `daily free allocation`)،
            حساب فعلی تا `00:00 UTC` به حالت `exhausted_today` می‌رود و بلافاصله حساب فعال بعدی
            امتحان می‌شود.
          * در صورت تمام شدن همه‌ی حساب‌ها، `AIQuotaExceeded` با پیام استاندارد فارسی پرتاب می‌شود.
          * در صورت خطاهای غیرسهمیه‌ای (مثل 3040 ظرفیت موقت یا 5007 مدل نامعتبر)، حساب exhausted
            نمی‌شود و مطابق منطق retry روی همان حساب مدیریت می‌گردد.
        """
        if not self.configured:
            raise AIConfigError(
                "CLOUDFLARE_ACCOUNT_ID یا CLOUDFLARE_API_TOKEN تنظیم نشده است (.env)"
            )

        budget = int(max_tokens or self.max_output_tokens)
        attempts = max(1, int(max_retries) + 1)
        active_model = model

        while True:
            account = self.pool.get_active_account()
            if account is None:
                log.warning(
                    "تمام حساب‌های Cloudflare در استخر به سقف سهمیه‌ی روزانه رسیده‌اند (تعداد کل=%s).",
                    len(self.pool._accounts),
                )
                raise AIQuotaExceeded(QUOTA_EXHAUSTED_MESSAGE)

            rotated_to_next = False
            last_error: Optional[BaseException] = None

            for attempt in range(attempts):
                try:
                    log.debug(
                        "ارسال درخواست به Cloudflare با حساب #%s (%s)...",
                        account.index,
                        account.masked_id,
                    )
                    return await self._chat_once(
                        messages,
                        budget,
                        model=active_model,
                        retry_on_empty=retry_on_empty,
                        account=account,
                    )
                except AIQuotaExceeded:
                    self.pool.mark_exhausted(account)
                    next_acc = self.pool.get_active_account()
                    if next_acc is not None:
                        log.info(
                            "🔀 چرخش خودکار از حساب #%s (%s) به حساب #%s (%s)",
                            account.index,
                            account.masked_id,
                            next_acc.index,
                            next_acc.masked_id,
                        )
                        rotated_to_next = True
                        break
                    raise AIQuotaExceeded(QUOTA_EXHAUSTED_MESSAGE)
                except AIConfigError:
                    raise
                except AIError as exc:
                    last_error = exc
                    # اگر مدل درخواستی با خطای ناموجود بودن یا پلن پولی روبرو شد، به مدل پیش‌فرض سوئیچ کن
                    if active_model and active_model != self.model:
                        err_msg = str(exc).lower()
                        if any(k in err_msg for k in ("not found", "invalid model", "paid", "5035", "5007", "3042")):
                            log.warning(
                                "مدل %s روی حساب #%s (%s) در دسترس نیست؛ سوئیچ به مدل پایدار %s",
                                active_model,
                                account.index,
                                account.masked_id,
                                self.model,
                            )
                            active_model = self.model
                            continue
                    if not exc.retryable or attempt >= attempts - 1:
                        raise
                    delay = min(base_delay * (2 ** attempt), 8.0)
                    log.warning(
                        "خطای موقت AI روی حساب #%s (%s) (تلاش %s/%s): %s → ری‌ترای پس از %.1fs",
                        account.index,
                        account.masked_id,
                        attempt + 1,
                        attempts,
                        _scrub(str(exc), *self._all_secrets(account)),
                        delay,
                    )
                    await asyncio.sleep(delay)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if attempt >= attempts - 1:
                        raise
                    delay = min(base_delay * (2 ** attempt), 8.0)
                    log.warning(
                        "استثنای نامنتظره AI روی حساب #%s (%s) (تلاش %s/%s): %s → ری‌ترای پس از %.1fs",
                        account.index,
                        account.masked_id,
                        attempt + 1,
                        attempts,
                        _scrub(str(exc), *self._all_secrets(account)),
                        delay,
                    )
                    await asyncio.sleep(delay)

            if rotated_to_next:
                continue

            assert last_error is not None
            raise last_error  # pragma: no cover

    async def _chat_once(
        self,
        messages,
        budget,
        *,
        model: Optional[str] = None,
        retry_on_empty: bool,
        account: Optional[PoolAccount] = None,
    ) -> AIResponse:
        """یک تلاش کامل برای ارسال به API روی یک حساب مشخص (با هندل تغییر پارامتر و بودجه)."""
        # ۱) تلاش با پارامتر مدرن؛ در صورت رد شدن، نام قدیمی
        try:
            _info, raw = await self._post(
                messages, budget, "max_completion_tokens", model=model, account=account
            )
            field_name = "max_completion_tokens"
        except AIError as exc:
            message = str(exc).lower()
            field_rejected = (
                ("max_completion_tokens" in message and "invalid" in message)
                or ("max_tokens" in message
                    and any(w in message for w in ("invalid", "unknown", "unexpected", "unsupported")))
            )
            if not field_rejected:
                raise
            log.warning("پارامتر max_completion_tokens پذیرفته نشد؛ تلاش با max_tokens")
            _info, raw = await self._post(
                messages, budget, "max_tokens", model=model, account=account
            )
            field_name = "max_tokens"

        text = extract_text(raw)
        finish = extract_finish_reason(raw)
        reasoning = extract_reasoning(raw)
        usage = extract_usage(raw)

        # ۲) پاسخ خالی با پایان «length» ⇒ بودجه صرف استدلال/طولانی‌تر خروجی شده
        if (not (text or "").strip()) and finish == "length" and retry_on_empty:
            bigger = min(max(budget * 2, 256), int(self.max_retry_budget))
            if bigger > budget:
                log.info("پاسخ خالی با finish_reason=length؛ تلاش دوباره با بودجه‌ی %s", bigger)
                _info, raw = await self._post(
                    messages, bigger, field_name, model=model, account=account
                )
                text = extract_text(raw)
                finish = extract_finish_reason(raw)
                reasoning = extract_reasoning(raw)
                usage = extract_usage(raw)
                budget = bigger

        if (text or "").strip():
            # استخراج استدلال از داخل تگ think در صورت نبود reasoning اختصاصی
            if not reasoning and re.search(r"(?is)<\s*think\s*>", text):
                m = re.search(r"(?is)<\s*(?:think|thought)\s*>(.*?)<\s*/\s*(?:think|thought)\s*>", text)
                if m:
                    reasoning = m.group(1).strip()
            # حذف تگ‌های think از متن پاسخ نهایی
            cleaned_text = re.sub(r"(?is)<\s*(?:think|thought)\s*>.*?<\s*/\s*(?:think|thought)\s*>", "", text).strip()
            if not cleaned_text:
                parts = re.split(r"(?is)<\s*/\s*(?:think|thought)\s*>", text)
                if len(parts) > 1 and parts[-1].strip():
                    cleaned_text = parts[-1].strip()
                else:
                    cleaned_text = re.sub(r"(?is)^\s*<\s*(?:think|thought)\s*>", "", text).strip()
            return AIResponse(text=cleaned_text or text.strip(), usage=usage, finish_reason=finish,
                              reasoning=reasoning)

        # ۳) خالی ولی مدل استدلال تولید کرده و «طبیعی» تمام شده ⇒ همان استدلال
        if (reasoning or "").strip() and finish != "length":
            return AIResponse(text=reasoning.strip(), usage=usage, finish_reason=finish,
                              reasoning=reasoning, used_reasoning=True)

        # ۴) واقعاً چیزی تولید نشده → خطای قابل‌تلاش (پاسخ خالی سرور)
        if finish == "length":
            raise AIError(
                f"مدل پاسخی متنی تولید نکرد: بودجه‌ی خروجی ({budget} توکن) کافی نبود "
                f"(finish_reason=length). مقدار ACOD_AI_MAX_OUTPUT_TOKENS را بالا ببرید.",
                retryable=True,
            )
        raise AIError(
            f"پاسخ خالی از مدل (finish_reason={finish or 'نامشخص'}"
            f"{'، استدلال داشت' if reasoning else ''}).",
            retryable=True,
        )


class GroqAI:
    """کلاینت رسمی و مستقیم Groq API (سازگار با پروتکل OpenAI Chat Completions).

    از endpoint استاندارد: https://api.groq.com/openai/v1/chat/completions استفاده می‌کند.
    """

    API_BASE = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        timeout: float = 40.0,
        session=None,
    ):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._session = session
        self._own_session = None

    @property
    def configured(self) -> bool:
        low = (self.api_key or "").strip().lower()
        return bool(low and not low.startswith(("your_", "your-", "xxx", "changeme", "<")))

    def is_available(self) -> bool:
        return self.configured

    async def _get_session(self):
        if self._session is not None:
            return self._session, False
        import aiohttp
        session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        self._own_session = session
        return session, True

    async def close(self) -> None:
        if self._own_session is not None:
            await self._own_session.close()
            self._own_session = None

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str,
        max_tokens: Optional[int] = 2048,
        temperature: float = 0.6,
        max_retries: int = 0,
        base_delay: float = 1.0,
    ) -> AIResponse:
        """ارسال پیام‌ها به Groq API."""
        if not self.configured:
            raise AIConfigError("کلید GROQ_API_KEY تنظیم نشده است.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": int(max_tokens or 2048),
            "temperature": float(temperature),
        }

        session, owns = await self._get_session()
        try:
            async with session.post(self.API_BASE, json=payload, headers=headers) as resp:
                status = resp.status
                raw = await resp.text()
        except asyncio.TimeoutError as exc:
            raise AIError(f"Groq Timeout ({self.timeout}s)", retryable=True) from exc
        except Exception as exc:
            raise AIError(_scrub(f"Groq Network Error: {exc}", self.api_key), retryable=True) from exc
        finally:
            if owns:
                await self.close()

        if status == 429:
            raise AIQuotaExceeded(f"Groq Rate Limit/Quota Reached (HTTP 429): {_scrub(raw[:150], self.api_key)}")
        if status in (401, 403):
            raise AIConfigError(f"خطای دسترسی یا کلید نامعتبر Groq (HTTP {status})")

        try:
            data = json.loads(raw)
        except Exception as exc:
            raise AIError(f"پاسخ نامعتبر از سرور Groq (HTTP {status}): {_scrub(raw[:120], self.api_key)}") from exc

        if status >= 400:
            err_msg = ""
            if isinstance(data, dict) and "error" in data:
                err_msg = data["error"].get("message", "")
            raise AIError(f"خطای Groq (HTTP {status}): {_scrub(err_msg or raw[:120], self.api_key)}")

        choices = data.get("choices") or []
        if not choices:
            raise AIError("پاسخ خالی از Groq دریافت شد.")

        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        usage_data = data.get("usage")

        return AIResponse(
            text=content.strip(),
            usage=usage_data,
            finish_reason=choices[0].get("finish_reason"),
        )
