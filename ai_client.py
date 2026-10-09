"""
کلاینت واقعی Cloudflare Workers AI (REST API).

endpoint واقعی (تأییدشده از مستندات رسمی Cloudflare):

    POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}
    Authorization: Bearer {API_TOKEN}
    body: {"messages": [{"role": "...", "content": "..."}], "max_completion_tokens": N}

نکته‌های مستندشده برای مدل `@cf/zai-org/glm-4.7-flash`:
  * پارامتر `max_tokens` **deprecated** است و جایش `max_completion_tokens` توصیه شده؛
    این کلاینت اولی را می‌فرستد و اگر سرور رد کرد، یک‌بار با دومی تلاش می‌کند.
  * این مدل **Reasoning** است و بودجه‌ی توکن بین «استدلال» و «پاسخ» مشترک است؛
    با بودجه‌ی کم، پاسخ می‌تواند HTTP 200 با `content` خالی و `finish_reason="length"`
    باشد. در این حالت کلاینت یک‌بار با بودجه‌ی دوبرابر تلاش می‌کند.

قالب پاسخ موفّق (این مدل، سبک OpenAI):
    {"result": {"choices": [{"message": {"content": "..."}}]}, "success": true, ...}
قالب قدیمی‌تر (برخی مدل‌ها):
    {"result": {"response": "..."}, "success": true, ...}
هر دو پشتیبانی می‌شوند.

کدهای خطای رسمی (Workers AI → Errors):
    5035/403 «نیاز به پلن Workers Paid» · 5007/400 «مدل وجود ندارد» ·
    3042/404 «Invalid model ID» · 3006/413 «Request too large» ·
    3007/408 «Timeout» · 3008/408 «Aborted» ·
    3036/429 «سهمیه‌ی روزانه‌ی رایگان (۱۰٬۰۰۰ نورون) تمام شده» ·
    3040/429 «ظرفیت موقتاً پر است» (**این یکی سهمیه نیست**).
    کدهای احراز هویت که در عمل دیده می‌شوند: 10000 (Authentication error) و 9109
    (Unauthorized to access requested resource).

هیچ Token یا Secretی در کد نیست؛ همه از متغیرهای محیطی/.env خوانده می‌شوند و در هیچ
پیام خطایی چاپ نمی‌شوند.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("acod.ai.client")

API_BASE = "https://api.cloudflare.com/client/v4/accounts"
DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct-fp8-fast"

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
        if secret and len(secret) >= 6:
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
    """سهمیه‌ی روزانه‌ی Cloudflare (Neurons) تمام شده است — تلاش مجدد بی‌فایده است."""


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
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        return result["usage"]
    return None


class CloudflareAI:
    """ارتباط با Workers AI — پایه‌ی همه‌ی درخواست‌های مدل."""

    #: سقف بودجه‌ای که در حالت «پاسخ خالی مدل استدلالی» یک‌بار امتحان می‌شود
    MAX_RETRY_BUDGET = 4096

    def __init__(
        self,
        account_id: Optional[str],
        api_token: Optional[str],
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = 45.0,
        max_output_tokens: int = 1024,
        session=None,
        max_retry_budget: int = MAX_RETRY_BUDGET,
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

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _is_placeholder(value: str) -> bool:
        """جای‌خالی‌های نمونه (مثل your_cloudflare_account_id) را واقعی حساب نکن."""
        low = (value or "").strip().lower()
        return not low or low.startswith(("your_", "your-", "xxx", "changeme", "<"))

    @property
    def configured(self) -> bool:
        """آیا Account ID و Token موجود است؟"""
        return not self._is_placeholder(self.account_id) and not self._is_placeholder(self.api_token)

    def url(self, model: Optional[str] = None) -> str:
        m = model or self.model
        return f"{API_BASE}/{self.account_id}/ai/run/{m}"

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

    # ------------------------------------------------------------------ requests
    async def _post(
        self, messages: list[dict], budget: int, field_name: str, model: Optional[str] = None
    ):
        """یک تماس خام با API.

        Returns: ``(APIStatus, payload)`` — خطاهای سخت پرتاب می‌شوند.
        """
        body: dict[str, Any] = {"messages": messages, field_name: int(budget)}
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        loop = asyncio.get_event_loop()
        started = loop.time()
        session, owns = await self._get_session()
        try:
            async with session.post(self.url(model), json=body, headers=headers) as resp:
                raw = await resp.text()
                status = resp.status
        except asyncio.TimeoutError as exc:
            raise AIError(f"Timeout پس از {self.timeout} ثانیه (کلاینت)", retryable=True) from exc
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001 — خطای شبکه/اتصال
            raise AIError(f"{type(exc).__name__}: {exc}", retryable=True) from exc
        finally:
            if owns:
                await self.close()

        elapsed = max(0.0, loop.time() - started)
        raw_safe = _scrub(raw, self.api_token, self.account_id)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            info = APIStatus(status=status, codes=[], messages=[], ok=status < 400,
                             elapsed=elapsed, field_used=field_name)
            self.last_status = info
            if _is_quota_error(status, [], raw):
                raise AIQuotaExceeded(f"HTTP {status} | {raw_safe[:200]}")
            if status >= 400:
                raise AIError(f"HTTP {status}: {raw_safe[:200]}")
            raise AIError(f"پاسخ نامعتبر از سرور (بدنه‌ی غیر-JSON): {raw_safe[:200]}")

        codes = error_codes(payload)
        messages_ = [_scrub(m, self.api_token, self.account_id) for m in error_messages(payload)]
        info = APIStatus(status=status, codes=codes, messages=messages_,
                         ok=(status < 400 and payload.get("success") is not False),
                         elapsed=elapsed, field_used=field_name)
        self.last_status = info

        if not info.ok:
            joined = " | ".join(messages_)
            hint = hint_for(codes, status, joined)
            if _is_quota_error(status, codes, joined):
                raise AIQuotaExceeded(f"HTTP {status} | {joined or 'quota/limit reached'}")
            # خطاهای موقت (قابل تلاش مجدد):
            #   * 3040 / 3007 / 3008 / خطاهای ناشناخته‌ی Cloudflare
            #   * 5xx (سرور)
            #   * 408 (درخواست timeout)
            #   * 429 غیرسهمیه (rate limit موقت)
            retryable = bool(
                RETRYABLE_ERROR_CODES.intersection(codes or [])
                or (status and status >= 500)
                or status == 408
                or (status == 429 and not _is_quota_error(status, codes, joined))
            )
            # خطاهای قطعی (احراز هویت، مدل، درخواست بزرگ، نیاز به پلن پولی) — ری‌ترای بی‌فایده
            if AUTH_ERROR_CODES.intersection(codes or []) or PAID_PLAN_ERROR_CODES.intersection(codes or []) \
                    or MODEL_ERROR_CODES.intersection(codes or []) or REQUEST_ERROR_CODES.intersection(codes or []):
                retryable = False
            detail = f"HTTP {status} | کدها: {codes or '—'} | {joined or raw_safe[:160]}"
            if hint:
                detail += f" | راهنما: {hint}"
            raise AIError(detail, retryable=retryable)
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
        """ارسال مکالمه به مدل و برگرداندن پاسخ متنی.

        محافظت‌ها:
          * پارامتر مدرن `max_completion_tokens` (در صورت رد شدن، تلاش با `max_tokens`).
          * تلاش مجدد خودکار با backoff نمایی برای خطاهای موقت (شبکه، ظرفیت، 5xx، 408).
          * برای خطاهای قطعی (احراز هویت، مدل، درخواست بزرگ، سهمیه) ری‌ترای انجام نمی‌شود.
          * اگر پاسخ خالی با `finish_reason="length"` برگردد، یک‌بار با بودجه‌ی دوبرابر.
        """
        if not self.configured:
            raise AIConfigError(
                "CLOUDFLARE_ACCOUNT_ID یا CLOUDFLARE_API_TOKEN تنظیم نشده است (.env)"
            )

        budget = int(max_tokens or self.max_output_tokens)
        last_error: Optional[BaseException] = None
        attempts = max(1, int(max_retries) + 1)
        active_model = model

        for attempt in range(attempts):
            try:
                return await self._chat_once(
                    messages, budget, model=active_model, retry_on_empty=retry_on_empty
                )
            except AIQuotaExceeded:
                raise                     # سهمیه: قطعی، فوراً بالا برود
            except AIConfigError:
                raise                     # تنظیمات: قطعی
            except AIError as exc:
                last_error = exc
                # اگر مدل درخواستی با خطای ناموجود بودن یا پلن پولی روبرو شد، به مدل پیش‌فرض سوئیچ کن
                if active_model and active_model != self.model:
                    err_msg = str(exc).lower()
                    if any(k in err_msg for k in ("not found", "invalid model", "paid", "5035", "5007", "3042")):
                        log.warning("مدل %s در دسترس نیست؛ سوئیچ به مدل پایدار %s", active_model, self.model)
                        active_model = self.model
                        continue
                if not exc.retryable or attempt >= attempts - 1:
                    raise
                delay = min(base_delay * (2 ** attempt), 8.0)
                log.warning("خطای موقت AI (تلاش %s/%s): %s → ری‌ترای پس از %.1fs",
                            attempt + 1, attempts, exc, delay)
                await asyncio.sleep(delay)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= attempts - 1:
                    raise
                delay = min(base_delay * (2 ** attempt), 8.0)
                log.warning("استثنای نامنتظره AI (تلاش %s/%s): %s → ری‌ترای پس از %.1fs",
                            attempt + 1, attempts, exc, delay)
                await asyncio.sleep(delay)

        # به این خط نباید برسیم؛ برای type-checker
        assert last_error is not None
        raise last_error  # pragma: no cover

    async def _chat_once(
        self, messages, budget, *, model: Optional[str] = None, retry_on_empty: bool
    ) -> AIResponse:
        """یک تلاش کامل برای ارسال به API (با هندل تغییر پارامتر و بودجه)."""
        # ۱) تلاش با پارامتر مدرن؛ در صورت رد شدن، نام قدیمی
        try:
            _info, raw = await self._post(
                messages, budget, "max_completion_tokens", model=model
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
            _info, raw = await self._post(messages, budget, "max_tokens", model=model)
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
                _info, raw = await self._post(messages, bigger, field_name)
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
