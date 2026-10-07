"""
تست‌های ابزار تشخیصی `check_ai.py` — کاملاً آفلاین (session جعلی، بدون شبکه).

هدف: تضمین اینکه خروجی تشخیصی
  * همان چیزهایی را نشان می‌دهد که لازم است (HTTP status، کد خطا، پیام عمومی، درستی endpoint)،
  * و هیچ‌وقت Account ID / API Token را لو نمی‌دهد.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_ai  # noqa: E402
from config import Config  # noqa: E402
from tests.test_ai_client import FakeSession  # noqa: E402

ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "SECRET-token-value-for-tests-987"


def run(coro):
    return asyncio.run(coro)


def make_cfg(**kw) -> Config:
    base = dict(cloudflare_account_id=ACCOUNT, cloudflare_api_token=TOKEN)
    base.update(kw)
    return Config(**base)


class FakePath:
    def __init__(self, is_file: bool):
        self._is_file = is_file

    def is_file(self) -> bool:
        return self._is_file

    def __str__(self) -> str:
        return "/tmp/fake.env"


class TestConfigReport(unittest.TestCase):
    def test_report_has_no_secret_values(self):
        report = check_ai.config_report(make_cfg(), FakePath(True))
        dumped = str(report)
        self.assertNotIn(ACCOUNT, dumped)
        self.assertNotIn(TOKEN, dumped)
        self.assertTrue(report["account_set"])
        self.assertTrue(report["token_set"])
        self.assertEqual(report["account_length"], len(ACCOUNT))
        self.assertTrue(report["account_looks_like_id"])
        self.assertEqual(len(report["account_fingerprint"]), 8)
        self.assertEqual(len(report["token_fingerprint"]), 8)
        self.assertTrue(report["env_file_found"])

    def test_report_detects_wrong_account_format(self):
        report = check_ai.config_report(make_cfg(cloudflare_account_id="not-an-id"), FakePath(False))
        self.assertFalse(report["account_looks_like_id"])
        self.assertFalse(report["env_file_found"])

    def test_report_when_nothing_is_set(self):
        report = check_ai.config_report(
            Config(cloudflare_account_id=None, cloudflare_api_token=None), None
        )
        self.assertFalse(report["account_set"])
        self.assertFalse(report["token_set"])
        self.assertIsNone(report["account_fingerprint"])

    def test_fingerprint_is_stable_and_hides_value(self):
        first = check_ai._fingerprint(TOKEN)
        second = check_ai._fingerprint(TOKEN)
        self.assertEqual(first, second)
        self.assertNotIn(TOKEN[:6], first)

    def test_endpoint_preview_masks_account(self):
        preview = check_ai.endpoint_preview(make_cfg())
        self.assertIn("<ACCOUNT_ID>", preview)
        self.assertNotIn(ACCOUNT, preview)
        self.assertIn("@cf/zai-org/glm-4.7-flash", preview)


class TestLiveCheck(unittest.TestCase):
    def test_success_path_reports_status_and_answer_without_secrets(self):
        session = FakeSession(payload={
            "result": {"choices": [{"finish_reason": "stop",
                                    "message": {"content": "سلام!"}}],
                       "usage": {"completion_tokens": 5, "prompt_tokens": 12}},
            "success": True, "errors": [], "messages": [],
        })
        live = run(check_ai.run_live_check(make_cfg(), session=session))

        self.assertTrue(live["ok"])
        self.assertEqual(live["status"], 200)
        self.assertEqual(live["answer"], "سلام!")
        self.assertEqual(live["finish_reason"], "stop")
        self.assertEqual(live["usage"]["completion_tokens"], 5)
        self.assertNotIn(TOKEN, str(live))
        self.assertNotIn(ACCOUNT, str(live))

    def test_auth_error_is_classified_with_hint_and_no_secret(self):
        session = FakeSession(status=401, payload={
            "errors": [{"code": 10000, "message": "Authentication error"}],
            "success": False, "result": {}, "messages": [],
        })
        live = run(check_ai.run_live_check(make_cfg(), session=session))

        self.assertFalse(live["ok"])
        self.assertEqual(live["status"], 401)
        self.assertEqual(live["codes"], [10000])
        self.assertIn("Authentication error", live["messages"][0])
        self.assertIn("احراز هویت", live["hint"])
        self.assertNotIn(TOKEN, str(live))
        self.assertNotIn(ACCOUNT, str(live))

    def test_quota_error_is_classified(self):
        session = FakeSession(status=429, payload={
            "errors": [{"code": 3036, "message": "You have used up your daily free allocation "
                                                "of 10,000 neurons."}],
            "success": False, "result": {}, "messages": [],
        })
        live = run(check_ai.run_live_check(make_cfg(), session=session))
        self.assertFalse(live["ok"])
        self.assertEqual(live["codes"], [3036])
        self.assertIn("سهمیه", live["hint"])

    def test_paid_plan_error_is_classified(self):
        session = FakeSession(status=403, payload={
            "errors": [{"code": 5035, "message": "This model requires a Workers Paid plan."}],
            "success": False, "result": {}, "messages": [],
        })
        live = run(check_ai.run_live_check(make_cfg(), session=session))
        self.assertIn("Workers Paid", live["hint"])

    def test_response_body_echoing_secret_is_scrubbed(self):
        """حتی اگر سرور (در بدترین حالت) مقدار حساس را در پیام بگذارد، چاپ نمی‌شود."""
        session = FakeSession(status=400, payload={
            "errors": [{"code": 5004, "message": f"Invalid data: token {TOKEN} not accepted"}],
            "success": False, "result": {}, "messages": [],
        })
        live = run(check_ai.run_live_check(make_cfg(), session=session))
        self.assertNotIn(TOKEN, str(live))
        self.assertIn("***", str(live["messages"]))

    def test_token_and_account_are_never_in_printed_report(self):
        """کل متن چاپ‌شده نباید هیچ سکرتی داشته باشد."""
        import io
        from contextlib import redirect_stdout

        session = FakeSession(status=403, payload={
            "errors": [{"code": 5035, "message": "This model requires a Workers Paid plan."}],
            "success": False, "result": {}, "messages": [],
        })
        cfg = make_cfg()
        live = run(check_ai.run_live_check(cfg, session=session))

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            check_ai.print_report(check_ai.config_report(cfg, FakePath(True)), cfg, live,
                                  FakePath(True))
        printed = buffer.getvalue()

        self.assertNotIn(TOKEN, printed)
        self.assertNotIn(ACCOUNT, printed)
        self.assertIn("<ACCOUNT_ID>", printed)          # endpoint پوشیده
        self.assertIn("HTTP status : 403", printed)
        self.assertIn("[5035]", printed)
        self.assertIn("Workers Paid", printed)


class TestCliBehaviour(unittest.TestCase):
    def test_no_call_mode_exits_zero_without_network(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = check_ai.main(["--no-call", "--env", "/nonexistent/.env"])
        self.assertEqual(code, 0)
        self.assertIn("بدون تماس واقعی", buffer.getvalue())

    def test_missing_config_fails_cleanly_without_network(self):
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = check_ai.main(["--env", "/nonexistent/.env"])
        self.assertEqual(code, 1)
        output = buffer.getvalue()
        self.assertIn("تنظیم‌نشده", output)
        self.assertIn("ناموفق", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
