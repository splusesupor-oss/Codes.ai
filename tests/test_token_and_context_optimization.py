"""
تست‌های بهینه‌سازی مصرف توکن و جلوگیری از ارسال تاریخچه و اعضای غیرضروری به Cloudflare Workers AI.

موارد تحت پوشش:
  ۱) درخواست‌های عادی (سلام، شعر، کدنویسی، سوال عمومی) حاوی پیام‌های اخیر گروه و فهرست اعضا نیستند.
  ۲) سوالات صریح درباره بحث گروه («بحث گروه درباره چی بود؟»، «خلاصه چت‌ها»، «اوضاع گروه») تاریخچه مرتبط را دریافت می‌کنند.
  ۳) سوالات درباره کاربران گروه («پیدا کردن کاربر»، «لیست اعضا») فهرست اعضا را دریافت می‌کنند.
  ۴) سوالات درباره مشخصات گروه («مالک کیست»، «اطلاعات گروه») متادیتای گروه را دریافت می‌کنند.
  ۵) ثبت و اندازه‌گیری واقعی مصرف توکن ورودی/خروجی از شیء usage پاسخ API بدون تخمین کاراکتری.
  ۶) کنترل و محدودسازی طول متن پیام‌های تاریخچه خصوصی کاربر جهت جلوگیری از تورم پرامپت.
  ۷) فشرده‌سازی پرامپت سیستمی و کاهش طول آن به زیر ۲۰۰۰ کاراکتر همراه با حفظ تمام قواعد و رفتارها.
"""

import asyncio
import unittest
from collections import deque

import brand
from ai_service import GroupAI
from config import Config
from core import BotCore
from models import MODEL_PROFILES
from storage import OwnerStore
from tests.fakes import FakeAI, FakeClient, FakeEvent, FakeReplyMessage, FakeSender


def run(coro):
    return asyncio.run(coro)


