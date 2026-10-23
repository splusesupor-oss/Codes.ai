"""
تست‌های واحد موتور دانش زنده و به‌روزرسانی اطلاعات مدل Cloudflare تا سال ۲۰۲۶ / ۱۴۰۵ (`live_search.py`).

این تست‌ها کاملاً آفلاین و بدون تماس شبکه‌ای هستند و موارد زیر را تضمین می‌کنند:
  * تزریق دقیق تاریخ و ساعت زنده خورشیدی (۱۴۰۵) و میلادی (۲۰۲۶) به پیام سیستم مدل
  * حذف کامل توهم قدیمی «آخرین بروزرسانی من سال ۲۰۲۱ است» در پرامپت سیستم
  * واکشی موازی و کش‌دار نتایج ۲۰۲۶ از ویکی‌پدیا و فیدهای خبری RSS (با شبیه‌ساز آفلاین aiohttp)
  * تزریق خودکار نتایج ۲۰۲۶ در `GroupAI` پیش از ارسال درخواست به `CloudflareAI`
  * ارتقای خودکار مدل‌های منسوخ ۲۰۲۱ در فایل‌های `.env` قدیمی (`upgrade_legacy_cf_model`)
  * عدم اجرای جستجوی وب برای سلام و احوال‌پرسی‌های ساده جهت حفظ سرعت آنی
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brand  # noqa: E402
from ai_client import CloudflareAI  # noqa: E402
from ai_service import GroupAI  # noqa: E402
from config import Config, upgrade_legacy_cf_model  # noqa: E402
from core import BotCore  # noqa: E402
from live_search import (  # noqa: E402
    LiveWebSearcher,
    build_realtime_date_context,
    clean_search_query,
    should_search_web,
)
from sender import BrandSender  # noqa: E402
from storage import OwnerStore  # noqa: E402
from tests.fakes import FakeClient, FakeEvent, FakeReplyMessage  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeHttpGetResponse:
    def __init__(self, status: int, body: str | dict):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    async def json(self, content_type=None):
        return json.loads(self._body) if isinstance(self._body, str) else self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSearchHttpSession:
    """شبیه‌ساز آفلاین منابع وب ۲۰۲۶ (ویکی‌پدیا فارسی + Bing News RSS + Google News RSS)."""

    def __init__(self):
        self.urls: list[str] = []

    def get(self, url: str):
        self.urls.append(url)
        if "fa.wikipedia.org" in url:
            return FakeHttpGetResponse(
                200,
                {
                    "query": {
                        "pages": {
                            "1": {
                                "index": 1,
                                "title": "فینال جام جهانی فوتبال ۲۰۲۶",
                                "extract": "فینال جام جهانی فوتبال ۲۰۲۶ در ۱۹ ژوئیه ۲۰۲۶ برگزار شد و تیم ملی اسپانیا قهرمان جهان شد.",
                            }
                        }
                    }
                },
            )
        if "bing.com/news" in url:
            xml = """<?xml version="1.0" encoding="utf-8"?>
            <rss version="2.0"><channel>
              <item>
                <title>گزارش رسمی رویدادهای فناوری سال ۱۴۰۵ و ۲۰۲۶</title>
                <description>آخرین آمار رسمی منتشرشده در پاییز ۱۴۰۵ (اکتبر ۲۰۲۶).</description>
                <pubDate>Fri, 23 Oct 2026 09:00:00 GMT</pubDate>
              </item>
            </channel></rss>"""
            return FakeHttpGetResponse(200, xml)
        xml_empty = '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel></channel></rss>'
        return FakeHttpGetResponse(200, xml_empty)


class CapturingCloudflareSession:
    """شبیه‌ساز کلاینت Cloudflare که پیام‌های ارسالی نهایی مدل را برای بازرسی ضبط می‌کند."""

    def __init__(self):
        self.captured_payloads: list[dict] = []

    def post(self, url: str, json=None, headers=None):  # noqa: A002
        self.captured_payloads.append(json or {})
        return FakeHttpGetResponse(
            200,
            {
                "success": True,
                "errors": [],
                "result": {
                    "response": "طبق اطلاعات به‌روز سال ۲۰۲۶ (۱۴۰۵)، تیم ملی اسپانیا قهرمان جام جهانی ۲۰۲۶ شد."
                },
            },
        )

    async def close(self):
        pass


class TestLive2026Knowledge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "live2026.sqlite3"
        self.store = OwnerStore(self.db_path)
        self.fixed_utc = _dt.datetime(2026, 10, 23, 15, 0, 0, tzinfo=_dt.timezone.utc)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def clock(self, tz=None):
        return self.fixed_utc.astimezone(tz) if tz is not None else self.fixed_utc

    def test_system_prompt_and_date_context_anchor_2026(self):
        """پرامپت سیستم و بلوک زمان زنده باید صراحتاً سال ۲۰۲۶ / ۱۴۰۵ را تثبیت و سال ۲۰۲۱ را نفی کنند."""
        self.assertIn("۲۰۲۶", brand.AI_SYSTEM_PROMPT)
        self.assertIn("۱۴۰۵", brand.AI_SYSTEM_PROMPT)
        self.assertIn("۲۰۲۱", brand.AI_SYSTEM_PROMPT)

        ctx = build_realtime_date_context(self.fixed_utc)
        self.assertIn("2026-10-23", ctx)
        self.assertIn("1405/08/01", ctx)
        self.assertIn("آبان", ctx)

    def test_intent_filter_skips_greetings_and_catches_2026_questions(self):
        """سلام و احوال‌پرسی نباید جستجوی وب بزند، اما سوالات دانشی و روز باید جستجو شوند."""
        self.assertFalse(should_search_web("سلام"))
        self.assertFalse(should_search_web("دمت گرم"))
        self.assertFalse(should_search_web("امروز چندمه؟"))  # بلوک زمان سیستم کافی است
        self.assertTrue(should_search_web("قهرمان جام جهانی ۲۰۲۶ کی شد؟"))
        self.assertTrue(should_search_web("رئیس جمهور فعلی ایران الان کیه؟"))
        self.assertEqual(clean_search_query("میشه بگی رئیس جمهور فعلی ایران الان کیه؟"), "رئیس جمهور ایران")

    def test_legacy_2021_cloudflare_models_auto_upgrade(self):
        """مدل‌های منسوخ ۲۰۲۱ کلودفلر خودکار ارتقا می‌یابند ولی مدل‌های سفارشی جدید حفظ می‌شوند."""
        self.assertEqual(
            upgrade_legacy_cf_model("@cf/meta/llama-2-7b-chat-int8"),
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        )
        self.assertEqual(
            upgrade_legacy_cf_model("@cf/zai-org/glm-4.7-flash"),
            "@cf/zai-org/glm-4.7-flash",
        )

    def test_cloudflare_request_receives_live_2026_web_context_and_uses_cache(self):
        """درخواست گروه روی Cloudflare باید داده‌های زنده‌ی ۲۰۲۶ را داخل پیام سیستم دریافت کند و کش شود."""
        fake_web = FakeSearchHttpSession()
        searcher = LiveWebSearcher(session=fake_web)
        cf_session = CapturingCloudflareSession()
        cfg = Config(
            db_path=self.db_path,
            cloudflare_accounts=[("1111111111111111111111111111aaaa", "cf_tok_safe_test")],
        )
        cf_client = CloudflareAI.from_config(cfg, store=self.store, session=cf_session, now_provider=self.clock)
        sender = BrandSender(cfg)
        group_ai = GroupAI(
            cfg,
            self.store,
            sender,
            ai_client=cf_client,
            now_provider=self.clock,
            live_searcher=searcher,
        )
        core = BotCore(cfg, self.store, sender, ai=group_ai, now_provider=self.clock)
        bot_client = FakeClient()

        self.store.claim(500, chat_id=-8800)
        self.store.set_global_suspended(False)
        self.store.ai_set_enabled(-8800, True)
        self.store.set_group_model(-8800, 2)

        run(
            core.on_new_message(
                bot_client,
                FakeEvent(
                    "قهرمان جام جهانی ۲۰۲۶ چه تیمی شد؟",
                    user_id=500,
                    chat_id=-8800,
                    is_group=True,
                    reply_to=FakeReplyMessage(sender_id=500, msg_id=1),
                ),
            )
        )

        self.assertEqual(len(cf_session.captured_payloads), 1)
        sys_msg = cf_session.captured_payloads[0]["messages"][0]["content"]
        self.assertIn("2026-10-23", sys_msg)
        self.assertIn("1405/08/01", sys_msg)
        self.assertIn("تیم ملی اسپانیا قهرمان جهان شد", sys_msg)
        self.assertIn("پاییز ۱۴۰۵", sys_msg)

        # فراخوانی مجدد همان سوال باید از حافظه نهان (Cache) خوانده شود و تماس وب جدیدی نزند
        prev_web_hits = len(fake_web.urls)
        cached_res = run(searcher.search("قهرمان جام جهانی ۲۰۲۶ چه تیمی شد؟", now_dt=self.fixed_utc))
        self.assertGreaterEqual(len(cached_res), 2)
        self.assertEqual(len(fake_web.urls), prev_web_hits)


if __name__ == "__main__":
    unittest.main(verbosity=2)
