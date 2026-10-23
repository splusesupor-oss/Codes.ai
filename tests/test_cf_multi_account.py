"""
تست‌های جامع قابلیت «چند حساب Cloudflare با چرخش خودکار» و سوئیچ خودکار به Groq.

پوشش کامل تمام موارد خواسته‌شده (کاملاً آفلاین و بدون تماس شبکه‌ای):
  * چرخش خودکار روی خطای سهمیه (HTTP 429 / کدهای 4006، 3036، 3037 / daily free allocation)
  * رد شدن خودکار از حساب‌های exhausted_today و disabled
  * برگرداندن پیام استاندارد «سهمیه روزانه هوش مصنوعی به پایان رسیده است.» هنگام اتمام همه‌ی حساب‌ها
  * ریست خودکار حساب‌های exhausted_today در ساعت 00:00 UTC بدون ری‌استارت
  * سازگاری کامل عقب‌رو با تک‌حساب و تقدم صحیح در Config.cloudflare_accounts
  * ماندگاری کامل در جدول cf_accounts در SQLite (OwnerStore)
  * سوئیچ خودکار از Cloudflare به Groq در ai_service.py هنگام خطای Cloudflare (در صورت تنظیم GROQ_API_KEY)
  * نمایش همه‌ی حساب‌ها با masked_id، status و request_count در check_ai.py
  * دستورات PV مالک سراسری: ai accounts و ai accounts reset
  * عدم نشت هیچ توکن خامی در لاگ‌ها، استثناها، repr و پیام‌ها
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import io
import json as _json
import logging
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_ai  # noqa: E402
import config as config_module  # noqa: E402
from ai_client import (  # noqa: E402
    AIError,
    AIQuotaExceeded,
    AIResponse,
    AccountPool,
    CloudflareAI,
)
from ai_service import GroupAI  # noqa: E402
from config import (  # noqa: E402
    Config,
    mask_account_id,
    normalize_cf_accounts,
    token_fingerprint,
)
from core import BotCore  # noqa: E402
from sender import BrandSender  # noqa: E402
from storage import OwnerStore  # noqa: E402
from tests.fakes import FakeClient, FakeEvent, FakeReplyMessage  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class RoutingFakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._text = payload if isinstance(payload, str) else _json.dumps(payload)

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class RoutingFakeSession:
    """شبیه‌ساز aiohttp که پاسخ‌ها را به تفکیک هر `account_id` در URL مدیریت می‌کند."""

    def __init__(self, routes: dict[str, list[tuple[int, object]]]):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls: list[dict] = []

    def post(self, url: str, json=None, headers=None):  # noqa: A002
        self.calls.append({"url": url, "body": json, "headers": dict(headers or {})})
        for acc_id, queue in self.routes.items():
            if f"/accounts/{acc_id}/ai/run/" in url:
                if len(queue) > 1:
                    status, payload = queue.pop(0)
                else:
                    status, payload = queue[0]
                return RoutingFakeResponse(status, payload)
        return RoutingFakeResponse(500, {"success": False, "errors": [{"code": 9999, "message": "unrouted"}]})

    async def close(self):
        pass


ACC_1 = "1111111111111111111111111111aaaa"
TOK_1 = "CF_SECRET_TOKEN_ONE_SUPER_CONFIDENTIAL_9911"
ACC_2 = "2222222222222222222222222222bbbb"
TOK_2 = "CF_SECRET_TOKEN_TWO_SUPER_CONFIDENTIAL_9922"
ACC_3 = "3333333333333333333333333333cccc"
TOK_3 = "CF_SECRET_TOKEN_THREE_SUPER_CONFIDENTIAL_9933"

OK_PAYLOAD_1 = {"result": {"response": "پاسخ از حساب اول"}, "success": True, "errors": []}
OK_PAYLOAD_2 = {"result": {"response": "پاسخ از حساب دوم"}, "success": True, "errors": []}
QUOTA_429_PAYLOAD = {
    "success": False,
    "result": {},
    "errors": [
        {
            "code": 4006,
            "message": "You have used up your daily free allocation of 10,000 neurons.",
        }
    ],
}


def _clean_cf_env() -> dict[str, str | None]:
    keys = [
        k for k in list(os.environ.keys())
        if k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNTS_JSON")
        or k.startswith("CF_ACCOUNT_")
    ]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    return saved


def _restore_cf_env(saved: dict[str, str | None]) -> None:
    _clean_cf_env()
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


class TestCloudflareMultiAccountRotation(unittest.TestCase):
    """تست‌های جامع چرخش حساب‌های Cloudflare، ماندگاری SQLite، فال‌بک Groq و امنیت توکن‌ها."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.sqlite3"
        self.store = OwnerStore(self.db_path)
        self.current_utc = _dt.datetime(2026, 10, 23, 20, 30, 0, tzinfo=_dt.timezone.utc)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def clock(self, tz=None):
        if tz is not None:
            return self.current_utc.astimezone(tz)
        return self.current_utc

    # ------------------------------------------------------------------ چرخش روی خطای سهمیه
    def test_account_pool_rotates_on_quota_error(self):
        """وقتی حساب اول خطای سهمیه (4006 / 3036 / HTTP 429) بدهد، بلافاصله روی حساب دوم می‌چرخد."""
        session = RoutingFakeSession(
            {
                ACC_1: [(429, QUOTA_429_PAYLOAD)],
                ACC_2: [(200, OK_PAYLOAD_2)],
            }
        )
        pool = AccountPool(
            accounts=[(ACC_1, TOK_1), (ACC_2, TOK_2)],
            store=self.store,
            now_provider=self.clock,
        )
        client = CloudflareAI(pool=pool, model="@cf/meta/llama-3.1-8b-instruct-fp8-fast", session=session)

        resp = run(client.chat([{"role": "user", "content": "سلام"}]))

        self.assertEqual(resp.text, "پاسخ از حساب دوم")
        self.assertEqual(len(session.calls), 2)
        self.assertIn(f"/accounts/{ACC_1}/ai/run/", session.calls[0]["url"])
        self.assertIn(f"/accounts/{ACC_2}/ai/run/", session.calls[1]["url"])
        self.assertEqual(pool.accounts[0].status, "exhausted_today")
        self.assertEqual(pool.accounts[1].status, "active")

    def test_account_pool_rotates_on_429(self):
        """پشتیبانی از نام تست جایگزین برای چرخش خودکار روی HTTP 429."""
        self.test_account_pool_rotates_on_quota_error()

    def test_account_pool_skips_exhausted_accounts(self):
        """درخواست بعدی در همان روز UTC بدون تماس با حساب exhausted مستقیماً به حساب فعال می‌رود."""
        session = RoutingFakeSession(
            {
                ACC_1: [(429, "Too Many Requests")],
                ACC_2: [(200, OK_PAYLOAD_2)],
            }
        )
        pool = AccountPool(
            accounts=[
                {"account_id": ACC_1, "api_token": TOK_1, "status": "exhausted_today"},
                {"account_id": ACC_2, "api_token": TOK_2, "status": "active"},
            ],
            store=self.store,
            now_provider=self.clock,
        )
        client = CloudflareAI(pool=pool, session=session)

        resp = run(client.chat([{"role": "user", "content": "تست"}]))
        self.assertEqual(resp.text, "پاسخ از حساب دوم")
        self.assertEqual(len(session.calls), 1)
        self.assertIn(f"/accounts/{ACC_2}/ai/run/", session.calls[0]["url"])

    # ------------------------------------------------------------------ اتمام همه‌ی حساب‌ها
    def test_account_pool_all_exhausted_returns_quota_message(self):
        """اگر همه‌ی حساب‌ها exhausted شوند و کلید Groq نباشد، پیام پایان سهمیه برگردانده می‌شود."""
        session = RoutingFakeSession(
            {
                ACC_1: [(429, QUOTA_429_PAYLOAD)],
                ACC_2: [(429, QUOTA_429_PAYLOAD)],
            }
        )
        cfg = Config(
            db_path=self.db_path,
            groq_api_key=None,
            cloudflare_accounts=[(ACC_1, TOK_1), (ACC_2, TOK_2)],
        )
        pool = AccountPool.from_config(cfg, store=self.store, now_provider=self.clock)
        ai_client = CloudflareAI.from_config(cfg, pool=pool, session=session)
        sender = BrandSender(cfg)
        group_ai = GroupAI(cfg, self.store, sender, ai_client=ai_client, now_provider=self.clock)
        core = BotCore(cfg, self.store, sender, ai=group_ai, now_provider=self.clock)
        fake_bot_client = FakeClient()

        self.store.claim(100, chat_id=-5001)
        self.store.set_global_suspended(False)
        self.store.ai_set_enabled(-5001, True)
        self.store.set_group_model(-5001, 2)

        run(
            core.on_new_message(
                fake_bot_client,
                FakeEvent(
                    "سوال تستی",
                    user_id=100,
                    chat_id=-5001,
                    is_group=True,
                    reply_to=FakeReplyMessage(sender_id=100, msg_id=10),
                ),
            )
        )

        self.assertEqual(pool.accounts[0].status, "exhausted_today")
        self.assertEqual(pool.accounts[1].status, "exhausted_today")
        sent_messages = [getattr(r, "message", "") for r in fake_bot_client.requests if hasattr(r, "message")]
        self.assertEqual(sent_messages[-1], "سهمیه روزانه هوش مصنوعی به پایان رسیده است.")

    def test_all_accounts_exhausted_returns_quota_message(self):
        """نام معادل تست تمام شدن سهمیه‌ی کل حساب‌ها."""
        self.test_account_pool_all_exhausted_returns_quota_message()

    # ------------------------------------------------------------------ ریست خودکار 00:00 UTC
    def test_utc_midnight_resets_exhausted_accounts(self):
        """در ساعت 00:00 UTC وضعیت exhausted_today همه‌ی حساب‌ها خودکار به active برمی‌گردد."""
        session = RoutingFakeSession(
            {
                ACC_1: [(429, QUOTA_429_PAYLOAD), (200, OK_PAYLOAD_1)],
                ACC_2: [(429, QUOTA_429_PAYLOAD), (200, OK_PAYLOAD_2)],
            }
        )
        pool = AccountPool(
            accounts=[(ACC_1, TOK_1), (ACC_2, TOK_2)],
            store=self.store,
            now_provider=self.clock,
        )
        client = CloudflareAI(pool=pool, session=session)

        self.current_utc = _dt.datetime(2026, 10, 23, 23, 59, 0, tzinfo=_dt.timezone.utc)
        with self.assertRaises(AIQuotaExceeded):
            run(client.chat([{"role": "user", "content": "قبل از نیمه‌شب"}]))
        self.assertEqual(pool.accounts[0].status, "exhausted_today")
        self.assertEqual(pool.accounts[1].status, "exhausted_today")

        # عبور از 00:00 UTC بدون ری‌استارت
        self.current_utc = _dt.datetime(2026, 10, 24, 0, 0, 5, tzinfo=_dt.timezone.utc)
        resp = run(client.chat([{"role": "user", "content": "بعد از نیمه‌شب UTC"}]))

        self.assertEqual(resp.text, "پاسخ از حساب اول")
        self.assertEqual(pool.accounts[0].status, "active")
        self.assertEqual(pool.accounts[1].status, "active")
        self.assertTrue(all(r.status == "active" for r in self.store.list_cf_accounts()))

    def test_quota_resets_at_utc_midnight(self):
        """نام معادل تست ریست نیمه‌شب UTC."""
        self.test_utc_midnight_resets_exhausted_accounts()

    # ------------------------------------------------------------------ سازگاری عقب‌رو و اولویت Config
    def test_single_account_backward_compat(self):
        """تست توابع کمکی config.py، سازگاری تک‌حساب و ترتیب اولویت JSON -> شماره‌گذاری -> قدیمی."""
        self.assertEqual(mask_account_id(ACC_1), "1111...aaaa")
        self.assertEqual(len(token_fingerprint(TOK_1)), 8)
        self.assertNotIn(TOK_1[:4], token_fingerprint(TOK_1))
        normed = normalize_cf_accounts([{"account_id": ACC_1, "api_token": TOK_1}, (ACC_2, TOK_2)])
        self.assertEqual(normed, [(ACC_1, TOK_1), (ACC_2, TOK_2)])

        saved = _clean_cf_env()
        try:
            # ۱) حالت قدیمی تک‌حساب
            os.environ["CLOUDFLARE_ACCOUNT_ID"] = ACC_1
            os.environ["CLOUDFLARE_API_TOKEN"] = TOK_1
            cfg_legacy = Config.from_env()
            self.assertEqual(cfg_legacy.cloudflare_accounts, [(ACC_1, TOK_1)])

            # ۲) وقتی CF_ACCOUNT_N_ID/TOKEN تنظیم شود بر قدیمی مقدم است
            os.environ["CF_ACCOUNT_1_ID"] = ACC_2
            os.environ["CF_ACCOUNT_1_TOKEN"] = TOK_2
            os.environ["CF_ACCOUNT_2_ID"] = ACC_3
            os.environ["CF_ACCOUNT_2_TOKEN"] = TOK_3
            cfg_num = Config.from_env()
            self.assertEqual(cfg_num.cloudflare_accounts, [(ACC_2, TOK_2), (ACC_3, TOK_3)])

            # ۳) وقتی CLOUDFLARE_ACCOUNTS_JSON تنظیم شود بالاترین اولویت را دارد
            os.environ["CLOUDFLARE_ACCOUNTS_JSON"] = _json.dumps(
                [{"account_id": ACC_3, "api_token": TOK_3}, {"account_id": ACC_1, "api_token": TOK_1}]
            )
            cfg_json = Config.from_env()
            self.assertEqual(cfg_json.cloudflare_accounts, [(ACC_3, TOK_3), (ACC_1, TOK_1)])

            # اجرای واقعی با کلاینت تک‌حساب قدیمی
            session = RoutingFakeSession({ACC_1: [(200, OK_PAYLOAD_1)]})
            legacy_client = CloudflareAI(ACC_1, TOK_1, session=session)
            self.assertEqual(run(legacy_client.chat([{"role": "user", "content": "سلام"}])).text, "پاسخ از حساب اول")
        finally:
            _restore_cf_env(saved)

    def test_backward_compat_single_account_still_works(self):
        """نام معادل تست سازگاری عقب‌رو تک‌حساب."""
        self.test_single_account_backward_compat()

    # ------------------------------------------------------------------ ماندگاری در SQLite
    def test_accounts_are_persisted_in_sqlite(self):
        """بررسی متدهای جدول cf_accounts در OwnerStore و بازیابی وضعیت پس از ری‌استارت."""
        rows = self.store.sync_cf_accounts([(ACC_1, TOK_1), (ACC_2, TOK_2)])
        self.assertEqual(len(rows), 2)

        self.store.record_cf_account_request(ACC_1, "2026-10-23T20:00:00+00:00")
        self.store.record_cf_account_error(ACC_1)
        self.store.mark_cf_account_exhausted(ACC_1, "2026-10-23")
        self.store.set_cf_account_status(ACC_2, "disabled", None)

        r1 = self.store.get_cf_account(ACC_1)
        r2 = self.store.get_cf_account(ACC_2)
        self.assertEqual(r1.status, "exhausted_today")
        self.assertEqual(r1.request_count, 1)
        self.assertEqual(r1.error_count, 1)
        self.assertEqual(r2.status, "disabled")

        # در روز جدید UTC فقط exhausted_today به active برمی‌گردد و disabled دست‌نخورده می‌ماند
        reset_n = self.store.reset_cf_accounts_if_new_utc_day("2026-10-24")
        self.assertEqual(reset_n, 1)
        self.assertEqual(self.store.get_cf_account(ACC_1).status, "active")
        self.assertEqual(self.store.get_cf_account(ACC_2).status, "disabled")

        # ریست دستی هم کار می‌کند
        self.store.mark_cf_account_exhausted(ACC_1, "2026-10-24")
        self.assertEqual(self.store.reset_all_exhausted_cf_accounts(), 1)
        self.assertEqual(self.store.get_cf_account(ACC_1).status, "active")

    # ------------------------------------------------------------------ سوئیچ خودکار Cloudflare -> Groq
    def test_cloudflare_error_switches_to_groq_when_key_configured(self):
        """در ai_service.py اگر Cloudflare خطا دهد و GROQ_API_KEY تنظیم باشد، خودکار به Groq سوئیچ می‌کند."""

        class StubGroq:
            def __init__(self):
                self.calls = []

            def is_available(self):
                return True

            async def chat(self, messages, *, model, max_tokens=None, **kw):
                self.calls.append({"model": model, "max_tokens": max_tokens})
                return AIResponse(text="پاسخ جایگزین از Groq")

        session = RoutingFakeSession(
            {
                ACC_1: [(429, QUOTA_429_PAYLOAD)],
                ACC_2: [(500, {"success": False, "errors": [{"code": 50000, "message": "Server Error"}]})],
            }
        )
        cfg = Config(
            db_path=self.db_path,
            groq_api_key="gsk_valid_secret_key",
            ai_retry_max=0,
            cloudflare_accounts=[(ACC_1, TOK_1), (ACC_2, TOK_2)],
        )
        stub_groq = StubGroq()
        ai_client = CloudflareAI.from_config(cfg, store=self.store, session=session, now_provider=self.clock)
        sender = BrandSender(cfg)
        group_ai = GroupAI(
            cfg, self.store, sender, ai_client=ai_client, groq_client=stub_groq, now_provider=self.clock
        )
        core = BotCore(cfg, self.store, sender, ai=group_ai, now_provider=self.clock)
        fake_bot_client = FakeClient()

        self.store.claim(100, chat_id=-6001)
        self.store.set_global_suspended(False)
        self.store.ai_set_enabled(-6001, True)
        self.store.set_group_model(-6001, 2)  # گروه روی مدل ۲ (Cloudflare) تنظیم است

        run(
            core.on_new_message(
                fake_bot_client,
                FakeEvent(
                    "تحلیل وضعیت بازار",
                    user_id=100,
                    chat_id=-6001,
                    is_group=True,
                    reply_to=FakeReplyMessage(sender_id=100, msg_id=20),
                ),
            )
        )

        self.assertEqual(len(stub_groq.calls), 1)
        sent_messages = [getattr(r, "message", "") for r in fake_bot_client.requests if hasattr(r, "message")]
        self.assertIn("پاسخ جایگزین از Groq", sent_messages[-1])

    # ------------------------------------------------------------------ check_ai چندحسابی و امنیت توکن‌ها
    def test_token_never_leaked(self):
        """هیچ توکن خامی هرگز در لاگ‌ها، پیام خطا، خروجی PV یا گزارش چندحسابی check_ai چاپ نمی‌شود."""
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        old_level = root_logger.level
        root_logger.setLevel(logging.DEBUG)

        try:
            session = RoutingFakeSession(
                {
                    ACC_1: [(429, {"success": False, "errors": [{"code": 4006, "message": f"quota {TOK_1}"}]})],
                    ACC_2: [(200, OK_PAYLOAD_2)],
                }
            )
            cfg = Config(
                db_path=self.db_path,
                cloudflare_accounts=[(ACC_1, TOK_1), (ACC_2, TOK_2)],
            )
            pool = AccountPool.from_config(cfg, store=self.store, now_provider=self.clock)
            ai_client = CloudflareAI.from_config(cfg, pool=pool, session=session)

            # یک درخواست موفق با چرخش (ACC_1 -> 429, ACC_2 -> 200)
            ans = run(ai_client.chat([{"role": "user", "content": "سلام"}]))
            self.assertEqual(ans.text, "پاسخ از حساب دوم")

            # بررسی check_ai روی هر دو حساب (نمایش همه‌ی حساب‌ها با masked_id، status و request_count)
            cfg_rep = check_ai.config_report(cfg)
            self.assertEqual(len(cfg_rep["accounts"]), 2)
            self.assertEqual(cfg_rep["accounts"][0]["masked_id"], "1111...aaaa")
            self.assertEqual(cfg_rep["accounts"][0]["status"], "exhausted_today")
            self.assertEqual(cfg_rep["accounts"][0]["request_count"], 1)
            self.assertEqual(cfg_rep["accounts"][1]["masked_id"], "2222...bbbb")
            self.assertEqual(cfg_rep["accounts"][1]["status"], "active")
            self.assertEqual(cfg_rep["accounts"][1]["request_count"], 1)

            live = run(check_ai.run_live_check(cfg, session=session))
            self.assertEqual(len(live["accounts"]), 2)
            buf = io.StringIO()
            with redirect_stdout(buf):
                check_ai.print_report(cfg_rep, cfg, live, Path("/tmp/fake.env"))
            printed_check = buf.getvalue()

            self.assertIn("masked_id=1111...aaaa", printed_check)
            self.assertIn("masked_id=2222...bbbb", printed_check)
            self.assertIn("request_count=", printed_check)
            self.assertNotIn(TOK_1, printed_check)
            self.assertNotIn(TOK_2, printed_check)
            self.assertNotIn(ACC_1, printed_check)
            self.assertNotIn(ACC_2, printed_check)

            # بررسی PV و لاگ‌ها
            sender = BrandSender(cfg)
            group_ai = GroupAI(cfg, self.store, sender, ai_client=ai_client, now_provider=self.clock)
            core = BotCore(cfg, self.store, sender, ai=group_ai, now_provider=self.clock)
            fake_bot_client = FakeClient()
            self.store.claim(777, chat_id=-5001)
            self.store.set_global_suspended(False)
            run(
                core.on_new_message(
                    fake_bot_client,
                    FakeEvent("ai accounts", user_id=777, chat_id=777, is_private=True),
                )
            )
            pv_out = "\n".join(getattr(r, "message", "") for r in fake_bot_client.requests if hasattr(r, "message"))
            for blob in (pv_out, log_stream.getvalue(), repr(pool), repr(ai_client), repr(cfg)):
                self.assertNotIn(TOK_1, blob)
                self.assertNotIn(TOK_2, blob)
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(old_level)

    def test_token_never_leaked_in_logs_or_messages(self):
        """نام معادل تست عدم نشت توکن در لاگ‌ها و پیام‌ها."""
        self.test_token_never_leaked()

    # ------------------------------------------------------------------ خطاهای غیرسهمیه‌ای و کنترل دسترسی PV
    def test_non_quota_errors_3040_and_5007_do_not_exhaust_account(self):
        """خطای ظرفیت موقت 3040 یا مدل نامعتبر 5007 نباید حساب را exhausted_today کند."""
        session_3040 = RoutingFakeSession(
            {
                ACC_1: [
                    (429, {"success": False, "errors": [{"code": 3040, "message": "Out of capacity"}]}),
                    (200, OK_PAYLOAD_1),
                ],
                ACC_2: [(200, OK_PAYLOAD_2)],
            }
        )
        pool = AccountPool(accounts=[(ACC_1, TOK_1), (ACC_2, TOK_2)], store=self.store)
        client = CloudflareAI(pool=pool, session=session_3040)
        res = run(client.chat([{"role": "user", "content": "سلام"}], max_retries=1, base_delay=0.01))
        self.assertEqual(res.text, "پاسخ از حساب اول")
        self.assertEqual(pool.accounts[0].status, "active")

        session_5007 = RoutingFakeSession(
            {
                ACC_1: [(400, {"success": False, "errors": [{"code": 5007, "message": "No such model"}]})],
            }
        )
        client_5007 = CloudflareAI(pool=pool, session=session_5007)
        with self.assertRaises(AIError):
            run(client_5007.chat([{"role": "user", "content": "سلام"}]))
        self.assertEqual(pool.accounts[0].status, "active")

    def test_pv_ai_accounts_reset_and_access_control(self):
        """دستورات `ai accounts` و `ai accounts reset` فقط برای مالک سراسری و فقط در PV مجازند."""
        cfg = Config(
            db_path=self.db_path,
            cloudflare_accounts=[(ACC_1, TOK_1), (ACC_2, TOK_2)],
        )
        pool = AccountPool.from_config(cfg, store=self.store, now_provider=self.clock)
        pool.mark_exhausted(ACC_1)
        ai_client = CloudflareAI.from_config(cfg, pool=pool)
        sender = BrandSender(cfg)
        group_ai = GroupAI(cfg, self.store, sender, ai_client=ai_client, now_provider=self.clock)
        core = BotCore(cfg, self.store, sender, ai=group_ai, now_provider=self.clock)
        fake_bot_client = FakeClient()

        OWNER_ID, OTHER_USER = 9001, 9002
        self.store.claim(OWNER_ID, chat_id=-7001)
        self.store.set_global_suspended(False)

        run(
            core.on_new_message(
                fake_bot_client,
                FakeEvent("ai accounts", user_id=OWNER_ID, chat_id=-7001, is_group=True),
            )
        )
        self.assertEqual(fake_bot_client.requests, [])

        run(
            core.on_new_message(
                fake_bot_client,
                FakeEvent("ai accounts reset", user_id=OTHER_USER, chat_id=OTHER_USER, is_private=True),
            )
        )
        self.assertEqual(pool.accounts[0].status, "exhausted_today")

        fake_bot_client.clear_requests()
        run(
            core.on_new_message(
                fake_bot_client,
                FakeEvent("ai accounts reset", user_id=OWNER_ID, chat_id=OWNER_ID, is_private=True),
            )
        )
        self.assertEqual(pool.accounts[0].status, "active")
        self.assertEqual(self.store.get_cf_account(ACC_1).status, "active")


if __name__ == "__main__":
    unittest.main(verbosity=2)