class TestTokenAndContextOptimization(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.from_env()
        self.group_id = -1007788
        self.user_id = 12345
        self.other_user_id = 67890

        # ساخت یک سرویس هوش مصنوعی با کلاینت ساختگی
        self.fake_ai = FakeAI(reply="پاسخ بهینه‌سازی‌شده تست.")
        from sender import BrandSender

        self.sender = BrandSender(self.cfg)
        import tempfile

        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3")
        self.store = OwnerStore(self._tmp.name)
        self.store.claim(self.user_id, chat_id=self.group_id, display_name="global_owner")
        self.store.activate_group(self.group_id, activated_by=self.user_id, group_name="گروه توسعه روباه")
        self.store.ai_set_enabled(self.group_id, True)

        self.ai = GroupAI(
            cfg=self.cfg,
            store=self.store,
            sender=self.sender,
            ai_client=self.fake_ai,
        )

        # تزریق اطلاعات کامل به گروه (متادیتا، اعضا، و ۱۵ پیام در بافر اخیر)
        self.ai.set_group_metadata(
            self.group_id,
            group_name="گروه توسعه روباه",
            owner_label="@owner_dev",
            admin_labels=["@admin_1", "@admin_2"],
            members_count=50,
        )

        for i in range(1, 16):
            self.ai.record_group_member(self.group_id, 1000 + i, f"عضو شماره {i}", username=f"user_{i}")
            self.ai.record_group_message(
                self.group_id,
                f"کاربر {i}",
                f"متن پیام آزمایشی شماره {i} ردوبدل شده در گروه",
                user_id=1000 + i,
                username=f"user_{i}",
            )

    def tearDown(self):
        self._tmp.close()

    # -----------------------------------------------------------------------
    # ۱) درخواست‌های عادی حاوی پیام‌های گروه و لیست اعضا نیستند
    # -----------------------------------------------------------------------
    def test_01_normal_requests_exclude_group_history_and_members_list(self):
        """پیام‌های معمولی مانند «سلام»، شعر یا کدنویسی نباید تاریخچه و اعضای گروه را ارسال کنند."""
        normal_queries = [
            "سلام چطوری؟",
            "یک شعر زیبا از سعدی برام بنویس",
            "کد پایتون برای مرتب‌سازی سریع (QuickSort) بنویس",
            "ریاضیات جبر خطی چیست؟",
        ]

        for query in normal_queries:
            msgs = self.ai._build_messages(self.group_id, self.user_id, query)
            sys_content = msgs[0]["content"]

            # نباید شامل آخرین پیام‌های ردوبدل شده گروه باشد
            self.assertNotIn("آخرین پیام‌های ردوبدل‌شده اعضای گروه", sys_content)
            self.assertNotIn("متن پیام آزمایشی شماره", sys_content)

            # نباید شامل لیست اعضای گروه باشد
            self.assertNotIn("فهرست اعضا و کاربران شناخته‌شده در گروه", sys_content)
            self.assertNotIn("عضو شماره 1", sys_content)
            self.assertNotIn("@user_1", sys_content)

            # نباید شامل اطلاعات مدیریت گروه (مالک/ادمین‌ها) باشد
            self.assertNotIn("اطلاعات گروه فعلی:", sys_content)
            self.assertNotIn("@admin_1", sys_content)

    # -----------------------------------------------------------------------
    # ۲) سوال درباره بحث یا رویدادهای گروه پیام‌های اخیر را اضافه می‌کند
    # -----------------------------------------------------------------------
    def test_02_asking_about_group_discussion_includes_recent_messages(self):
        """پرسش درباره بحث، خلاصه چت‌ها یا اوضاع گروه باید تاریخچه اخیر را به پرامپت اضافه کند."""
        discussion_queries = [
            "بحث گروه درباره چی بود؟",
            "بچه‌ها در مورد چی صحبت می‌کردن؟",
            "خلاصه چت‌های اخیر گروه رو بگو",
            "اوضاع گروه چطوره و چه پیام‌هایی ردوبدل شده؟",
        ]

        for query in discussion_queries:
            msgs = self.ai._build_messages(self.group_id, self.user_id, query)
            sys_content = msgs[0]["content"]

            # باید شامل پیام‌های ردوبدل شده اخیر باشد
            self.assertIn("آخرین پیام‌های ردوبدل‌شده اعضای گروه", sys_content)
            self.assertIn("متن پیام آزمایشی شماره 15", sys_content)

    # -----------------------------------------------------------------------
    # ۳) سوال درباره اعضای گروه فهرست اعضا را اضافه می‌کند
    # -----------------------------------------------------------------------
    def test_03_asking_about_members_includes_members_list(self):
        """پرسش درباره اعضا یا یافتن یک کاربر باید فهرست اعضا را در پرامپت قرار دهد."""
        member_queries = [
            "یه کاربر با اسم عضو شماره 5 هست تو گروه پیداش کن",
            "لیست اعضای فعال گروه رو برام بگو",
            "کی تو گروه حضور داره؟",
        ]

        for query in member_queries:
            msgs = self.ai._build_messages(self.group_id, self.user_id, query)
            sys_content = msgs[0]["content"]

            # باید شامل فهرست اعضا باشد
            self.assertIn("فهرست اعضا و کاربران شناخته‌شده در گروه", sys_content)
            self.assertIn("عضو شماره 5", sys_content)
            self.assertIn("@user_5", sys_content)

    # -----------------------------------------------------------------------
    # ۴) سوال درباره مشخصات گروه اطلاعات گروه را اضافه می‌کند
    # -----------------------------------------------------------------------
    def test_04_asking_about_group_meta_includes_group_info(self):
        """پرسش درباره سازنده یا مدیران گروه باید متادیتای گروه را اضافه کند."""
        meta_queries = [
            "مالک گروه کیست؟",
            "ادمین‌های این گروه کیان؟",
            "اطلاعات این گروه چیه؟",
        ]

        for query in meta_queries:
            msgs = self.ai._build_messages(self.group_id, self.user_id, query)
            sys_content = msgs[0]["content"]

            self.assertIn("اطلاعات گروه فعلی:", sys_content)
            self.assertIn("گروه توسعه روباه", sys_content)
            self.assertIn("@owner_dev", sys_content)
            self.assertIn("@admin_1", sys_content)

    # -----------------------------------------------------------------------
    # ۵) ثبت واقعی مصرف توکن بر اساس گزارش API
    # -----------------------------------------------------------------------
    def test_05_real_token_usage_recorded_from_api_response(self):
        """مصرف واقعی توکن باید مستقیماً از روی فیلد usage پاسخ API ذخیره و لاگ شود."""
        client = FakeClient()
        core = BotCore(self.cfg, self.store, sender=self.sender, ai=self.ai)

        # شبیه‌سازی بازگشت مصرف توکن توسط مدل
        self.fake_ai.usage = {
            "prompt_tokens": 142,
            "completion_tokens": 58,
            "total_tokens": 200,
        }

        msg_ev = FakeEvent(
            "ai سلام وقت بخیر",
            user_id=self.user_id,
            chat_id=self.group_id,
            msg_id=400,
            reply_to=FakeReplyMessage(sender_id=client.me_id, msg_id=399, out=True),
        )

        run(core.on_new_message(client, msg_ev))

        # بررسی ثبت مصرف در هوش مصنوعی
        self.assertIn(self.group_id, self.ai.last_token_usage)
        usage = self.ai.last_token_usage[self.group_id]
        self.assertEqual(usage["prompt_tokens"], 142)
        self.assertEqual(usage["completion_tokens"], 58)
        self.assertEqual(usage["total_tokens"], 200)

        self.assertEqual(self.ai.total_tokens_used["prompt"], 142)
        self.assertEqual(self.ai.total_tokens_used["completion"], 58)
        self.assertEqual(self.ai.total_tokens_used["total"], 200)

    # -----------------------------------------------------------------------
    # ۶) کنترل سقف طول تاریخچه خصوصی کاربر
    # -----------------------------------------------------------------------
    def test_06_user_history_capped_to_prevent_prompt_inflation(self):
        """پاسخ‌های بسیار طولانی نباید باعث تورم بیش از حد تاریخچه در دور بعدی شوند."""
        huge_reply = "الف" * 2000
        self.ai._push_history(self.group_id, self.user_id, "assistant", huge_reply)

        key = (self.group_id, self.user_id)
        history = list(self.ai._history[key])
        self.assertEqual(len(history), 1)
        # طول محتوا در تاریخچه به ۶۰۰ کاراکتر محدود شده است
        self.assertLessEqual(len(history[0]["content"]), 600)

    # -----------------------------------------------------------------------
    # ۷) کاهش چشمگیر طول پرامپت سیستمی همراه با حفظ اصول
    # -----------------------------------------------------------------------
    def test_07_system_prompt_compressed_under_2000_characters(self):
        """پرامپت سیستمی باید کمتر از ۲۰۰۰ کاراکتر باشد اما تمام نیازمندی‌های حیاتی را حفظ کند."""
        prompt = brand.AI_SYSTEM_PROMPT
        self.assertLess(len(prompt), 2000, f"پرامپت بیش از حد طولانی است: {len(prompt)} کاراکتر")
        self.assertIn("روباه", prompt)
        self.assertIn("فارسی روان", prompt)
        self.assertIn("کلیشه‌ها", prompt)
        self.assertIn("سیکتیر", prompt)
        self.assertIn("توهم", prompt)
        self.assertIn("نرم‌افزار", prompt)
        self.assertIn("<think>", prompt)
        self.assertIn("Display Name", prompt)
        self.assertIn("۵۰ دقیقه", prompt)
        self.assertIn("剛才", prompt)

    # -----------------------------------------------------------------------
    # ۸) دستور ai usage توسط مالک سراسری و نمایش باقیمانده سهمیه ۱۰٬۰۰۰ نورون
    # -----------------------------------------------------------------------
    def test_08_ai_usage_command_by_global_owner(self):
        """مالک سراسری با دستور ai usage گزارش سهمیه ۱۰٬۰۰۰ نورون و خرج ۱۵۰ جمله مدل‌ها را می‌بیند."""
        client = FakeClient()
        core = BotCore(self.cfg, self.store, sender=self.sender, ai=self.ai)

        # ارسال یک پیام و شبیه‌سازی مصرف توکن
        self.fake_ai.usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "total_tokens": 1200,
        }
        msg_ev = FakeEvent(
            "ai تست",
            user_id=self.user_id,
            chat_id=self.group_id,
            msg_id=500,
            reply_to=FakeReplyMessage(sender_id=client.me_id, msg_id=499, out=True),
        )
        run(core.on_new_message(client, msg_ev))

        # اجرای دستور ai usage توسط مالک سراسری
        usage_ev = FakeEvent(
            "ai usage",
            user_id=self.user_id,
            chat_id=self.group_id,
            msg_id=501,
        )
        client.clear_requests()
        run(core.on_new_message(client, usage_ev))

        # باید پاسخ حاوی گزارش کامل باشد
        sent_texts = client.text_messages()
        self.assertGreaterEqual(len(sent_texts), 1)
        report = sent_texts[-1]

        # بررسی باقیمانده سهمیه واقعی ۱۰٬۰۰۰ نورون
        self.assertIn("گزارش سهمیه واقعی Cloudflare Workers AI", report)
        self.assertIn("۱۰,۰۰۰ نورون", report)
        self.assertIn("سهمیه باقیمانده", report)
        self.assertIn("۰۰:۰۰ UTC", report)

        # بررسی گزارش هر ۳ مدل برای ۱۵۰ جمله
        self.assertIn("تحلیل هزینه هر ۳ مدل در صورت نگارش ۱۵۰ جمله", report)
        self.assertIn("مدل ۱ — دستیار سریع و عمومی (Llama 3.1 8B)", report)
        self.assertIn("مدل ۲ — دستیار استدلالی متعادل (Llama 3.3 70B)", report)
        self.assertIn("مدل ۳ — پیشرفته استدلال و کدنویسی (Qwen 2.5 Coder 32B)", report)
        self.assertIn("۱۵۰ پیام مجزا", report)
        self.assertIn("نورون", report)

    # -----------------------------------------------------------------------
    # ۹) رد دستور ai usage برای کاربران غیرمالک سراسری
    # -----------------------------------------------------------------------
    def test_09_ai_usage_command_rejected_for_non_global_owner(self):
        """کاربران غیرمالک سراسری (اعضای عادی یا مدیران) مجاز به اجرای ai usage نیستند."""
        client = FakeClient()
        core = BotCore(self.cfg, self.store, sender=self.sender, ai=self.ai)

        non_owner_id = 998877
        usage_ev = FakeEvent(
            "ai usage",
            user_id=non_owner_id,
            chat_id=self.group_id,
            msg_id=601,
        )
        client.clear_requests()
        run(core.on_new_message(client, usage_ev))

        sent_texts = client.text_messages()
        self.assertGreaterEqual(len(sent_texts), 1)
        self.assertIn("فقط برای مالک سراسری ربات مجاز است", sent_texts[-1])


if __name__ == "__main__":
    unittest.main()
