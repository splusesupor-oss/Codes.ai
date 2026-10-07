"""
کلاینت واقعی Cloudflare Workers AI (REST API).

endpoint واقعی (تأییدشده از مستندات Cloudflare):

    POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}
    Authorization: Bearer {API_TOKEN}
    body: {"messages": [{"role": "...", "content": "..."}], "max_tokens": N}

قالب پاسخ موفّق (مدل‌های متنی):
    {"result": {"response": "..."}, "success": true, "errors": [], "messages": []}
بعضی مدل‌ها قالب سازگار با OpenAI برمی‌گردانند:
    {"result": {"choices": [{"message": {"content": "..."}}]}, "success": true, ...}
هر دو حالت پشتیبانی می‌شوند.

خطای سهمیه (بر اساس گزارش‌های واقعی Cloudflare):
    HTTP 429 با errors[0].code = 4006 (و در مستندات: 3036)
    پیام: "you have used up your daily free allocation of 10,000 neurons ..."
    ⇒ در این حالت AIQuotaExceeded پرتاب می‌شود تا سرویس، سهمیه‌ی روز را تمام‌شده علامت بزند.

هیچ Token یا Secretی در کد نیست؛ همه از متغیرهای محیطی/.env خوانده می‌شوند.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger("acod.ai.client")

API_BASE = "https://api.cloudflare.com/client/v4/accounts"
DEFAULT_MODEL = "@cf/zai-org/glm-4.7-flash"

# کدهایی که Cloudflare برای «تمام شدن سهمیه روزانه» برمی‌گرداند
QUOTA_ERROR_CODES = {3036, 4006, 3037}
QUOTA_KEYWORDS = (
    "neurons",
    "daily free allocation",
    "quota",
    "rate limit",
    "too many requests",
    "limit exceeded",
)


class AIError(Exception):
    """خطای عمومی سرویس هوش مصنوعی."""


class AIQuotaExceeded(AIError):
    """سهمیه‌ی روزانه‌ی Cloudflare (Neurons) تمام شده است."""


class AIConfigError(AIError):
    """تنظیمات ناقص (Account ID یا API Token تنظیم نشده)."""


@dataclass
class AIResponse:
    text: str
    usage: Optional[dict] = None


def _looks_like_quota_error(status: int, errors: list, message: str) -> bool:
    if status == 429:
        return True
    for err in errors or []:
        code = err.get("code") if isinstance(err, dict) else None
        if code in QUOTA_ERROR_CODES:
            return True
        text = str(err.get("message", "")) if isinstance(err, dict) else str(err)
        if any(word in text.lower() for word in QUOTA_KEYWORDS):
            return True
    return any(word in (message or "").lower() for word in QUOTA_KEYWORDS)


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
            if isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    return None


class CloudflareAI:
    """ارتباط با Workers AI — پایه‌ی همه‌ی درخواست‌های مدل."""

    def __init__(
        self,
        account_id: Optional[str],
        api_token: Optional[str],
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = 45.0,
        max_output_tokens: int = 256,
        session=None,
    ):
        self.account_id = (account_id or "").strip()
        self.api_token = (api_token or "").strip()
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
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

    def url(self) -> str:
        return f"{API_BASE}/{self.account_id}/ai/run/{self.model}"

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

    # --------------------------------------------------------------------- chat
    async def chat(self, messages: list[dict], *, max_tokens: Optional[int] = None) -> AIResponse:
        """ارسال مکالمه به مدل و برگرداندن پاسخ متنی."""
        if not self.configured:
            raise AIConfigError(
                "CLOUDFLARE_ACCOUNT_ID یا CLOUDFLARE_API_TOKEN تنظیم نشده است (.env)"
            )

        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": int(max_tokens or self.max_output_tokens),
        }
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

        session, owns = await self._get_session()
        try:
            async with session.post(self.url(), json=body, headers=headers) as resp:
                raw = await resp.text()
                status = resp.status
        except asyncio.TimeoutError as exc:
            raise AIError(f"Timeout پس از {self.timeout} ثانیه") from exc
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001 — خطای شبکه/اتصال
            raise AIError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            if owns:
                await self.close()

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # بدنه‌ی غیر-JSON: برای 429 باز هم «سهمیه تمام شده» در نظر گرفته می‌شود
            if _looks_like_quota_error(status, [], raw):
                raise AIQuotaExceeded(f"HTTP {status} | {raw[:200]}")
            if status >= 400:
                raise AIError(f"HTTP {status}: {raw[:200]}")
            raise AIError(f"پاسخ نامعتبر از سرور: {raw[:200]}")

        errors = payload.get("errors") or []
        error_message = errors[0].get("message") if errors and isinstance(errors[0], dict) else ""

        if status >= 400 or payload.get("success") is False:
            if _looks_like_quota_error(status, errors, error_message):
                raise AIQuotaExceeded(
                    f"HTTP {status} | {error_message or 'quota/limit reached'}"
                )
            raise AIError(f"HTTP {status} | {error_message or raw[:200]}")

        text = extract_text(payload)
        if not text or not text.strip():
            raise AIError("پاسخ خالی از مدل")

        usage = payload.get("result", {}).get("usage") if isinstance(payload.get("result"), dict) else None
        return AIResponse(text=text.strip(), usage=usage)
