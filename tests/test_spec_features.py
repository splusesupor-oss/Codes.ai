"""
مجموعه تست‌های جامع قابلیت‌های جدید بر اساس مشخصات فنی (Acode AI Specification).

پوشش موارد ده‌گانه:
  ۱) مرزهای دسترسی Global Owner و Registered Bot Owner
  ۲) دستور Tery ai و انتساب مالک ربات
  ۳) سهمیه درخواست روزانه (N پیام) و سقف اعضای مجاز (N عضو) با اعداد فارسی و انگلیسی
  ۴) معافیت Registered Bot Owner از مجوز و شمارش درخواست‌هایش در سهمیه گروه
  ۵) ریست در 00:00 به وقت Asia/Tehran و تست مرز نیمه‌شب و ری‌استارت
  ۶) انقضای ربات (N day) و غیرفعال‌سازی خودکار و تمدید توسط مالک سراسری
  ۷) فعال‌سازی گروه با «ai x cod» و اعلان با قالب‌بندی تزئینی، نام گروه، مالک و مدیران
  ۸) دستور «راهنما» و انتیتی‌های Blockquote و Bold به همراه فال‌بک
  ۹) دستورات «ai plun» و «ai» («جانم 👾»)
  ۱۰) امنیت، اعتبارسنجی مقادیر و مدیریت داده‌های ناقص API
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import tempfile
import unittest
from pathlib import Path

import brand
from ai_client import AIError
from ai_service import GroupAI, tehran_day
from config import Config
from core import BotCore, match_global_config_command, normalize_text
from sender import BrandSender
from splusthon import types
from storage import OwnerStore
from tests.fakes import (
    FakeAI,
    FakeChat,
    FakeClient,
    FakeEvent,
    FakeReplyMessage,
    FakeUserParticipant,
)

try:
    import zoneinfo
    TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30), name="Asia/Tehran")
GROUP_A = -1001
GROUP_B = -2002
GLOBAL_OWNER_ID = 1000
REG_OWNER_ID = 2000
USER_1 = 3001
USER_2 = 3002
USER_3 = 3003
USER_4 = 3004


def run(coro):
    return asyncio.run(coro)


class SpecBaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite3"
        self.cfg = Config()
        self.store = OwnerStore(self.db)
        self.client = FakeClient()
        self.ai_client = FakeAI(reply="پاسخ تستی هوش مصنوعی")
        self.sender = BrandSender(self.cfg)
        self.current_time = _dt.datetime(2026, 10, 9, 12, 0, 0, tzinfo=TEHRAN_TZ)

        def now_provider(tz=None):
            if tz is not None:
                return self.current_time.astimezone(tz)
            return self.current_time

        self.ai = GroupAI(
            self.cfg, self.store, self.sender, self.ai_client, now_provider=now_provider
        )
        self.core = BotCore(self.cfg, self.store, self.sender, ai=self.ai)

        # ثبت پیش‌فرض Global Owner
        self.store.claim(
            GLOBAL_OWNER_ID,
            chat_id=GROUP_A,
            message_id=1,
            username="global_owner",
            display_name="مالک سراسری",
        )
        self.store.set_global_suspended(False)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def send(
        self,
        text: str,
        *,
        user_id: int,
        chat_id: int = GROUP_A,
        is_group: bool = True,
        reply_to=None,
        username=None,
        display_name=None,
        chat_title=None,
    ):
        event = FakeEvent(
            text,
            user_id=user_id,
            chat_id=chat_id,
            is_group=is_group,
            reply_to=reply_to,
            username=username,
            display_name=display_name,
            chat_title=chat_title,
        )
        await self.core.on_new_message(self.client, event)

    def bot_reply(self, msg_id=99):
        return FakeReplyMessage(
            sender_id=self.client.me_id,
            display_name="acod",
            msg_id=msg_id,
            text="...",
            out=True,
        )

    def user_reply(self, user_id, username=None, display_name="کاربر", msg_id=88):
        return FakeReplyMessage(
            sender_id=user_id,
            username=username,
            display_name=display_name,
            msg_id=msg_id,
            out=False,
        )


class TestPermissionsAndRegisteredOwner(SpecBaseTestCase):
    """بخش ۱: مالک سراسری و مالک ثبت‌شده (Registered Bot Owner)."""

    def test_global_owner_can_register_bot_owner_via_tery_ai(self):
        reply = self.user_reply(REG_OWNER_ID, username="bot_admin", display_name="ادمین ربات")
        run(self.send("Tery ai", user_id=GLOBAL_OWNER_ID, reply_to=reply))

        self.assertTrue(self.store.is_registered_bot_owner(REG_OWNER_ID))
        rec = self.store.get_registered_bot_owner()
        self.assertEqual(rec.user_id, REG_OWNER_ID)
        self.assertEqual(rec.registered_by, GLOBAL_OWNER_ID)

        last_msg = self.client.requests[-1].message
        self.assertIn("𝗥𝗘𝗚𝗜𝗦𝗧𝗘𝗥𝗘𝗗 𝗢𝗪𝗡𝗘𝗥", last_msg)
        self.assertIn("@bot_admin", last_msg)

    def test_tery_ai_without_reply_asks_for_reply(self):
        run(self.send("Tery ai", user_id=GLOBAL_OWNER_ID, reply_to=None))
        self.assertFalse(self.store.is_registered_bot_owner(REG_OWNER_ID))
        last_msg = self.client.requests[-1].message
        self.assertIn("Reply", last_msg)

    def test_ordinary_user_cannot_register_bot_owner(self):
        reply = self.user_reply(REG_OWNER_ID, username="bot_admin")
        run(self.send("Tery ai", user_id=USER_1, reply_to=reply))
        self.assertFalse(self.store.is_registered_bot_owner(REG_OWNER_ID))

    def test_registered_bot_owner_cannot_register_another_owner(self):
        # ثبت مالک اول
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)
        # تلاش مالک ثبت‌شده برای ثبت کاربر دیگر
        reply = self.user_reply(USER_2)
        run(self.send("Tery ai", user_id=REG_OWNER_ID, reply_to=reply))
        # هنوز مالک قبلی است و تغییر نکرده
        self.assertEqual(self.store.get_registered_bot_owner().user_id, REG_OWNER_ID)

    def test_replacing_registered_bot_owner(self):
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)
        reply2 = self.user_reply(USER_3, username="new_admin")
        run(self.send("Tery ai", user_id=GLOBAL_OWNER_ID, reply_to=reply2))
        self.assertFalse(self.store.is_registered_bot_owner(REG_OWNER_ID))
        self.assertTrue(self.store.is_registered_bot_owner(USER_3))

    def test_registered_bot_owner_can_manage_ai(self):
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)

        # ai online
        run(self.send("ai online", user_id=REG_OWNER_ID))
        self.assertTrue(self.store.ai_is_enabled(GROUP_A))

        # ai list
        reply = self.user_reply(USER_1, username="ali")
        run(self.send("ai list", user_id=REG_OWNER_ID, reply_to=reply))
        self.assertTrue(self.store.ai_is_allowed(GROUP_A, USER_1))

        # ai L
        run(self.send("ai L", user_id=REG_OWNER_ID))
        self.assertIn("@ali", self.client.requests[-1].message)

        # ai list x
        run(self.send("ai list x", user_id=REG_OWNER_ID, reply_to=reply))
        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_1))

        # ai of
        run(self.send("ai of", user_id=REG_OWNER_ID))
        self.assertFalse(self.store.ai_is_enabled(GROUP_A))

    def test_ordinary_user_cannot_use_ai_admin_commands(self):
        run(self.send("ai online", user_id=USER_1))
        self.assertFalse(self.store.ai_is_enabled(GROUP_A))

        reply = self.user_reply(USER_2)
        run(self.send("ai list", user_id=USER_1, reply_to=reply))
        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_2))


class TestDailyQuotaAndMemberLimits(SpecBaseTestCase):
    """بخش ۲: سهمیه پیام روزانه و سقف اعضای مجاز."""

    def test_global_owner_sets_daily_quota_english_and_persian(self):
        run(self.send("150 پیام", user_id=GLOBAL_OWNER_ID))
        self.assertEqual(self.store.get_daily_quota(), 150)
        self.assertIn("150 پیام", self.client.requests[-1].message)

        run(self.send("۸۰ پیام", user_id=GLOBAL_OWNER_ID))
        self.assertEqual(self.store.get_daily_quota(), 80)
        self.assertIn("80 پیام", self.client.requests[-1].message)

    def test_global_owner_sets_max_users_english_and_persian(self):
        run(self.send("5 عضو", user_id=GLOBAL_OWNER_ID))
        self.assertEqual(self.store.get_max_allowed_users(), 5)
        self.assertIn("5 عضو", self.client.requests[-1].message)

        run(self.send("۲ عضو", user_id=GLOBAL_OWNER_ID))
        self.assertEqual(self.store.get_max_allowed_users(), 2)
        self.assertIn("2 عضو", self.client.requests[-1].message)

    def test_non_owner_cannot_change_quota_or_members(self):
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)
        initial_quota = self.store.get_daily_quota()
        initial_members = self.store.get_max_allowed_users()

        # کاربر عادی
        run(self.send("50 پیام", user_id=USER_1))
        run(self.send("10 عضو", user_id=USER_1))

        # مالک ثبت‌شده
        run(self.send("50 پیام", user_id=REG_OWNER_ID))
        run(self.send("10 عضو", user_id=REG_OWNER_ID))

        self.assertEqual(self.store.get_daily_quota(), initial_quota)
        self.assertEqual(self.store.get_max_allowed_users(), initial_members)

    def test_member_limit_enforcement(self):
        # تنظیم سقف به ۲ عضو
        run(self.send("2 عضو", user_id=GLOBAL_OWNER_ID))
        self.store.ai_set_enabled(GROUP_A, True)

        # اضافه کردن عضو ۱
        r1 = self.user_reply(USER_1, username="u1")
        run(self.send("ai list", user_id=GLOBAL_OWNER_ID, reply_to=r1))
        self.assertTrue(self.store.ai_is_allowed(GROUP_A, USER_1))

        # اضافه کردن عضو ۲
        r2 = self.user_reply(USER_2, username="u2")
        run(self.send("ai list", user_id=GLOBAL_OWNER_ID, reply_to=r2))
        self.assertTrue(self.store.ai_is_allowed(GROUP_A, USER_2))

        # تلاش برای عضو ۳ -> باید ریجکت شود
        r3 = self.user_reply(USER_3, username="u3")
        run(self.send("ai list", user_id=GLOBAL_OWNER_ID, reply_to=r3))
        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_3))
        self.assertIn("سقف اعضای مجاز", self.client.requests[-1].message)

        # به‌روزرسانی مجدد عضو ۱ (سهمیه اضافه مصرف نمی‌کند)
        run(self.send("ai list", user_id=GLOBAL_OWNER_ID, reply_to=r1))
        self.assertTrue(self.store.ai_is_allowed(GROUP_A, USER_1))

    def test_registered_bot_owner_privileges_and_quota_consumption(self):
        """مالک ثبت‌شده نیازی به مجوز ندارد اما مصرفش از سهمیه کسر می‌شود."""
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.set_daily_quota(2)

        today = tehran_day(self.current_time)
        self.assertEqual(self.store.ai_used_quota(GROUP_A, today), 0)

        # گفت‌وگوی اول مالک ثبت‌شده
        reply = self.bot_reply()
        run(self.send("سلام هوش مصنوعی", user_id=REG_OWNER_ID, reply_to=reply))
        self.assertEqual(self.store.ai_used_quota(GROUP_A, today), 1)

        # گفت‌وگوی دوم
        run(self.send("سوال بعدی", user_id=REG_OWNER_ID, reply_to=reply))
        self.assertEqual(self.store.ai_used_quota(GROUP_A, today), 2)

        # درخواست سوم -> سهمیه تمام شده
        run(self.send("سوال سوم", user_id=REG_OWNER_ID, reply_to=reply))
        self.assertEqual(self.store.ai_used_quota(GROUP_A, today), 2)
        self.assertIn(brand.AI_QUOTA_TEXT, self.client.requests[-1].message)

    def test_failed_upstream_request_does_not_consume_quota(self):
        """خطای بالادستی AI نباید سهمیه کاربر را بسوزاند."""
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.set_daily_quota(10)

        today = tehran_day(self.current_time)
        self.assertEqual(self.store.ai_used_quota(GROUP_A, today), 0)

        # تنظیم کلاینت AI روی خطا
        self.ai_client.error = AIError("خطای موقت بالادست")
        reply = self.bot_reply()
        run(self.send("سلام", user_id=REG_OWNER_ID, reply_to=reply))

        # سهمیه مصرف نشده است
        self.assertEqual(self.store.ai_used_quota(GROUP_A, today), 0)
        self.assertIn(brand.AI_ERROR_TEXT, self.client.requests[-1].message)


class TestTehranMidnightReset(SpecBaseTestCase):
    """بخش ۲ (ادامه): ریست سهمیه در 00:00 ساعت رسمی تهران."""

    def test_midnight_rollover(self):
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.set_daily_quota(5)

        # ساعت 23:59:50 تهران در تاریخ 2026-10-09
        self.current_time = _dt.datetime(2026, 10, 9, 23, 59, 50, tzinfo=TEHRAN_TZ)
        today = tehran_day(self.current_time)
        self.assertEqual(today, "2026-10-09")

        reply = self.bot_reply()
        run(self.send("پیام قبل از نیمه‌شب", user_id=REG_OWNER_ID, reply_to=reply))
        self.assertEqual(self.store.ai_used_quota(GROUP_A, "2026-10-09"), 1)

        # تغییر زمان به 00:00:10 تاریخ 2026-10-10
        self.current_time = _dt.datetime(2026, 10, 10, 0, 0, 10, tzinfo=TEHRAN_TZ)
        tomorrow = tehran_day(self.current_time)
        self.assertEqual(tomorrow, "2026-10-10")

        # مصرف دیروز همچنان ۱ است
        self.assertEqual(self.store.ai_used_quota(GROUP_A, "2026-10-09"), 1)
        # مصرف امروز ۰ است
        self.assertEqual(self.store.ai_used_quota(GROUP_A, "2026-10-10"), 0)

        # ارسال پیام جدید در روز جدید
        run(self.send("پیام بعد از نیمه‌شب", user_id=REG_OWNER_ID, reply_to=reply))
        self.assertEqual(self.store.ai_used_quota(GROUP_A, "2026-10-10"), 1)

    def test_utc_timezone_device_still_respects_tehran_midnight(self):
        # زمان در UTC معادل 20:29:50 (ساعت 23:59:50 تهران)
        utc_dt = _dt.datetime(2026, 10, 9, 20, 29, 50, tzinfo=_dt.timezone.utc)
        self.assertEqual(tehran_day(utc_dt), "2026-10-09")

        # ۱۰ ثانیه بعد در UTC معادل 20:30:00 (ساعت 00:00:00 تهران در روز بعد)
        utc_next = _dt.datetime(2026, 10, 9, 20, 30, 0, tzinfo=_dt.timezone.utc)
        self.assertEqual(tehran_day(utc_next), "2026-10-10")


class TestBotExpiration(SpecBaseTestCase):
    """بخش ۵: انقضای سرویس ربات."""

    def test_global_owner_configures_expiration(self):
        run(self.send("4 day", user_id=GLOBAL_OWNER_ID))
        exp = self.store.get_expiration()
        self.assertIsNotNone(exp)
        expected = self.current_time + _dt.timedelta(days=4)
        self.assertEqual(exp.date(), expected.date())
        self.assertIn("4 روز", self.client.requests[-1].message)

    def test_expiration_persian_numerals(self):
        run(self.send("۷ day", user_id=GLOBAL_OWNER_ID))
        exp = self.store.get_expiration()
        expected = self.current_time + _dt.timedelta(days=7)
        self.assertEqual(exp.date(), expected.date())

    def test_registered_bot_owner_cannot_set_expiration(self):
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)
        run(self.send("10 day", user_id=REG_OWNER_ID))
        self.assertIsNone(self.store.get_expiration())

    def test_expired_bot_rejects_ai_and_group_activation(self):
        # تنظیم انقضا برای ۲ روز
        run(self.send("2 day", user_id=GLOBAL_OWNER_ID))
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_allow_user(GROUP_A, USER_1)

        # هنوز منقضی نشده
        reply = self.bot_reply()
        run(self.send("سلام", user_id=USER_1, reply_to=reply))
        self.assertEqual(self.ai_client.calls[-1][-1]["content"], "سلام")

        # ۳ روز جلو می‌رویم (منقضی می‌شود)
        self.current_time += _dt.timedelta(days=3)
        self.assertTrue(self.store.is_expired(self.current_time))

        # تلاش برای پیام AI -> پاسخ انقضا
        run(self.send("سلام مجدد", user_id=USER_1, reply_to=reply))
        self.assertIn(brand.BOT_EXPIRED_TEXT, self.client.requests[-1].message)

        # تلاش برای فعال‌سازی گروه -> پاسخ انقضا
        run(self.send("ai x cod", user_id=GLOBAL_OWNER_ID))
        self.assertIn(brand.BOT_EXPIRED_TEXT, self.client.requests[-1].message)

    def test_global_owner_can_renew_expired_bot(self):
        run(self.send("1 day", user_id=GLOBAL_OWNER_ID))
        self.current_time += _dt.timedelta(days=2)
        self.assertTrue(self.store.is_expired(self.current_time))

        # تمدید توسط مالک سراسری با ۳۰ روز
        run(self.send("30 day", user_id=GLOBAL_OWNER_ID))
        self.assertFalse(self.store.is_expired(self.current_time))


class TestPersianHelpAndPlunAndSingleAi(SpecBaseTestCase):
    """بخش ۴ و ۶: دستورات راهنما، ai plun و ai."""

    def test_help_command_formatting_and_entities(self):
        run(self.send("راهنما", user_id=USER_1))

        last_req = self.client.requests[-1]
        msg = last_req.message
        entities = getattr(last_req, "entities", [])

        # بررسی متن
        self.assertIn(brand.HELP_TITLE, msg)
        self.assertIn("ai list", msg)
        self.assertIn("ai list x", msg)
        self.assertIn("ai of", msg)
        self.assertIn("ai online", msg)
        self.assertIn("ai L", msg)
        self.assertIn("ai plun", msg)
        self.assertIn("ai", msg)

        # بررسی entityها
        has_blockquote = any(isinstance(e, types.MessageEntityBlockquote) for e in entities)
        has_bold = any(isinstance(e, types.MessageEntityBold) for e in entities)
        self.assertTrue(has_blockquote)
        self.assertTrue(has_bold)

    def test_ai_plun_command(self):
        self.store.set_daily_quota(100)
        self.store.ai_set_enabled(GROUP_A, True)
        today = tehran_day(self.current_time)
        self.store.ai_consume_quota(GROUP_A, today, 100)
        self.store.ai_consume_quota(GROUP_A, today, 100)

        run(self.send("ai plun", user_id=USER_1))
        last_msg = self.client.requests[-1].message
        self.assertIn("سهمیه هوش مصنوعی این گروه", last_msg)
        self.assertIn("100", last_msg)
        self.assertIn("2", last_msg)
        self.assertIn("98", last_msg)

    def test_ai_single_call(self):
        run(self.send("ai", user_id=USER_1))
        last_msg = self.client.requests[-1].message
        self.assertEqual(last_msg, "جانم 👾")
        self.assertEqual(len(self.ai_client.calls), 0)


class TestAiXCodAnnouncement(SpecBaseTestCase):
    """بخش ۷: فعال‌سازی گروه و اعلامیه."""

    def test_ai_xcod_successful_announcement_with_metadata(self):
        # شبیه‌سازی اعضای گروه
        creator = FakeUserParticipant(1100, username="boss", is_creator=True)
        admin1 = FakeUserParticipant(1101, username="mod1", is_admin=True)
        admin2 = FakeUserParticipant(1102, first_name="رضا", last_name="مدیر", is_admin=True)
        ordinary = FakeUserParticipant(1103, is_creator=False, is_admin=False)

        self.client.participants[GROUP_A] = [creator, admin1, admin2, ordinary]

        run(self.send("ai x cod", user_id=GLOBAL_OWNER_ID, chat_title="انجمن کدرز"))

        self.assertTrue(self.store.is_group_activated(GROUP_A))
        self.assertTrue(self.store.ai_is_enabled(GROUP_A))

        last_req = self.client.requests[-1]
        msg = last_req.message

        self.assertIn("#𝗮𝗶 𝗰𝗼𝗱𝗲 𝗽𝗹𝘂𝘀 𝟓", msg)
        self.assertIn("「انجمن کدرز」", msg)
        self.assertIn("@boss", msg)
        self.assertIn("@mod1", msg)
        self.assertIn("رضا مدیر", msg)
        self.assertNotIn("1103", msg)  # کاربر عادی نباید در ادمین‌ها باشد
        self.assertIn("برای آشنایی با هوش مصنوعی کلمه راهنما را بفرستید", msg)

        # خط پایانی باید هم bold و هم blockquote باشد
        entities = getattr(last_req, "entities", [])
        self.assertTrue(any(isinstance(e, types.MessageEntityBlockquote) for e in entities))
        self.assertTrue(any(isinstance(e, types.MessageEntityBold) for e in entities))

    def test_ai_xcod_graceful_fallback_without_metadata(self):
        # بدون اطلاعات اعضا
        self.client.participants[GROUP_A] = []
        run(self.send("ai x cod", user_id=GLOBAL_OWNER_ID, chat_title=None))

        msg = self.client.requests[-1].message
        self.assertIn("#𝗮𝗶 𝗰𝗼𝗱𝗲 𝗽𝗹𝘂𝘀 𝟓", msg)
        self.assertIn("مالک یافت نشد", msg)
        self.assertIn("مدیری یافت نشد", msg)

    def test_non_global_owner_cannot_run_ai_xcod(self):
        self.store.set_registered_bot_owner(REG_OWNER_ID, registered_by=GLOBAL_OWNER_ID)
        run(self.send("ai x cod", user_id=REG_OWNER_ID))
        self.assertFalse(self.store.is_group_activated(GROUP_A))


class TestSecurityAndInputValidation(SpecBaseTestCase):
    """بخش ۸: امنیت و اعتبارسنجی ورودی‌ها."""

    def test_conversational_messages_do_not_trigger_commands(self):
        self.assertIsNone(match_global_config_command("من دیروز ۱۰۰ پیام فرستادم"))
        self.assertIsNone(match_global_config_command("۳ عضو خانواده"))
        self.assertIsNone(match_global_config_command("۴ day off"))
        self.assertIsNone(match_global_config_command("0 پیام"))
        self.assertIsNone(match_global_config_command("-5 عضو"))

    def test_duplicate_admins_and_owner_in_admin_list_are_filtered(self):
        # سناریو: مالک در لیست ادمین‌ها هم هست و ادمین تکراری وجود دارد
        creator = FakeUserParticipant(1100, username="boss", is_creator=True)
        creator_as_admin = FakeUserParticipant(1100, username="boss", is_admin=True)
        admin1 = FakeUserParticipant(1101, username="mod1", is_admin=True)
        admin1_dup = FakeUserParticipant(1101, username="mod1", is_admin=True)

        self.client.participants[GROUP_A] = [creator, creator_as_admin, admin1, admin1_dup]
        run(self.send("ai x cod", user_id=GLOBAL_OWNER_ID, chat_title="تست فیلتر"))

        msg = self.client.requests[-1].message
        # boss فقط یک بار زیر OWNER باشد و در ADMIN نباشد
        self.assertEqual(msg.count("@boss"), 1)
        # mod1 فقط یک بار در ADMIN باشد
        self.assertEqual(msg.count("@mod1"), 1)


if __name__ == "__main__":
    unittest.main()
