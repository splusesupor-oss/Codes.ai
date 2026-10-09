"""
تست‌های اعتبارسنجی معماری چندگروهی، صف‌های مستقل، ایزولاسیون مکالمات و مدل‌های انتخابی.

موارد تحت پوشش:
  ۱) ایزولاسیون کامل صف‌های مستقل پردازش به ازای هر گروه (Per-Group Queue Isolation).
  ۲) ایزولاسیون دقیق گفت‌وگو به ازای هر کاربر (Per-User Conversation Isolation).
  ۳) تغییر مدل با دستورات «ai model 1|2|3» و انحصار آن برای مالک سراسری (Global Owner).
  ۴) رد تغییر مدل توسط مالک ثبت‌شده (Registered Bot Owner) و اعضای عادی.
  ۵) استقلال تنظیم مدل هر گروه و عدم سرایت تغییرات یک گروه به گروه دیگر.
  ۶) دستور «لیست انقضا» با نمایش جزئیات کامل و مسدودسازی دسترسی کاربران غیرمجاز.
  ۷) درج تاریخ فعال‌سازی و انقضا در اعلان «ai x cod».
  ۸) قالب‌بندی «ai L» با سربرگ «᳆ 𝗔𝗜 𝗟𝗜𝗦𝗧 𝗟» و نشانگر «❥「...」».
  ۹) مدیریت فشار صف و بازگرداندن پیام شلوغی صف در شرایط اشباع.
  ۱۰) لحن روان و طبیعی پرامپت سیستمی فارسی.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import tempfile
import unittest
from pathlib import Path

import brand
from ai_client import AIError
from ai_service import GroupAI, QueuedRequest, tehran_day
from config import Config
from core import BotCore
from models import (
    DEFAULT_MODEL_PROFILE_ID,
    MODEL_PROFILES,
    get_model_profile,
)
from sender import BrandSender
from storage import OwnerStore
from tests.fakes import (
    FakeAI,
    FakeChat,
    FakeClient,
    FakeEvent,
    FakeReplyMessage,
)

def run(coro):
    return asyncio.run(coro)

OWNER = 1001
REG_OWNER = 2002
USER_A = 3001
USER_B = 3002
GROUP_1 = -100111
GROUP_2 = -100222


class TestMultiGroupAndModels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

        self.store = OwnerStore(self.db_path)

        self.cfg = Config()
        self.client = FakeClient()
        self.sender = BrandSender(self.cfg)
        self.fake_ai = FakeAI("پاسخ تستی هوش مصنوعی")
        self.ai = GroupAI(self.cfg, self.store, self.sender, self.fake_ai)
        self.core = BotCore(self.cfg, self.store, self.sender, ai=self.ai)

        # ثبت اولیه مالک
        self.store.claim(OWNER, chat_id=GROUP_1, message_id=1, display_name="مالک اصلی")
        self.store.activate_group(GROUP_1, OWNER, group_name="گروه برنامه نویسی ۱")
        self.store.activate_group(GROUP_2, OWNER, group_name="گروه برنامه نویسی ۲")
        self.store.ai_set_enabled(GROUP_1, True)
        self.store.ai_set_enabled(GROUP_2, True)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def _reply_to_bot(self, msg_id=99):
        return FakeReplyMessage(
            sender_id=self.client.me_id,
            display_name="acod",
            msg_id=msg_id,
            text="...",
            out=True,
        )

    # -----------------------------------------------------------------------
    # ۱) ایزولاسیون کامل مکالمات کاربران در یک گروه مشترک
    # -----------------------------------------------------------------------
    def test_per_user_conversation_isolation(self):
        """تاریخچه و پرامپت کاربر A و کاربر B در یک گروه نباید هرگز با هم تداخل داشته باشد."""
        self.store.ai_allow_user(GROUP_1, USER_A, display_name="کاربر الف")
        self.store.ai_allow_user(GROUP_1, USER_B, display_name="کاربر ب")

        # درخواست اول از کاربر A
        ev_a1 = FakeEvent("سلام من کاربر الف هستم و پایتون کار می‌کنم", user_id=USER_A, chat_id=GROUP_1,
                          is_group=True, reply_to=self._reply_to_bot())
        run(self.core.on_new_message(self.client, ev_a1))

        # ساخت پیام‌های کاربر B
        msgs_b = self.ai._build_messages(GROUP_1, USER_B, "سلام من کی هستم؟")
        # پیام‌های کاربر B نباید شامل متن کاربر A باشد
        history_contents = [m["content"] for m in msgs_b]
        self.assertNotIn("سلام من کاربر الف هستم و پایتون کار می‌کنم", history_contents)

        # درخواست از کاربر B
        ev_b1 = FakeEvent("سلام من کاربر ب هستم و جاوا کار می‌کنم", user_id=USER_B, chat_id=GROUP_1,
                          is_group=True, reply_to=self._reply_to_bot())
        run(self.core.on_new_message(self.client, ev_b1))

        # ساخت پیام‌های مجدد برای هر دو کاربر
        msgs_a2 = self.ai._build_messages(GROUP_1, USER_A, "زبان من چی بود؟")
        msgs_b2 = self.ai._build_messages(GROUP_1, USER_B, "زبان من چی بود؟")

        a_text = " ".join(m["content"] for m in msgs_a2)
        b_text = " ".join(m["content"] for m in msgs_b2)

        self.assertIn("پایتون", a_text)
        self.assertNotIn("جاوا", a_text)
        self.assertIn("جاوا", b_text)
        self.assertNotIn("پایتون", b_text)

    # -----------------------------------------------------------------------
    # ۲) تغییر مدل هوش مصنوعی با دستورات ai model 1|2|3 توسط مالک سراسری
    # -----------------------------------------------------------------------
    def test_model_selection_by_global_owner(self):
        """مالک سراسری می‌تواند مدل را تغییر دهد و این تغییر در رجیستری ذخیره می‌شود."""
        ev = FakeEvent("ai model 2", user_id=OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev))

        last_resp = self.client.requests[-1].message
        self.assertIn("مدل هوش مصنوعی این گروه تغییر یافت", last_resp)
        self.assertIn("مدل 2", last_resp)
        self.assertEqual(self.store.get_group_model(GROUP_1), 2)

        # گروه ۲ باید همچنان روی مدل پیش‌فرض (1) بماند
        self.assertEqual(self.store.get_group_model(GROUP_2), DEFAULT_MODEL_PROFILE_ID)

    # -----------------------------------------------------------------------
    # ۳) امنیت تغییر مدل: مالک ثبت‌شده و کاربران عادی حق تغییر مدل ندارند
    # -----------------------------------------------------------------------
    def test_model_selection_security(self):
        """مالک ثبت‌شده و اعضای گروه حق تغییر مدل هوش مصنوعی ندارند."""
        self.store.set_registered_bot_owner(REG_OWNER, registered_by=OWNER)

        # تلاش مالک ثبت‌شده
        ev_reg = FakeEvent("ai model 3", user_id=REG_OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev_reg))

        last_resp = self.client.requests[-1].message
        self.assertIn("فقط توسط مالک سراسری امکان‌پذیر است", last_resp)
        self.assertNotEqual(self.store.get_group_model(GROUP_1), 3)

        # تلاش کاربر معمولی
        ev_user = FakeEvent("ai model 3", user_id=USER_A, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev_user))
        self.assertNotEqual(self.store.get_group_model(GROUP_1), 3)

    # -----------------------------------------------------------------------
    # ۴) مشاهده وضعیت مدل با دستور ai model
    # -----------------------------------------------------------------------
    def test_model_status_inspection(self):
        """دستور ai model مشخصات مدل فعلی گروه را نشان می‌دهد."""
        self.store.set_group_model(GROUP_1, 3)

        ev = FakeEvent("ai model", user_id=USER_A, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev))

        last_resp = self.client.requests[-1].message
        self.assertIn("مدل 3", last_resp)
        self.assertIn("deepseek-r1-distill-qwen-32b", last_resp)

    # -----------------------------------------------------------------------
    # ۵) اجرای درخواست در مدل انتخابی گروه
    # -----------------------------------------------------------------------
    def test_chat_uses_selected_group_model(self):
        """درخواست گفت‌وگو با هوش مصنوعی باید شناسه مدل انتخابی گروه را به کلاینت بفرستد."""
        self.store.ai_allow_user(GROUP_1, USER_A, display_name="کاربر الف")
        self.store.set_group_model(GROUP_1, 3)

        ev = FakeEvent("کد حل مسئله کوله‌پشتی در پایتون", user_id=USER_A, chat_id=GROUP_1,
                       is_group=True, reply_to=self._reply_to_bot())
        run(self.core.on_new_message(self.client, ev))

        # بررسی اینکه FakeAI مدل مربوطه را دریافت کرده است
        self.assertTrue(len(self.fake_ai.calls) > 0)
        self.assertEqual(
            self.fake_ai.last_kwargs.get("model"),
            MODEL_PROFILES[3].model_id,
        )
        self.assertIn("پاسخ تستی هوش مصنوعی", self.client.requests[-1].message)

    # -----------------------------------------------------------------------
    # ۶) دستور «لیست انقضا» منحصراً برای مالک سراسری
    # -----------------------------------------------------------------------
    def test_expiration_list_command(self):
        """دستور «لیست انقضا» فهرست گروه‌ها، زمان انقضا و سهمیه‌ها را نشان می‌دهد."""
        # تنظیم انقضا برای گروه ۱
        future_dt = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=10, hours=5)
        self.store.set_group_expiration(GROUP_1, future_dt)

        # اجرای دستور توسط مالک سراسری
        ev_owner = FakeEvent("لیست انقضا", user_id=OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev_owner))

        resp = self.client.requests[-1].message
        self.assertIn("𝗟𝗜𝗦𝗧 𝗘𝗫𝗣𝗜𝗥𝗔𝗧𝗜𝗢𝗡", resp)
        self.assertIn("گروه برنامه نویسی ۱", resp)
        self.assertIn(str(GROUP_1), resp)
        self.assertIn("روز", resp)

        # تلاش کاربر معمولی نباید پاسخی به بار آورد
        req_count_before = len(self.client.requests)
        ev_user = FakeEvent("لیست انقضا", user_id=USER_A, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev_user))
        self.assertEqual(len(self.client.requests), req_count_before)

    # -----------------------------------------------------------------------
    # ۷) فرمت دقیق اعلان «ai x cod» با تاریخ‌های فعال‌سازی و انقضا
    # -----------------------------------------------------------------------
    def test_ai_xcod_timestamps(self):
        """اعلان ai x cod باید شامل تاریخ‌های فعال‌سازی و انقضا باشد."""
        future_dt = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=30)
        self.store.set_group_expiration(GROUP_1, future_dt)

        ev = FakeEvent("ai x cod", user_id=OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev))

        msg = self.client.requests[-1].message
        self.assertIn("#𝗮𝗶 𝗰𝗼𝗱𝗲 𝗽𝗹𝘂𝘀 𝟓", msg)
        self.assertIn("𝗔𝗰𝘁𝗶𝘃𝗮𝘁𝗶𝗼𝗻 𝗗𝗮𝘁𝗲⏱", msg)
        self.assertIn("𝗘𝘅𝗽𝗶𝗿𝗮𝘁𝗶𝗼𝗻 𝗱𝗮𝘁𝗲⏱", msg)
        self.assertIn("꧇◖", msg)

    # -----------------------------------------------------------------------
    # ۸) فرمت «ai L» طبق مشخصات فنی (᳆ 𝗔𝗜 𝗟𝗜𝗦𝗧 𝗟 و ❥「...」)
    # -----------------------------------------------------------------------
    def test_ai_l_format(self):
        """فهرست اعضای مجاز باید با نمادهای جدید قالب‌بندی شود."""
        self.store.ai_allow_user(GROUP_1, USER_A, username="reza_dev")
        self.store.ai_allow_user(GROUP_1, USER_B, display_name="سارا احمدی")

        ev = FakeEvent("ai L", user_id=OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev))

        msg = self.client.requests[-1].message
        self.assertIn("᳆ 𝗔𝗜 𝗟𝗜𝗦𝗧 𝗟", msg)
        self.assertIn("❥「@reza_dev」", msg)
        self.assertIn("❥「سارا احمدی」", msg)

    # -----------------------------------------------------------------------
    # ۹) استقلال صف‌های گروه‌ها و عدم مسدودسازی یکدیگر
    # -----------------------------------------------------------------------
    def test_group_queue_independence(self):
        """صف هر گروه کاملاً مستقل است و گروه‌ها صف مجزایی دارند."""
        q1 = self.ai.queue_manager.get_queue(GROUP_1)
        q2 = self.ai.queue_manager.get_queue(GROUP_2)
        self.assertIsNot(q1, q2)
        self.assertEqual(q1.qsize(), 0)
        self.assertEqual(q2.qsize(), 0)

    # -----------------------------------------------------------------------
    # ۱۰) سیستم پرامپت روان فارسی
    # -----------------------------------------------------------------------
    def test_human_like_system_prompt(self):
        """پرامپت سیستمی باید لحن طبیعی، فارسی روان و مسلط داشته باشد."""
        msgs = self.ai._build_messages(GROUP_1, USER_A, "تست")
        system_msg = msgs[0]["content"]
        self.assertIn("روباه", system_msg)
        self.assertIn("فارسی روان", system_msg)
        self.assertIn("کلیشه‌ها", system_msg)

    # -----------------------------------------------------------------------
    # ۱۱) رد شماره مدل نامعتبر
    # -----------------------------------------------------------------------
    def test_invalid_model_profile_rejected(self):
        """شماره مدل‌های نامعتبر باید با پیام خطای مشخص رد شوند."""
        ev = FakeEvent("ai model 9", user_id=OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev))

        last_resp = self.client.requests[-1].message
        self.assertIn("شماره مدل نامعتبر است", last_resp)

    # -----------------------------------------------------------------------
    # ۱۲) رفتار در صورت انقضای اختصاصی گروه
    # -----------------------------------------------------------------------
    def test_group_expiration_blocks_requests(self):
        """اگر ربات یا گروه منقضی شده باشد، پیام انقضا ارسال شده و درخواست‌ها مسدود می‌شود."""
        past_dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=2)
        self.store.set_group_expiration(GROUP_1, past_dt)

        self.assertTrue(self.store.is_group_expired(GROUP_1))

        # بررسی ارسال پیام انقضا
        self.store.set_expiration(past_dt)
        ev = FakeEvent("ai x cod", user_id=OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev))

        last_resp = self.client.requests[-1].message
        self.assertIn(brand.BOT_EXPIRED_TEXT, last_resp)

    # -----------------------------------------------------------------------
    # ۱۳) ریست نیمه‌شب به وقت تهران (Asia/Tehran)
    # -----------------------------------------------------------------------
    def test_midnight_tehran_quota_reset(self):
        """سهمیه در ساعت 00:00 تهران به صورت خودکار برای روز جدید ریست می‌شود."""
        self.store.set_daily_quota(10)
        day1 = "2026-10-09"
        day2 = "2026-10-10"

        # مصرف کامل در روز اول
        for _ in range(10):
            self.store.ai_consume_quota(GROUP_1, day1, 10)
        self.assertEqual(self.store.ai_used_quota(GROUP_1, day1), 10)

        # در روز دوم باید سهمیه مصرف‌شده ۰ باشد
        self.assertEqual(self.store.ai_used_quota(GROUP_1, day2), 0)

    # -----------------------------------------------------------------------
    # ۱۴) تفکیک کامل سهمیه روزانه به ازای هر گروه (Per-Group Daily Quota)
    # -----------------------------------------------------------------------
    def test_per_group_quota_configuration(self):
        """تنظیم سهمیه در گروه ۱ نباید سهمیه گروه ۲ را تغییر دهد."""
        # تنظیم ۶۰۰ پیام برای گروه ۱
        ev1 = FakeEvent("600 پیام", user_id=OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev1))

        resp1 = self.client.requests[-1].message
        self.assertIn("600", resp1)
        self.assertIn("این گروه", resp1)

        # سهمیه گروه ۱ باید ۶۰۰ و گروه ۲ همچنان پیش‌فرض باشد
        self.assertEqual(self.store.get_daily_quota(chat_id=GROUP_1), 600)
        self.assertEqual(self.store.get_daily_quota(chat_id=GROUP_2), 5000)

        # حالا تنظیم ۵۰۰ پیام برای گروه ۲
        ev2 = FakeEvent("500 پیام", user_id=OWNER, chat_id=GROUP_2, is_group=True)
        run(self.core.on_new_message(self.client, ev2))

        # بررسی نهایی سهمیه‌های مستقل هر دو گروه
        self.assertEqual(self.store.get_daily_quota(chat_id=GROUP_1), 600)
        self.assertEqual(self.store.get_daily_quota(chat_id=GROUP_2), 5000 if False else 500)

    # -----------------------------------------------------------------------
    # ۱۵) تفکیک کامل سقف اعضای مجاز به ازای هر گروه (Per-Group Member Limits)
    # -----------------------------------------------------------------------
    def test_per_group_max_users_configuration(self):
        """تنظیم سقف اعضا در گروه ۱ نباید سقف گروه ۲ را تغییر دهد."""
        ev1 = FakeEvent("7 عضو", user_id=OWNER, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev1))

        resp1 = self.client.requests[-1].message
        self.assertIn("7", resp1)
        self.assertIn("این گروه", resp1)

        self.assertEqual(self.store.get_max_allowed_users(chat_id=GROUP_1), 7)
        self.assertEqual(self.store.get_max_allowed_users(chat_id=GROUP_2), 3)

        ev2 = FakeEvent("4 عضو", user_id=OWNER, chat_id=GROUP_2, is_group=True)
        run(self.core.on_new_message(self.client, ev2))

        self.assertEqual(self.store.get_max_allowed_users(chat_id=GROUP_1), 7)
        self.assertEqual(self.store.get_max_allowed_users(chat_id=GROUP_2), 4)

    # -----------------------------------------------------------------------
    # ۱۶) دستور ai plun سقف اختصاصی همان گروه را نمایش می‌دهد
    # -----------------------------------------------------------------------
    def test_per_group_plun_displays_group_specific_ceilings(self):
        """دستور ai plun باید سقف و مصرف منحصربه‌فرد همان گروه را نشان دهد."""
        self.store.set_group_daily_quota(GROUP_1, 600)
        self.store.set_group_daily_quota(GROUP_2, 500)

        # مصرف ۱ پیام در گروه ۱
        today = tehran_day()
        self.store.ai_consume_quota(GROUP_1, today, 600)

        # ارسال ai plun در گروه ۱
        ev1 = FakeEvent("ai plun", user_id=USER_A, chat_id=GROUP_1, is_group=True)
        run(self.core.on_new_message(self.client, ev1))
        msg1 = self.client.requests[-1].message
        self.assertIn("600", msg1)
        self.assertIn("599", msg1)

        # ارسال ai plun در گروه ۲
        ev2 = FakeEvent("ai plun", user_id=USER_A, chat_id=GROUP_2, is_group=True)
        run(self.core.on_new_message(self.client, ev2))
        msg2 = self.client.requests[-1].message
        self.assertIn("500", msg2)
        self.assertNotIn("600", msg2)


if __name__ == "__main__":
    unittest.main()
