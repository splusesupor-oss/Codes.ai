"""
تست‌های کلاینت Cloudflare Workers AI و خواندن تنظیمات از `.env` (کاملاً آفلاین).

هدف: تضمین درستی endpoint، پارس پاسخ‌ها و تشخیص «خطای سهمیه» بر اساس همان
قالب‌های واقعی Cloudflare — بدون هیچ درخواست شبکه‌ای.
"""

from __future__ import annotations

import asyncio
import json as _json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_module  # noqa: E402
from ai_client import (  # noqa: E402
    AIConfigError,
    AIError,
    AIQuotaExceeded,
    CloudflareAI,
    extract_reasoning,
    extract_text,
    hint_for,
)


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, status: int, payload: str):
        self.status = status
        self._payload = payload

    async def text(self) -> str:
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """session جعلی aiohttp که پاسخ‌های کاند (مطابق قالب واقعی) برمی‌گرداند.

    ``responses`` می‌تواند لیستی از ``(status, payload)`` باشد تا رفتار پشت‌سرهم
    (مثل تلاش دوباره) هم قابل تست باشد.
    """

    def __init__(self, *, status=200, payload=None, responses=None):
        self.status = status
        self.payload = payload if payload is not None else {
            "result": {"response": "سلام!"}, "success": True, "errors": [], "messages": []
        }
        self.responses = list(responses) if responses else None
        self.calls: list[dict] = []
        self.closed = False

    def post(self, url, json=None, headers=None):   # noqa: A002 — نام پارامتر خود aiohttp
        self.calls.append({"url": url, "body": json, "headers": headers})
        if self.responses:
            status, payload = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        else:
            status, payload = self.status, self.payload
        text = payload if isinstance(payload, str) else _json.dumps(payload)
        return FakeResponse(status, text)

    async def close(self):
        self.closed = True


class TestCloudflareClient(unittest.TestCase):
    def make(self, session, **kw):
        return CloudflareAI("acct-123", "tok-abc", "@cf/zai-org/glm-4.7-flash",
                            timeout=5, max_output_tokens=256, session=session, **kw)

    def test_url_and_model_are_real(self):
        client = self.make(FakeSession())
        self.assertEqual(
            client.url(),
            "https://api.cloudflare.com/client/v4/accounts/acct-123/ai/run/"
            "@cf/zai-org/glm-4.7-flash",
        )
        self.assertTrue(client.configured)

    def test_success_response_shape(self):
        session = FakeSession()
        result = run(self.make(session).chat([{"role": "user", "content": "سلام"}]))
        self.assertEqual(result.text, "سلام!")

        call = session.calls[0]
        self.assertTrue(call["headers"]["Authorization"].startswith("Bearer "))
        # پارامتر مدرن طبق مستندات مدل (max_tokens در مستندات deprecated شده است)
        self.assertEqual(call["body"]["max_completion_tokens"], 256)
        self.assertNotIn("max_tokens", call["body"])
        self.assertEqual(call["body"]["messages"][0]["content"], "سلام")

    def test_openai_style_response_shape(self):
        session = FakeSession(payload={
            "result": {"choices": [{"message": {"content": "پاسخ سبک OpenAI"}}]},
            "success": True, "errors": [], "messages": [],
        })
        self.assertEqual(run(self.make(session).chat([{"role": "user", "content": "x"}])).text,
                         "پاسخ سبک OpenAI")

    def test_quota_error_codes_are_detected(self):
        for code in (4006, 3036, 3037):
            with self.subTest(code=code):
                session = FakeSession(status=429, payload={
                    "errors": [{"message": "you have used up your daily free allocation "
                                           "of 10,000 neurons, please upgrade", "code": code}],
                    "success": False, "result": {}, "messages": [],
                })
                with self.assertRaises(AIQuotaExceeded):
                    run(self.make(session).chat([{"role": "user", "content": "x"}]))

    def test_http_429_is_treated_as_quota_even_without_code(self):
        session = FakeSession(status=429, payload="Too Many Requests")
        with self.assertRaises(AIQuotaExceeded):
            run(self.make(session).chat([{"role": "user", "content": "x"}]))

    def test_other_errors_raise_ai_error(self):
        session = FakeSession(status=400, payload={
            "errors": [{"message": "Invalid model name", "code": 7000}],
            "success": False, "result": {}, "messages": [],
        })
        with self.assertRaises(AIError) as ctx:
            run(self.make(session).chat([{"role": "user", "content": "x"}]))
        self.assertNotIsInstance(ctx.exception, AIQuotaExceeded)
        self.assertIn("Invalid model name", str(ctx.exception))

    def test_empty_response_is_error(self):
        session = FakeSession(payload={"result": {"response": "   "}, "success": True,
                                       "errors": [], "messages": []})
        with self.assertRaises(AIError):
            run(self.make(session).chat([{"role": "user", "content": "x"}]))

    def test_missing_credentials_raise_config_error(self):
        client = CloudflareAI("", "", "@cf/zai-org/glm-4.7-flash", session=FakeSession())
        self.assertFalse(client.configured)
        with self.assertRaises(AIConfigError):
            run(client.chat([{"role": "user", "content": "x"}]))

    def test_injected_session_is_not_closed(self):
        session = FakeSession()
        run(self.make(session).chat([{"role": "user", "content": "x"}]))
        self.assertFalse(session.closed)

    def test_extract_text_variants(self):
        self.assertEqual(extract_text({"result": {"response": "a"}}), "a")
        self.assertEqual(
            extract_text({"result": {"choices": [{"message": {"content": "b"}}]}}), "b"
        )
        self.assertEqual(extract_text({"result": "c"}), "c")
        self.assertIsNone(extract_text({"result": {}}))


