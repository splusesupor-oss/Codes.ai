"""
تست‌های رگرسیون باگ «ربات در گروه روشن نمی‌شود و همیشه می‌گوید اعتبار تمام شده».

سناریوی واقعی کاربر:
  ۱) گروه فعال می‌شود و «30 day» می‌گیرد.
  ۲) زمان می‌گذرد و انقضا سر می‌رسد.
  ۳) «ai x cod» و «ai online» هیچ کاری نمی‌کنند و پیام انقضا برمی‌گردد.
  ۴) «30 day» پیام موفقیت می‌دهد ولی ربات همچنان منقضی می‌ماند  ← باگ اصلی

پوشش:
  * تمدید پس از انقضا واقعاً از «اکنون» حساب می‌شود و گروه از قفل خارج می‌شود.
  * تمدید زودهنگام (انقضای فعلی در آینده) اعتبار را روی همان تاریخ جمع می‌کند.
  * تمدید یک گروه، اعتبار سراسری را کوتاه نمی‌کند.
  * پیام راهنمای تمدید برای مالک سراسری ارسال می‌شود (نه سکوت).
  * «حذف انقضا» محدودیت زمانی گروه/ربات را کامل برمی‌دارد.
  * مقایسه‌ی تاریخ naive/aware دیگر خطا نمی‌دهد.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import tempfile
import unittest
from pathlib import Path

import brand
from ai_service import GroupAI
from config import Config
from core import BotCore, is_expiration_clear_command
from sender import BrandSender
from storage import OwnerStore
from tests.fakes import FakeAI, FakeClient, FakeEvent, FakeReplyMessage

try:
    import zoneinfo
    TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30), name="Asia/Tehran")

GROUP = -100111
OWNER = 1000
USER_1 = 3001


def run(coro):
    return asyncio.run(coro)


class ExpiryRenewalBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = OwnerStore(Path(self.tmp.name) / "t.sqlite3")
        self.cfg = Config()
        self.client = FakeClient()
        self.sender = BrandSender(self.cfg)
        self.now = _dt.datetime.now(TEHRAN_TZ)

        def now_provider(tz=None):
            return self.now.astimezone(tz) if tz else self.now

        self.ai = GroupAI(self.cfg, self.store, self.sender, FakeAI(reply="پاسخ"),
                          now_provider=now_provider)
        self.core = BotCore(self.cfg, self.store, self.sender, ai=self.ai)
        self.store.claim(OWNER, chat_id=GROUP, message_id=1, display_name="مالک سراسری")
        self.store.set_global_suspended(False)
        self.store.activate_group(GROUP, OWNER, group_name="گروه من")
        self.store.ai_set_enabled(GROUP, True)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def send(self, text, *, user_id=OWNER, chat_id=GROUP, is_group=True, reply_to=None):
        before = len(self.client.requests)
        event = FakeEvent(text, user_id=user_id, chat_id=chat_id, is_group=is_group,
                          reply_to=reply_to)
        await self.core.on_new_message(self.client, event)
        return self.client.requests[-1].message if len(self.client.requests) > before else None

    def make_expired(self, days_ago: int = 5) -> None:
        """گروه و ربات را «days_ago» روز پیش منقضی می‌کند (مثل وضعیت واقعی کاربر)."""
        past = self.now - _dt.timedelta(days=days_ago)
        older = self.now - _dt.timedelta(days=days_ago + 30)
        self.store._conn.execute(
            "UPDATE group_registry SET activated_at = ?, expires_at = ? WHERE chat_id = ?",
            (older.isoformat(), past.isoformat(), GROUP),
        )
        self.store._conn.execute(
            "UPDATE group_activations SET activated_at = ? WHERE chat_id = ?",
            (older.isoformat(), GROUP),
        )
        self.store._conn.commit()
        self.store.set_expiration(past)

    def bot_reply(self):
        return FakeReplyMessage(sender_id=self.client.me_id, display_name="acod",
                                msg_id=99, text="...", out=True)


class TestExpiredGroupRenewal(ExpiryRenewalBase):
    def test_renewal_after_expiry_uses_now_not_activation_date(self):
        """باگ اصلی: «30 day» پس از انقضا باید از امروز حساب شود و گروه را باز کند."""
        self.make_expired(days_ago=15)
        self.assertTrue(self.store.is_group_expired(GROUP, self.now))

        run(self.send("30 day"))

        exp = self.store.get_group_expiration(GROUP)
        self.assertIsNotNone(exp)
        expected = self.now + _dt.timedelta(days=30)
        self.assertLessEqual(abs((exp - expected).total_seconds()), 120,
                             "انقضا باید از «اکنون» حساب شود، نه از تاریخ فعال‌سازی")
        self.assertFalse(self.store.is_group_expired(GROUP, self.now))
        self.assertFalse(self.store.is_expired(self.now))

    def test_group_is_usable_right_after_renewal(self):
        """پس از تمدید، «ai x cod» و «ai online» باید مثل قبل کار کنند."""
        self.make_expired(days_ago=15)
        run(self.send("30 day"))
        self.client.requests.clear()

        msg = run(self.send("ai x cod"))
        self.assertIsNotNone(msg)
        self.assertNotIn(brand.BOT_EXPIRED_TEXT, msg or "")
        self.assertIn("𝗔𝗰𝘁𝗶𝘃𝗮𝘁𝗶𝗼𝗻 𝗗𝗮𝘁𝗲", msg or "")

        msg = run(self.send("ai online"))
        self.assertIn(brand.AI_ENABLED_TEXT, msg or "")
        self.assertTrue(self.store.ai_is_enabled(GROUP))

    def test_ai_chat_works_after_renewal(self):
        self.make_expired(days_ago=15)
        run(self.send("30 day"))
        run(self.send("ai online"))
        self.client.requests.clear()

        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))
        last = self.client.requests[-1].message
        self.assertNotIn(brand.BOT_EXPIRED_TEXT, last)

    def test_expired_owner_gets_renewal_hint_instead_of_silence(self):
        """پیام مالک در حالت منقضی نباید بی‌پاسخ بماند؛ باید مسیر تمدید را نشان دهد."""
        self.make_expired(days_ago=3)
        msg = run(self.send("ai online"))
        self.assertIsNotNone(msg)
        self.assertIn(brand.BOT_EXPIRED_TEXT, msg)
        self.assertIn("30 day", msg)
        self.assertIn("حذف انقضا", msg)

    def test_expired_non_owner_still_gets_plain_expired_text(self):
        self.make_expired(days_ago=3)
        run(self.send("ai online", user_id=USER_1))
        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))
        last = self.client.requests[-1].message
        self.assertEqual(last, brand.BOT_EXPIRED_TEXT)

    def test_early_renewal_stacks_on_current_expiry(self):
        """تمدید زودهنگام (اعتبار باقی‌مانده) باید روی همان تاریخ جمع شود."""
        future = self.now + _dt.timedelta(days=10)
        self.store.set_group_expiration(GROUP, future)
        run(self.send("7 day"))
        exp = self.store.get_group_expiration(GROUP)
        expected = future + _dt.timedelta(days=7)
        self.assertLessEqual(abs((exp - expected).total_seconds()), 120)

    def test_renewing_one_group_does_not_shorten_global_expiry(self):
        far = self.now + _dt.timedelta(days=365)
        self.store.set_expiration(far)
        run(self.send("1 day"))
        self.assertIsNotNone(self.store.get_expiration())
        self.assertGreaterEqual(self.store.get_expiration(), far)


class TestClearExpiryCommand(ExpiryRenewalBase):
    def test_clear_command_variants_detected(self):
        for text in ("حذف انقضا", "حذف انقضاء", "پاک کردن انقضا", "بدون انقضا", "clear expiry"):
            self.assertTrue(is_expiration_clear_command(text), text)
        self.assertFalse(is_expiration_clear_command("30 day"))

    def test_owner_can_clear_expired_group(self):
        self.make_expired(days_ago=20)
        msg = run(self.send("حذف انقضا"))
        self.assertIn(brand.EXPIRY_CLEARED_GROUP_TEXT, msg or "")
        self.assertIsNone(self.store.get_group_expiration(GROUP))
        self.assertIsNone(self.store.get_expiration())
        self.assertFalse(self.store.is_group_expired(GROUP, self.now))

        # بعد از حذف انقضا، ربات باید دوباره کار کند
        self.client.requests.clear()
        msg = run(self.send("ai online"))
        self.assertIn(brand.AI_ENABLED_TEXT, msg or "")

    def test_non_owner_cannot_clear_expiry(self):
        self.make_expired(days_ago=20)
        exp_before = self.store.get_group_expiration(GROUP)
        run(self.send("حذف انقضا", user_id=USER_1))
        self.assertEqual(self.store.get_group_expiration(GROUP), exp_before)


class TestTimezoneSafety(ExpiryRenewalBase):
    def test_naive_expiry_does_not_crash_comparison(self):
        """انقضای naive (مثلاً از نسخه‌های قدیمی) نباید با اکنونِ aware خطا بدهد."""
        naive_past = _dt.datetime(2020, 1, 1, 0, 0, 0)
        self.store._conn.execute(
            "INSERT INTO bot_settings(key, value) VALUES('bot_expires_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (naive_past.isoformat(),),
        )
        self.store._conn.execute(
            "UPDATE group_registry SET expires_at = ? WHERE chat_id = ?",
            (naive_past.isoformat(), GROUP),
        )
        self.store._conn.commit()

        self.assertTrue(self.store.is_expired(_dt.datetime.now(_dt.timezone.utc)))
        self.assertTrue(self.store.is_group_expired(GROUP, _dt.datetime.now(_dt.timezone.utc)))
        self.assertTrue(self.store.is_expired())
        self.assertTrue(self.store.is_group_expired(GROUP))

    def test_default_clock_is_timezone_aware_utc(self):
        """ساعت پیش‌فرض باید aware-UTC باشد، نه naive-محلی (منبع انقضای زودهنگام ۳:۳۰)."""
        ai = GroupAI(self.cfg, self.store, self.sender, FakeAI(reply="x"))
        now = ai._now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), _dt.timedelta(0))

        core = BotCore(self.cfg, self.store, self.sender, ai=ai)
        self.assertEqual(core._now().utcoffset(), _dt.timedelta(0))


if __name__ == "__main__":
    unittest.main()
