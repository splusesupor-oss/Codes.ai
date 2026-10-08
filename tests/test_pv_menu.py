"""
تست‌های رفتار جدید پیام خصوصی:

  * اولین پیام هر کاربر PV → معرفی (یک‌بار) + منوی انتخاب (یک‌بار)
  * پیام‌های بعدی → معرفی/منو تکرار نمی‌شود
  * ۶ گزینه‌ی منو → هر کدام فقط پاسخ خودش، با قالب Bold + نقل‌قول شیشه‌ای
  * پایداری «اولین پیام» بعد از restart و مستقل از تغییر username
  * دست‌نخورده ماندن گروه‌ها، کدرز، ai cod و دستورهای مالک
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brand  # noqa: E402
from config import Config  # noqa: E402
from core import BotCore, match_menu_option  # noqa: E402
from sender import BrandSender  # noqa: E402
from storage import OwnerStore  # noqa: E402
from splusthon import types  # noqa: E402
from tests.fakes import FakeClient, FakeEvent  # noqa: E402

GROUP_A = -1001
OWNER_ID, USER_A, USER_B = 111, 501, 502


def run(coro):
    return asyncio.run(coro)


class PvMenuTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "owner.sqlite3"
        self.cfg = Config()
        self.store = OwnerStore(self.db)
        self.client = FakeClient()
        self.core = BotCore(self.cfg, self.store, BrandSender(self.cfg))
        # طبق رفتار جدید، تا قبل از «ai cod» هیچ پاسخ خودکاری فرستاده نمی‌شود؛
        # اکثر تست‌های این فایل رفتار «بعد از فعال‌سازی» را می‌سنجند، پس یک مالک
        # پیش‌فرض ثبت می‌کنیم.
        self.store.claim(OWNER_ID, chat_id=GROUP_A, message_id=1, display_name="مالک")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def pv(self, text, *, user_id, username=None, display_name=None, out=False):
        event = FakeEvent(text, user_id=user_id, chat_id=user_id, is_group=False,
                          username=username, display_name=display_name, out=out)
        await self.core.on_new_message(self.client, event)

    async def group(self, text, *, user_id, chat_id=GROUP_A):
        event = FakeEvent(text, user_id=user_id, chat_id=chat_id, is_group=True)
        await self.core.on_new_message(self.client, event)

    def texts(self):
        return self.client.text_messages()

    def last_request(self):
        return self.client.requests[-1]

    @staticmethod
    def _kinds(req):
        return [type(e).__name__ for e in (req.entities or [])]


class TestFirstMessage(PvMenuTestCase):
    def test_1_first_message_sends_intro_then_menu(self):
        run(self.pv("سلام", user_id=USER_A, username="osine", display_name="علی"))

        self.assertEqual(len(self.client.requests), 2, "باید معرفی + منو ارسال شود")
        intro, menu = self.client.requests
        self.assertEqual(intro.message, brand.FULL_TEXT)      # همان معرفی فعلی
        self.assertEqual(menu.message, brand.MENU_TEXT)

    def test_1b_menu_content_is_exact(self):
        run(self.pv("سلام", user_id=USER_A))
        self.assertEqual(
            self.client.requests[1].message,
            "برای انتخاب فقط دستورات زیر را ارسال کنید\n\n"
            "سازنده\nکانال روباه\nربات پشتیبانی\nسایت خرید\nکانال دانلود\nربات روباه",
        )

    def test_2_second_message_does_not_resend_intro_or_menu(self):
        run(self.pv("سلام", user_id=USER_A))
        self.client.clear_requests()

        run(self.pv("ممنون", user_id=USER_A))          # متن غیردستوری
        self.assertEqual(self.client.requests, [], "معرفی/منو نباید تکرار شود")

        run(self.pv("سازنده", user_id=USER_A))          # دستور
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.last_request().message, "@osine2")   # فقط پاسخ دستور

    def test_3_restart_does_not_make_user_new_again(self):
        run(self.pv("سلام", user_id=USER_A, username="osine"))
        self.store.close()

        store2 = OwnerStore(self.db)                    # restart / reload storage
        try:
            core2 = BotCore(self.cfg, store2, BrandSender(self.cfg))
            client2 = FakeClient()

            async def scenario():
                event = FakeEvent("سلام دوباره", user_id=USER_A, chat_id=USER_A,
                                  is_group=False, username="osine_new")
                await core2.on_new_message(client2, event)

            run(scenario())
            self.assertEqual(client2.requests, [], "کاربر بعد از restart نباید new باشد")
            self.assertEqual(store2.count_pv_users(), 1)
        finally:
            store2.close()
            self.store = OwnerStore(self.db)

    def test_4_username_change_keeps_user_same(self):
        run(self.pv("سلام", user_id=USER_A, username="osine"))
        self.client.clear_requests()

        # همان user_id، username متفاوت → کاربر جدید نیست
        run(self.pv("سلام", user_id=USER_A, username="osine2_new"))
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.store.count_pv_users(), 1)

    def test_5_second_user_gets_own_intro_and_menu_once(self):
        run(self.pv("سلام", user_id=USER_A, username="osine"))
        run(self.pv("سلام", user_id=USER_B, username="elism", display_name="الیس"))
        self.client.clear_requests()

        run(self.pv("سلام", user_id=USER_B))            # پیام دوم کاربر دوم
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.store.count_pv_users(), 2)  # دو کاربر مستقل

    def test_6_restart_then_second_user_still_gets_intro_once(self):
        run(self.pv("سلام", user_id=USER_A))
        self.store.close()
        store2 = OwnerStore(self.db)
        try:
            core2 = BotCore(self.cfg, store2, BrandSender(self.cfg))
            client2 = FakeClient()

            async def scenario():
                event = FakeEvent("سلام", user_id=USER_B, chat_id=USER_B,
                                  is_group=False, username="elism")
                await core2.on_new_message(client2, event)

            run(scenario())
            self.assertEqual(client2.text_messages(),
                             [brand.FULL_TEXT, brand.MENU_TEXT])
        finally:
            store2.close()
            self.store = OwnerStore(self.db)


class TestMenuReplies(PvMenuTestCase):
    """گزینه‌های منو: پاسخ دقیق + قالب Bold و نقل‌قول شیشه‌ای."""

    def _ask(self, option, *, user_id=USER_A):
        run(self.pv("سلام", user_id=user_id))        # اولین پیام: معرفی + منو
        self.client.clear_requests()
        run(self.pv(option, user_id=user_id))
        self.assertEqual(len(self.client.requests), 1, "باید فقط یک پاسخ برود")
        return self.last_request()

    def test_7_sazande(self):
        self.assertEqual(self._ask("سازنده").message, "@osine2")

    def test_8_kanal_roobah(self):
        self.assertEqual(self._ask("کانال روباه").message, "@ai_fox")

    def test_9_robot_poshtibani(self):
        self.assertEqual(self._ask("ربات پشتیبانی").message, "@Aifox_bot")

    def test_10_site_kharid(self):
        self.assertEqual(self._ask("سایت خرید").message,
                         "https://foxbot.osine2.workers.dev/")

    def test_11_kanal_download(self):
        self.assertEqual(self._ask("کانال دانلود").message,
                         "https://splus.ir/Orderawebsite")

    def test_12_robot_roobah_full_structure(self):
        req = self._ask("ربات روباه")
        self.assertEqual(
            req.message,
            "ربات های روباه\n\n"
            "نسخه یک🔹 @fox_bot\n"
            "نسخه دو🔹 @aifox\n"
            "نسخه سه🔹 @bot_fox",
        )

    def test_13_all_replies_are_bold(self):
        for option in brand.MENU_OPTIONS:
            with self.subTest(option=option):
                req = self._ask(option)
                bolds = [e for e in req.entities
                         if isinstance(e, types.MessageEntityBold)]
                self.assertTrue(bolds, f"«{option}» باید Bold باشد")
                units = req.message.encode("utf-16-le")
                covered = "".join(
                    units[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")
                    for e in bolds
                )
                # همه‌ی خطوط غیرخالی پاسخ باید Bold باشند
                for line in req.message.split("\n"):
                    if line.strip():
                        self.assertIn(line, covered)

    def test_14_all_replies_are_inside_glass_quote(self):
        for option in brand.MENU_OPTIONS:
            with self.subTest(option=option):
                req = self._ask(option)
                quotes = [e for e in req.entities
                          if isinstance(e, types.MessageEntityBlockquote)]
                self.assertEqual(len(quotes), 1, f"«{option}» باید داخل نقل‌قول باشد")
                self.assertEqual(quotes[0].offset, 0)
                self.assertEqual(quotes[0].length, brand.utf16_len(req.message))

    def test_15_menu_message_is_bold_and_quoted(self):
        run(self.pv("سلام", user_id=USER_A))
        menu = self.client.requests[1]

        quotes = [e for e in menu.entities
                  if isinstance(e, types.MessageEntityBlockquote)]
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].length, brand.utf16_len(brand.MENU_TEXT))

        bolds = [e for e in menu.entities if isinstance(e, types.MessageEntityBold)]
        units = menu.message.encode("utf-16-le")
        covered = "".join(
            units[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")
            for e in bolds
        )
        self.assertIn("برای انتخاب فقط دستورات زیر را ارسال کنید", covered)
        for option in brand.MENU_OPTIONS:      # هر ۶ گزینه هم Bold هستند
            self.assertIn(option, covered)

    def test_16_utf16_offsets_are_correct(self):
        """پاسخ «ربات روباه» ایموجی 🔹 دارد؛ آفست نباید مثل len() پایتون باشد."""
        req = self._ask("ربات روباه")
        self.assertGreater(brand.utf16_len(req.message), len(req.message))

        units = req.message.encode("utf-16-le")
        for entity in req.entities:
            if isinstance(entity, types.MessageEntityBold):
                value = units[entity.offset * 2:(entity.offset + entity.length) * 2]
                self.assertTrue(value.decode("utf-16-le").strip())

    def test_17_repeated_option_does_not_resend_menu(self):
        run(self.pv("سلام", user_id=USER_A))
        run(self.pv("سازنده", user_id=USER_A))
        run(self.pv("سازنده", user_id=USER_A))

        texts = self.texts()
        self.assertEqual(texts[0], brand.FULL_TEXT)         # معرفی فقط یک‌بار
        self.assertEqual(texts.count(brand.MENU_TEXT), 1)   # منو فقط یک‌بار
        self.assertEqual(texts[2:], ["@osine2", "@osine2"])

    def test_18_matching_variants(self):
        self.assertEqual(match_menu_option("  کانال   روباه "), "کانال روباه")
        self.assertEqual(match_menu_option("ربات\u200cپشتیبانی"), "ربات پشتیبانی")
        self.assertEqual(match_menu_option("سایت خرید"), "سایت خرید")
        self.assertIsNone(match_menu_option("کدرز"))
        self.assertIsNone(match_menu_option("ai cod"))


class TestPvMenuGuards(PvMenuTestCase):
    def test_19_groups_are_unchanged(self):
        run(self.group("کدرز", user_id=USER_A))
        self.assertEqual(self.texts(), [brand.FULL_TEXT])   # فقط معرفی، بدون منو

        self.client.clear_requests()
        run(self.group("سلام به همه", user_id=USER_A))
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.store.count_pv_users(), 0)

    def test_20_ai_cod_in_pv_does_not_change_owner(self):
        run(self.pv("ai cod", user_id=USER_A))
        # «ai cod» در PV نباید مالک قبلی (OWNER_ID از setUp) را تغییر دهد
        self.assertEqual(self.store.get_owner().user_id, OWNER_ID)
        # پیام اول بود → معرفی + منو (و «ai cod» گزینه‌ی منو نیست)
        self.assertEqual(self.texts(), [brand.FULL_TEXT, brand.MENU_TEXT])

    def test_20b_pv_is_silent_before_owner_claim(self):
        # قبل از فعال‌سازی، PV کاملاً بی‌صدا است (ثبت می‌کند ولی پیام نمی‌فرستد)
        tmp = tempfile.TemporaryDirectory()
        try:
            db_path = Path(tmp.name) / "o.sqlite3"
            store2 = OwnerStore(db_path)
            client2 = FakeClient()
            core2 = BotCore(self.cfg, store2, BrandSender(self.cfg))
            run(core2.on_new_message(client2, FakeEvent(
                "سلام", user_id=USER_A, chat_id=USER_A, is_group=False)))
            self.assertIsNone(store2.get_owner())
            self.assertEqual(client2.requests, [])
            self.assertEqual(store2.count_pv_users(), 1)
        finally:
            store2.close(); tmp.cleanup()

    def test_21_anti_loop_outgoing_is_ignored(self):
        run(self.pv(brand.MENU_TEXT, user_id=USER_A, out=True))
        run(self.pv("سلام", user_id=USER_A, out=True))
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.store.count_pv_users(), 0)

    def test_22_owner_admin_commands_still_work(self):
        # مالک از قبل در setUp ثبت شده است
        run(self.pv("سلام", user_id=USER_B, username="osine"))
        self.client.clear_requests()

        run(self.pv("تعداد اعضا", user_id=OWNER_ID, display_name="مالک"))
        self.assertTrue(self.last_request().message.startswith("تعداد اعضا :"))

        self.client.clear_requests()
        run(self.pv("لیست اعضا", user_id=OWNER_ID))
        self.assertIn("1 : @osine", self.last_request().message)

    def test_23_menu_reply_goes_only_to_same_user(self):
        run(self.pv("سلام", user_id=USER_A))
        self.client.clear_requests()
        run(self.pv("سازنده", user_id=USER_A))

        req = self.last_request()
        self.assertIsInstance(req.peer, types.InputPeerUser)
        self.assertEqual(req.peer.user_id, USER_A)

    def test_24_feature_can_be_disabled(self):
        import dataclasses

        cfg = dataclasses.replace(self.cfg, private_auto_reply=False)
        core = BotCore(cfg, self.store, BrandSender(cfg))

        async def scenario():
            event = FakeEvent("سلام", user_id=USER_A, chat_id=USER_A, is_group=False)
            await core.on_new_message(self.client, event)

        run(scenario())
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.store.count_pv_users(), 1)   # ثبت کاربر همچنان انجام می‌شود


if __name__ == "__main__":
    unittest.main(verbosity=2)
