"""
تست‌های قابلیت «مدیریت کاربران PV»:

  * ثبت یک‌بارِ هر کاربر PV (بدون تکرار)
  * نمایش «@username» یا نام نمایشی یا «کاربر بدون نام»
  * «تعداد اعضا» و «لیست اعضا» فقط برای مالک سراسری و فقط در PV
  * وارد نشدن کاربران گروهی به آمار PV
  * تکه‌تکه‌شدن لیست بلند (محدودیت طول پیام)
  * دست‌نخورده ماندن رفتار قبلی: کدرز، پاسخ خودکار PV و anti-loop
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brand  # noqa: E402
from config import Config  # noqa: E402
from core import (  # noqa: E402
    BotCore,
    chunk_lines,
    format_pv_user_list,
    match_pv_admin_command,
    pv_user_label,
)
from sender import BrandSender  # noqa: E402
from storage import OwnerStore  # noqa: E402
from tests.fakes import FakeClient, FakeEvent  # noqa: E402

GROUP_A = -1001
OWNER_ID, USER_2, USER_3 = 111, 222, 333


def run(coro):
    return asyncio.run(coro)


class PvUsersTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "owner.sqlite3"
        self.cfg = Config()
        self.store = OwnerStore(self.db)
        self.client = FakeClient()
        self.core = BotCore(self.cfg, self.store, BrandSender(self.cfg))
        # طبق رفتار جدید تا قبل از «ai cod»، PV بی‌صدا است. اکثر تست‌های این
        # فایل رفتار «بعد از فعال‌سازی» را می‌سنجند، پس یک مالک پیش‌فرض می‌گذاریم.
        self.make_owner()

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def pv(self, text, *, user_id, username=None, display_name=None, out=False):
        """ارسال یک پیام خصوصی ورودی به ربات."""
        event = FakeEvent(
            text,
            user_id=user_id,
            chat_id=user_id,          # در PV، chat_id همان شناسه‌ی کاربر است
            is_group=False,
            username=username,
            display_name=display_name,
            out=out,
        )
        await self.core.on_new_message(self.client, event)

    async def group(self, text, *, user_id, chat_id=GROUP_A):
        event = FakeEvent(text, user_id=user_id, chat_id=chat_id, is_group=True)
        await self.core.on_new_message(self.client, event)

    def make_owner(self, user_id=OWNER_ID):
        self.store.claim(user_id, chat_id=GROUP_A, message_id=1, display_name="مالک")

    def sent_texts(self):
        return self.client.text_messages()


class TestRegistration(PvUsersTestCase):
    def test_11_single_pv_message_registers_user(self):
        run(self.pv("سلام", user_id=USER_2, username="osine", display_name="علی"))

        self.assertEqual(self.store.count_pv_users(), 1)
        user = self.store.list_pv_users()[0]
        self.assertEqual(user.user_id, USER_2)
        self.assertEqual(user.username, "osine")
        self.assertEqual(user.display_name, "علی")

    def test_12_repeated_messages_do_not_duplicate(self):
        for _ in range(5):
            run(self.pv("سلام", user_id=USER_2, username="osine", display_name="علی"))

        self.assertEqual(self.store.count_pv_users(), 1, "کاربر تکراری ثبت شد")
        self.assertEqual(len(self.store.list_pv_users()), 1)

    def test_13_registration_is_idempotent_at_storage_level(self):
        self.assertTrue(self.store.register_pv_user(USER_2, username="osine"))
        self.assertFalse(self.store.register_pv_user(USER_2, username="osine"))
        self.assertFalse(self.store.register_pv_user(USER_2, username="changed"))
        self.assertEqual(self.store.count_pv_users(), 1)
        # داده‌ی اولین ثبت دست‌نخورده می‌ماند
        self.assertEqual(self.store.list_pv_users()[0].username, "osine")

    def test_14_missing_username_falls_back_to_display_name(self):
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))
        self.assertEqual(self.store.count_pv_users(), 1)
        self.assertEqual(self.store.list_pv_users()[0].username, None)

    def test_15_users_are_persisted_across_restarts(self):
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        self.store.close()

        store2 = OwnerStore(self.db)
        try:
            self.assertEqual(store2.count_pv_users(), 1)
            self.assertEqual(store2.list_pv_users()[0].username, "osine")
        finally:
            store2.close()
            self.store = OwnerStore(self.db)

    def test_16_group_messages_never_enter_pv_stats(self):
        run(self.group("کدرز", user_id=USER_2))
        run(self.group("ai cod", user_id=USER_2))
        run(self.group("سلام به همه", user_id=USER_3))

        self.assertEqual(self.store.count_pv_users(), 0, "کاربر گروهی وارد آمار PV شد")
        self.assertEqual(self.store.list_pv_users(), [])

    def test_17_pv_users_get_no_privileges(self):
        run(self.pv("سلام", user_id=USER_2, username="osine"))

        self.assertEqual(self.store.count_pv_users(), 1)
        # مالک از قبل در setUp ثبت شده (OWNER_ID)؛ کاربر PV نباید جایگزینش شود
        self.assertEqual(self.store.get_owner().user_id, OWNER_ID)
        self.assertFalse(self.store.is_owner(USER_2), "کاربر PV نباید مالک محسوب شود")


class TestLabels(PvUsersTestCase):
    def test_18_username_is_shown_with_at_sign(self):
        run(self.pv("سلام", user_id=USER_2, username="osine", display_name="علی"))
        self.assertEqual(format_pv_user_list(self.store.list_pv_users()), ["1 : @osine"])

    def test_19_display_name_is_used_when_no_username(self):
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))
        self.assertEqual(format_pv_user_list(self.store.list_pv_users()), ["1 : ali"])

    def test_20_unknown_name_fallback(self):
        run(self.pv("سلام", user_id=USER_3, username=None, display_name=""))
        self.assertEqual(
            format_pv_user_list(self.store.list_pv_users()), ["1 : کاربر بدون نام"]
        )
        # و نمایش مستقیم هم همان مقدار امن را می‌دهد
        self.assertEqual(
            pv_user_label(self.store.list_pv_users()[0]), "کاربر بدون نام"
        )

    def test_21_username_with_at_sign_is_not_doubled(self):
        self.store.register_pv_user(USER_2, username="@osine")
        self.assertEqual(format_pv_user_list(self.store.list_pv_users()), ["1 : @osine"])

    def test_22_multiple_users_are_numbered_in_order(self):
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))
        run(self.pv("سلام", user_id=444, username="elism", display_name="الیس"))

        self.assertEqual(
            format_pv_user_list(self.store.list_pv_users()),
            ["1 : @osine", "2 : ali", "3 : @elism"],
        )


class TestOwnerCommands(PvUsersTestCase):
    def test_23_count_command_shows_count_and_full_list(self):
        # «تعداد اعضا» → تعداد + فهرست کامل کاربران PV، در یک پیام (اگر کوتاه باشد)
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))
        run(self.pv("سلام", user_id=444, username="elism"))

        self.client.clear_requests()
        run(self.pv("تعداد اعضا", user_id=OWNER_ID, display_name="مالک"))

        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(
            self.client.requests[0].message,
            "تعداد اعضا : 4\n"          # ۳ کاربر + خود مالک
            "\n"
            "1 : @osine\n"
            "2 : ali\n"
            "3 : @elism\n"
            "4 : مالک",
        )

    def test_24_list_command_for_owner(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))
        run(self.pv("سلام", user_id=444, username="elism"))

        self.client.clear_requests()
        run(self.pv("لیست اعضا", user_id=OWNER_ID, display_name="مالک"))

        self.assertEqual(len(self.client.requests), 1)
        lines = self.client.requests[0].message.split("\n")
        # مالک هم چون «هر کاربری که در PV پیام می‌دهد» است، خودش هم در فهرست می‌آید
        # (نیازمندی ۱) و شماره‌گذاری به ترتیب اولین ثبت است.
        self.assertEqual(
            lines, ["1 : @osine", "2 : ali", "3 : @elism", "4 : مالک"]
        )

    def test_25_non_owner_cannot_use_admin_commands(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine"))   # اولین پیام → ثبت
        run(self.pv("سلام", user_id=USER_3))                     # اولین پیام → ثبت
        self.client.clear_requests()

        # «لیست اعضا» و «تعداد اعضا» نه گزینه‌ی منو هستند و نه برای غیرمالک اجرا می‌شوند
        run(self.pv("لیست اعضا", user_id=USER_2))
        run(self.pv("تعداد اعضا", user_id=USER_3))
        run(self.pv("تعداد اعضا", user_id=USER_2))

        self.assertEqual(self.client.requests, [],
                         "کاربر غیرمالک نباید هیچ پاسخ مدیریتی/آماری بگیرد")

    def test_26_admin_commands_are_ignored_in_groups(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        self.client.clear_requests()

        run(self.group("لیست اعضا", user_id=OWNER_ID))
        run(self.group("تعداد اعضا", user_id=OWNER_ID))

        self.assertEqual(self.client.requests, [], "دستور مدیریتی در گروه نباید اجرا شود")

    def test_27_list_is_never_empty_for_the_owner(self):
        self.make_owner()
        # مالک اولین کسی است که در PV پیام می‌دهد → خودش رکورد اول است (نیازمندی ۱)
        run(self.pv("لیست اعضا", user_id=OWNER_ID, display_name="مالک"))
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.requests[0].message, "1 : مالک")

    def test_27b_empty_storage_message_when_no_users(self):
        # اگر هنوز هیچ کاربری ثبت نشده باشد، پیام امن ارسال می‌شود (نه لیست خالی)
        from core import BotCore as _Core
        from sender import BrandSender as _Sender

        cfg = dataclasses.replace(self.cfg, private_auto_reply=False)
        core = _Core(cfg, self.store, _Sender(cfg))

        async def scenario():
            event = FakeEvent("لیست اعضا", user_id=OWNER_ID, chat_id=OWNER_ID,
                              is_group=False, display_name="مالک")
            self.store.claim(OWNER_ID, chat_id=GROUP_A, display_name="مالک")
            # کاربر مالک، ولی با private_auto_reply=False مسیر ثبت کاربر PV
            # همچنان اجرا می‌شود؛ برای تست حالت «خالی» مستقیم تابع را صدا می‌زنیم.
            await core._send_pv_list(self.client, event)

        run(scenario())
        self.assertEqual(self.client.requests[-1].message,
                         "هنوز کاربری ثبت نشده است.")

    def test_28_commands_with_half_space_and_spacing(self):
        cfg = self.cfg
        self.assertEqual(match_pv_admin_command("تعداد اعضا", cfg), "count")
        self.assertEqual(match_pv_admin_command("  لیست   اعضا ", cfg), "list")
        self.assertEqual(match_pv_admin_command("لیست\u200cاعضا", cfg), "list")
        self.assertIsNone(match_pv_admin_command("کدرز", cfg))
        self.assertIsNone(match_pv_admin_command("سلام", cfg))


class TestLongListChunking(PvUsersTestCase):
    def test_29_chunk_lines_respects_limit(self):
        lines = [f"{i} : @user_{i:05d}" for i in range(1, 2001)]
        chunks = chunk_lines(lines, 3500)

        self.assertGreater(len(chunks), 1, "لیست بلند باید به چند پیام شکسته شود")
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 3500)
        # هیچ خطی گم یا جابه‌جا نشود
        rebuilt = "\n".join(chunks).split("\n")
        self.assertEqual(rebuilt, lines)

    def test_30_long_list_is_sent_in_multiple_messages(self):
        self.make_owner()
        for i in range(600):
            self.store.register_pv_user(100000 + i, username=f"user_{i:05d}")

        self.client.clear_requests()
        run(self.pv("لیست اعضا", user_id=OWNER_ID))

        self.assertGreater(len(self.client.requests), 1)
        for req in self.client.requests:
            self.assertLessEqual(len(req.message), self.cfg.max_message_chars)
        all_lines = "\n".join(self.sent_texts()).split("\n")
        self.assertEqual(all_lines[0], "1 : @user_00000")
        self.assertEqual(len(all_lines), 601)     # ۶۰۰ کاربر + مالک

    def test_31_single_long_line_is_not_split_midway(self):
        long_line = "1 : " + "x" * 4000
        chunks = chunk_lines([long_line], 3500)
        self.assertEqual(chunks, [long_line])


class TestRegression(PvUsersTestCase):
    def test_32_kodrez_in_group_still_works(self):
        run(self.group("کدرز", user_id=USER_2))
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)
        self.assertEqual(self.store.count_pv_users(), 0)

    def test_33_pv_auto_reply_unchanged_for_regular_users(self):
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        self.assertEqual(len(self.client.requests), 2)      # معرفی + منو
        req = self.client.requests[0]                       # معرفی، با همان قالب قبلی
        self.assertEqual(req.message, brand.FULL_TEXT)
        kinds = [type(e).__name__ for e in req.entities]
        self.assertIn("MessageEntityBlockquote", kinds)
        self.assertEqual(kinds.count("MessageEntityBold"), len(brand.BODY_LINES))
        self.assertIn(brand.LINK_LINE, req.message)

    def test_34_anti_loop_outgoing_pv_is_ignored(self):
        run(self.pv(brand.FULL_TEXT, user_id=USER_2, out=True))
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.store.count_pv_users(), 0, "پیام خروجی نباید ثبت شود")

    def test_35_owner_command_does_not_break_auto_reply_for_others(self):
        self.make_owner()
        run(self.pv("تعداد اعضا", user_id=OWNER_ID))
        before = len(self.client.requests)
        run(self.pv("سلام", user_id=USER_2))

        # کاربر جدید → معرفی + منو (دو پیام)؛ دستور مالک هم همچنان کار می‌کند
        self.assertEqual(len(self.client.requests), before + 2)
        self.assertEqual(self.client.requests[-2].message, brand.FULL_TEXT)
        self.assertEqual(self.client.requests[-1].message, brand.MENU_TEXT)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCountWithList(PvUsersTestCase):
    """دستور «تعداد اعضا»: تعداد + فهرست کامل کاربران PV (اصلاح جدید)."""

    def _count_reply(self, *, owner_id=OWNER_ID):
        self.client.clear_requests()
        run(self.pv("تعداد اعضا", user_id=owner_id, display_name="مالک"))
        return self.client.text_messages()

    def test_36_format_is_exactly_count_blank_line_then_numbered_list(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))
        run(self.pv("سلام", user_id=444, username="elism"))

        messages = self._count_reply()

        self.assertEqual(
            messages,
            ["تعداد اعضا : 4\n\n1 : @osine\n2 : ali\n3 : @elism\n4 : مالک"],
        )

    def test_37_numbering_starts_at_1_in_registration_order(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_3, username="first_user"))
        run(self.pv("سلام", user_id=USER_2, username="second_user"))
        run(self.pv("سلام", user_id=444, username="third_user"))

        lines = self._count_reply()[0].split("\n")
        self.assertEqual(lines[0], "تعداد اعضا : 4")
        self.assertEqual(lines[1], "")
        self.assertEqual(lines[2:], [
            "1 : @first_user",
            "2 : @second_user",
            "3 : @third_user",
            "4 : مالک",
        ])

    def test_38_username_then_display_name_then_safe_fallback(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine", display_name="علی"))
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))
        run(self.pv("سلام", user_id=444, username=None, display_name=""))

        body = self._count_reply()[0].split("\n\n", 1)[1]
        self.assertEqual(
            body.split("\n"),
            ["1 : @osine", "2 : ali", "3 : کاربر بدون نام", "4 : مالک"],
        )

    def test_39_each_user_appears_only_once(self):
        self.make_owner()
        for _ in range(4):
            run(self.pv("سلام", user_id=USER_2, username="osine"))
            run(self.pv("سازنده", user_id=USER_2))          # پیام‌های بعدی همان کاربر

        messages = self._count_reply()
        self.assertTrue(messages[0].startswith("تعداد اعضا : 2"))
        self.assertEqual(messages[0].count("@osine"), 1)

    def test_40_group_users_are_not_in_the_list(self):
        self.make_owner()
        run(self.group("کدرز", user_id=USER_2))
        run(self.group("ai cod", user_id=USER_3))
        run(self.pv("سلام", user_id=444, username="elism"))

        messages = self._count_reply()
        self.assertTrue(messages[0].startswith("تعداد اعضا : 2"))   # elism + مالک
        self.assertNotIn("User", messages[0])

    def test_41_only_global_owner_can_run_it(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine"))      # اولین پیام → ثبت
        run(self.pv("سلام", user_id=USER_3, username="elism"))
        self.client.clear_requests()

        run(self.pv("تعداد اعضا", user_id=USER_2))
        run(self.pv("تعداد اعضا", user_id=USER_3))

        self.assertEqual(self.client.requests, [],
                         "غیرمالک نباید هیچ خروجی آماری بگیرد")

    def test_42_empty_storage_sends_only_the_header(self):
        # حالت بدون هیچ کاربر ثبت‌شده (فراخوانی مستقیم، بدون ثبت خود مالک)
        self.make_owner()

        async def scenario():
            event = FakeEvent("تعداد اعضا", user_id=OWNER_ID, chat_id=OWNER_ID,
                              is_group=False, display_name="مالک")
            await self.core._send_pv_count(self.client, event)

        run(scenario())
        self.assertEqual(self.client.text_messages(), ["تعداد اعضا : 0"])

    def test_43_long_list_is_split_with_continuous_numbering(self):
        self.make_owner()
        for i in range(600):
            self.store.register_pv_user(100000 + i, username=f"user_{i:05d}")

        messages = self._count_reply()

        self.assertGreater(len(messages), 1, "لیست بلند باید به چند پیام شکسته شود")
        for msg in messages:                                  # محدودیت طول پیام
            self.assertLessEqual(len(msg), self.cfg.max_message_chars)

        all_lines = "\n".join(messages).split("\n")
        self.assertEqual(all_lines[0], "تعداد اعضا : 601")     # ۶۰۰ کاربر + مالک
        self.assertEqual(all_lines[1], "")
        # شماره‌گذاری ادامه‌دار و بدون تکرار/پرش
        numbered = [line for line in all_lines if line and line[0].isdigit()]
        self.assertEqual(numbered[0], "1 : @user_00000")
        self.assertEqual(numbered[-1], "601 : مالک")
        self.assertEqual([int(l.split(" : ")[0]) for l in numbered],
                         list(range(1, 602)))

    def test_44_list_command_is_still_unchanged(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))

        self.client.clear_requests()
        run(self.pv("لیست اعضا", user_id=OWNER_ID, display_name="مالک"))

        # «لیست اعضا» همچنان فقط شماره‌گذاری‌شده است، بدون سرتیتر تعداد
        self.assertEqual(
            self.client.requests[0].message,
            "1 : @osine\n2 : ali\n3 : مالک",
        )

    def test_45_count_and_list_agree_with_storage(self):
        self.make_owner()
        run(self.pv("سلام", user_id=USER_2, username="osine"))
        run(self.pv("سلام", user_id=USER_3, username=None, display_name="ali"))

        messages = self._count_reply()
        header_count = int(messages[0].splitlines()[0].split(" : ")[1])

        self.assertEqual(header_count, self.store.count_pv_users())
        numbered_lines = [
            l for l in messages[0].split("\n")[2:] if l and l[0].isdigit()
        ]
        self.assertEqual(len(numbered_lines), len(self.store.list_pv_users()))