class TestEnvFile(unittest.TestCase):
    def test_env_file_is_loaded_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "# نمونه\n"
                "CLOUDFLARE_ACCOUNT_ID=acct-from-env-file\n"
                "CLOUDFLARE_API_TOKEN='tok-from-env-file'\n"
                "ACOD_AI_MODEL=@cf/zai-org/glm-4.7-flash\n",
                encoding="utf-8",
            )
            import os

            old = {k: os.environ.get(k) for k in
                   ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "ACOD_AI_MODEL")}
            for key in old:
                os.environ.pop(key, None)
            try:
                loaded = config_module.load_env_file(env)
                self.assertEqual(loaded, 3)
                self.assertEqual(os.environ["CLOUDFLARE_ACCOUNT_ID"], "acct-from-env-file")
                self.assertEqual(os.environ["CLOUDFLARE_API_TOKEN"], "tok-from-env-file")

                # مقدار موجود در محیط نباید بازنویسی شود
                os.environ["CLOUDFLARE_API_TOKEN"] = "manual-token"
                config_module.load_env_file(env)
                self.assertEqual(os.environ["CLOUDFLARE_API_TOKEN"], "manual-token")

                cfg = config_module.Config.from_env()
                self.assertEqual(cfg.cloudflare_account_id, "acct-from-env-file")
                self.assertEqual(cfg.cloudflare_api_token, "manual-token")
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_missing_env_file_is_silent(self):
        self.assertEqual(config_module.load_env_file("/nonexistent/.env"), 0)

    def test_exact_variable_names_are_read_from_env_file(self):
        """نام‌ها باید دقیقاً CLOUDFLARE_ACCOUNT_ID و CLOUDFLARE_API_TOKEN باشند."""
        import os

        keys = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        old = {k: os.environ.get(k) for k in keys}
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "verify.env"
            env.write_text("CLOUDFLARE_ACCOUNT_ID=acct-verify\n"
                           "CLOUDFLARE_API_TOKEN=tok-verify\n", encoding="utf-8")
            for k in keys:
                os.environ.pop(k, None)
            try:
                self.assertEqual(config_module.load_env_file(env), 2)
                cfg = config_module.Config.from_env()
                self.assertEqual(cfg.cloudflare_account_id, "acct-verify")
                self.assertEqual(cfg.cloudflare_api_token, "tok-verify")
                self.assertTrue(CloudflareAI(
                    cfg.cloudflare_account_id, cfg.cloudflare_api_token, cfg.ai_model
                ).configured)
                self.assertIn("acct-verify", CloudflareAI(
                    cfg.cloudflare_account_id, cfg.cloudflare_api_token, cfg.ai_model
                ).url())
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_empty_values_in_env_file_are_safe(self):
        """مثل .env.example: اگر مقادیر خالی بمانند، ربات فقط «تنظیم نشده» می‌شود (بدون کرش)."""
        import os

        keys = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        old = {k: os.environ.get(k) for k in keys}
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "verify.env"
            env.write_text("CLOUDFLARE_ACCOUNT_ID=\nCLOUDFLARE_API_TOKEN=\n", encoding="utf-8")
            for k in keys:
                os.environ.pop(k, None)
            try:
                self.assertEqual(config_module.load_env_file(env), 2)
                cfg = config_module.Config.from_env()
                self.assertIsNone(cfg.cloudflare_account_id)
                self.assertIsNone(cfg.cloudflare_api_token)
                client = CloudflareAI("", "", cfg.ai_model)
                self.assertFalse(client.configured)
                with self.assertRaises(AIConfigError):
                    run(client.chat([{"role": "user", "content": "x"}]))
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_placeholder_values_are_not_treated_as_real(self):
        for value in ("your_cloudflare_account_id", "", "   ", "xxx-xxx", "<change>"):
            with self.subTest(value=repr(value)):
                self.assertFalse(CloudflareAI(value, value, "m").configured)
        self.assertTrue(CloudflareAI("acct-123", "tok-456", "m").configured)

    def test_config_constructor_reads_cloudflare_from_environment(self):
        """علت باگ قبلی: Config() فیلدهای Cloudflare را از env نمی‌خواند (فقط from_env این کار را می‌کرد)."""
        import os

        keys = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        old = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["CLOUDFLARE_ACCOUNT_ID"] = "dummy-account-for-test"
            os.environ["CLOUDFLARE_API_TOKEN"] = "dummy-token-for-test"

            cfg = config_module.Config()                       # ← سازنده‌ی ساده
            self.assertEqual(cfg.cloudflare_account_id, "dummy-account-for-test")
            self.assertEqual(cfg.cloudflare_api_token, "dummy-token-for-test")
            self.assertTrue(CloudflareAI(cfg.cloudflare_account_id,
                                         cfg.cloudflare_api_token, cfg.ai_model).configured)

            from_env = config_module.Config.from_env()          # ← رفتار قبلی هم حفظ شده
            self.assertEqual(from_env.cloudflare_account_id, "dummy-account-for-test")
            self.assertEqual(from_env.cloudflare_api_token, "dummy-token-for-test")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_config_constructor_is_none_when_environment_is_empty(self):
        import os

        keys = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        old = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys:
                os.environ.pop(k, None)
            cfg = config_module.Config()
            self.assertIsNone(cfg.cloudflare_account_id)
            self.assertIsNone(cfg.cloudflare_api_token)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_empty_string_counts_as_not_configured(self):
        import os

        keys = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        old = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["CLOUDFLARE_ACCOUNT_ID"] = "   "
            os.environ["CLOUDFLARE_API_TOKEN"] = ""
            cfg = config_module.Config()
            self.assertIsNone(cfg.cloudflare_account_id)
            self.assertIsNone(cfg.cloudflare_api_token)
            self.assertFalse(CloudflareAI(cfg.cloudflare_account_id or "",
                                          cfg.cloudflare_api_token or "", cfg.ai_model).configured)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_explicit_arguments_win_over_environment(self):
        import os

        keys = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        old = {k: os.environ.get(k) for k in keys}
        try:
            os.environ["CLOUDFLARE_ACCOUNT_ID"] = "env-account"
            os.environ["CLOUDFLARE_API_TOKEN"] = "env-token"
            cfg = config_module.Config(cloudflare_account_id="explicit-account")
            self.assertEqual(cfg.cloudflare_account_id, "explicit-account")
            self.assertEqual(cfg.cloudflare_api_token, "env-token")   # فقط فیلد داده‌شده override می‌شود
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_env_file_to_config_end_to_end(self):
        """سناریوی واقعی کاربر: load_env_file() سپس Config() باید «True True» بدهد."""
        import os

        keys = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
        old = {k: os.environ.get(k) for k in keys}
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / "dotenv"
            env.write_text(
                "# مقادیر ساختگی برای تست\n"
                "CLOUDFLARE_ACCOUNT_ID=cfg-check-account\n"
                "CLOUDFLARE_API_TOKEN=cfg-check-token\n",
                encoding="utf-8",
            )
            for k in keys:
                os.environ.pop(k, None)
            try:
                config_module.load_env_file(env)
                cfg = config_module.Config()        # ← همان الگویی که در گزارش کاربر خطا می‌داد
                self.assertTrue(bool(cfg.cloudflare_account_id))
                self.assertTrue(bool(cfg.cloudflare_api_token))
                self.assertTrue(CloudflareAI(cfg.cloudflare_account_id, cfg.cloudflare_api_token,
                                             cfg.ai_model).configured)
                # و سایر تنظیمات دقیقاً مثل قبل می‌مانند
                self.assertEqual(cfg.owner_command, "ai cod")
                self.assertEqual(cfg.kodrez_command, "کدرز")
                self.assertEqual(cfg.pv_count_command, "تعداد اعضا")
                self.assertEqual(cfg.ai_daily_quota, 5000)
                self.assertEqual(cfg.ai_model, "@cf/zai-org/glm-4.7-flash")
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

