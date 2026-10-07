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
    extract_text,
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
    """session جعلی aiohttp که پاسخ‌های کاند (مطابق قالب واقعی) برمی‌گرداند."""

    def __init__(self, *, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {
            "result": {"response": "سلام!"}, "success": True, "errors": [], "messages": []
        }
        self.calls: list[dict] = []
        self.closed = False

    def post(self, url, json=None, headers=None):   # noqa: A002 — نام پارامتر خود aiohttp
        self.calls.append({"url": url, "body": json, "headers": headers})
        payload = self.payload if isinstance(self.payload, str) else _json.dumps(self.payload)
        return FakeResponse(self.status, payload)

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
        self.assertEqual(call["body"]["max_tokens"], 256)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