class TestReasoningAndBudget(unittest.TestCase):
    """ریشه‌ی «Cloudflare AI: FAILED» برای مدل استدلالی: پاسخ خالی با finish_reason=length."""

    def make(self, session, **kw):
        return CloudflareAI("acct-1", "tok-1", "@cf/zai-org/glm-4.7-flash",
                            timeout=5, session=session, **kw)

    @staticmethod
    def empty_at_budget(reasoning="1. **Analyze the User", finish="length"):
        return {
            "result": {"choices": [{"finish_reason": finish,
                                    "message": {"content": "", "reasoning_content": reasoning}}],
                       "usage": {"completion_tokens": 8, "completion_tokens_details":
                                 {"reasoning_tokens": 8}}},
            "success": True, "errors": [], "messages": [],
        }

    @staticmethod
    def answer(text="پاسخ نهایی", finish="stop", reasoning="thinking"):
        return {
            "result": {"choices": [{"finish_reason": finish,
                                    "message": {"content": text, "reasoning_content": reasoning}}],
                       "usage": {"completion_tokens": 40}},
            "success": True, "errors": [], "messages": [],
        }

    def test_empty_content_at_budget_retries_once_with_doubled_budget(self):
        session = FakeSession(responses=[
            (200, self.empty_at_budget()),      # تلاش اول: بودجه صرف استدلال
            (200, self.answer("سلام! چطور کمکت کنم؟")),   # تلاش دوم: پاسخ واقعی
        ])
        result = run(self.make(session, max_output_tokens=256)
                     .chat([{"role": "user", "content": "سلام"}]))

        self.assertEqual(result.text, "سلام! چطور کمکت کنم؟")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0]["body"]["max_completion_tokens"], 256)
        self.assertEqual(session.calls[1]["body"]["max_completion_tokens"], 512)   # دوبرابر
        self.assertEqual(result.finish_reason, "stop")

    def test_retry_budget_is_capped(self):
        """دوبرابر شدن بودجه از سقف max_retry_budget عبور نمی‌کند و فقط یک تلاش دوباره است."""
        session = FakeSession(responses=[
            (200, self.empty_at_budget()),      # بودجه صرف استدلال شد
            (200, self.answer()),               # تلاش دوباره با بودجه‌ی سقف‌دار
        ])
        client = self.make(session, max_output_tokens=3000, max_retry_budget=4096)
        result = run(client.chat([{"role": "user", "content": "x"}]))

        self.assertEqual(len(session.calls), 2)                       # فقط یک تلاش دوباره
        self.assertEqual(session.calls[1]["body"]["max_completion_tokens"], 4096)  # سقف، نه 6000
        self.assertEqual(result.text, "پاسخ نهایی")

    def test_empty_after_retry_raises_actionable_error(self):
        session = FakeSession(payload=self.empty_at_budget())          # همیشه خالی
        with self.assertRaises(AIError) as ctx:
            run(self.make(session, max_output_tokens=256)
                .chat([{"role": "user", "content": "سلام"}]))

        message = str(ctx.exception)
        self.assertIn("ACOD_AI_MAX_OUTPUT_TOKENS", message)            # قابل‌اقدام
        self.assertIn("length", message)
        self.assertNotIn("توکن‌ی", message)                            # هیچ سکرتی چاپ نمی‌شود
        self.assertNotIn("tok-1", message)
        self.assertEqual(len(session.calls), 2)                        # تلاش دوباره انجام شد

    def test_reasoning_only_with_normal_finish_is_returned_as_last_resort(self):
        session = FakeSession(payload={
            "result": {"choices": [{"finish_reason": "stop",
                                    "message": {"content": "", "reasoning_content": "فکر کردم و جواب می‌دهم"}}]},
            "success": True, "errors": [], "messages": [],
        })
        result = run(self.make(session).chat([{"role": "user", "content": "x"}]))
        self.assertTrue(result.used_reasoning)
        self.assertEqual(result.text, "فکر کردم و جواب می‌دهم")
        self.assertEqual(len(session.calls), 1)                        # تلاش اضافه لازم نیست

    def test_reasoning_field_variants(self):
        for key in ("reasoning_content", "reasoning"):
            with self.subTest(key=key):
                payload = {"result": {"choices": [{"finish_reason": "stop",
                                                   "message": {key: "متن استدلال"}}]},
                           "success": True, "errors": [], "messages": []}
                self.assertEqual(extract_reasoning(payload), "متن استدلال")

    def test_content_as_parts_is_joined(self):
        session = FakeSession(payload={
            "result": {"choices": [{"finish_reason": "stop",
                                    "message": {"content": [{"type": "text", "text": "سلام "},
                                                            {"type": "text", "text": "دنیا"}]}}]},
            "success": True, "errors": [], "messages": [],
        })
        self.assertEqual(run(self.make(session).chat([{"role": "user", "content": "x"}])).text,
                         "سلام دنیا")

    def test_parameter_fallback_when_server_rejects_modern_field(self):
        session = FakeSession(responses=[
            (400, {"errors": [{"code": 5004, "message": "Invalid data: unsupported field "
                                                        "max_completion_tokens"}],
                   "success": False, "result": {}, "messages": []}),
            (200, self.answer("پاسخ با نام قدیمی پارامتر")),
        ])
        result = run(self.make(session).chat([{"role": "user", "content": "x"}]))

        self.assertEqual(result.text, "پاسخ با نام قدیمی پارامتر")
        self.assertEqual(len(session.calls), 2)
        self.assertIn("max_completion_tokens", session.calls[0]["body"])
        self.assertIn("max_tokens", session.calls[1]["body"])
        self.assertNotIn("max_completion_tokens", session.calls[1]["body"])

    def test_other_400_is_not_retried_as_parameter_issue(self):
        session = FakeSession(status=400, payload={
            "errors": [{"code": 3003, "message": "Incomplete request"}],
            "success": False, "result": {}, "messages": [],
        })
        with self.assertRaises(AIError):
            run(self.make(session).chat([{"role": "user", "content": "x"}]))
        self.assertEqual(len(session.calls), 1, "برای خطاهای بی‌ربط نباید دوباره صدا زده شود")


class TestErrorClassification(unittest.TestCase):
    """طبقه‌بندی خطاها طبق جدول رسمی Workers AI (بدون هیچ داده‌ی حساس)."""

    def make(self, session):
        return CloudflareAI("acct-secret-xyz", "tok-secret-abc", "@cf/zai-org/glm-4.7-flash",
                            timeout=5, session=session)

    def test_daily_quota_3036_is_quota(self):
        session = FakeSession(status=429, payload={
            "errors": [{"code": 3036, "message": "You have used up your daily free allocation "
                                                "of 10,000 neurons."}],
            "success": False, "result": {}, "messages": [],
        })
        with self.assertRaises(AIQuotaExceeded):
            run(self.make(session).chat([{"role": "user", "content": "x"}]))

    def test_capacity_3040_is_not_quota(self):
        """مهم: 3040 هم 429 است ولی «سهمیه» نیست؛ نباید کل روز بسته شود."""
        session = FakeSession(status=429, payload={
            "errors": [{"code": 3040, "message": "Capacity temporarily exceeded, please try again."}],
            "success": False, "result": {}, "messages": [],
        })
        with self.assertRaises(AIError) as ctx:
            run(self.make(session).chat([{"role": "user", "content": "x"}]))
        self.assertNotIsInstance(ctx.exception, AIQuotaExceeded)
        self.assertIn("ظرفیت", str(ctx.exception))

    def test_plain_429_without_code_is_quota(self):
        session = FakeSession(status=429, payload="Too Many Requests")
        with self.assertRaises(AIQuotaExceeded):
            run(self.make(session).chat([{"role": "user", "content": "x"}]))

    def test_auth_error_hints(self):
        for code in (10000, 9109):
            with self.subTest(code=code):
                session = FakeSession(status=403 if code == 9109 else 400, payload={
                    "errors": [{"code": code, "message": "Authentication error"}],
                    "success": False, "result": {}, "messages": [],
                })
                with self.assertRaises(AIError) as ctx:
                    run(self.make(session).chat([{"role": "user", "content": "x"}]))
                message = str(ctx.exception)
                self.assertIn(str(code), message)
                self.assertIn("راهنما", message)
                # هیچ سکرتی در پیام خطا نیست
                self.assertNotIn("tok-secret-abc", message)
                self.assertNotIn("acct-secret-xyz", message)

    def test_paid_plan_error_hint(self):
        session = FakeSession(status=403, payload={
            "errors": [{"code": 5035, "message": "This model requires a Workers Paid plan."}],
            "success": False, "result": {}, "messages": [],
        })
        with self.assertRaises(AIError) as ctx:
            run(self.make(session).chat([{"role": "user", "content": "x"}]))
        self.assertIn("Workers Paid", str(ctx.exception))

    def test_last_status_is_recorded_for_diagnostics(self):
        session = FakeSession(responses=[
            (403, {"errors": [{"code": 5035, "message": "This model requires a Workers Paid plan."}],
                   "success": False, "result": {}, "messages": []}),
        ])
        client = self.make(session)
        with self.assertRaises(AIError):
            run(client.chat([{"role": "user", "content": "x"}]))
        self.assertEqual(client.last_status.status, 403)
        self.assertEqual(client.last_status.codes, [5035])
        self.assertFalse(client.last_status.ok)
        self.assertTrue(client.last_status.elapsed >= 0)

    def test_hint_for_maps_known_codes(self):
        self.assertIn("Workers Paid", hint_for([5035], 403))
        self.assertIn("احراز هویت", hint_for([10000], 400))
        self.assertIn("سهمیه", hint_for([3036], 429))
        self.assertIn("ظرفیت", hint_for([3040], 429))
        self.assertEqual(hint_for([999999], 200, ""), "")

    def test_masked_url_hides_account(self):
        client = self.make(FakeSession())
        self.assertNotIn("acct-secret-xyz", client.url_masked())
        self.assertIn("<ACCOUNT_ID>", client.url_masked())
        self.assertIn("@cf/zai-org/glm-4.7-flash", client.url_masked())

    def test_scrub_removes_secrets_from_echoed_text(self):
        from ai_client import _scrub
        text = "error for tok-secret-abc in account acct-secret-xyz"
        cleaned = _scrub(text, "tok-secret-abc", "acct-secret-xyz")
        self.assertNotIn("tok-secret-abc", cleaned)
        self.assertNotIn("acct-secret-xyz", cleaned)


if __name__ == "__main__":
    unittest.main(verbosity=2)
