"""
تست سناریوهای اصلی ربات (بندهای ۷، ۸ و ۹ خروجی مورد انتظار):

  ۷) فقط اولین کاربر مالک می‌شود.
  ۸) کاربر دوم — حتی در گروهی دیگر — نمی‌تواند مالک شود.
  ۹) «کدرز» در گروه‌های مختلف برای همه کار می‌کند.
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
from core import BotCore  # noqa: E402
from sender import BrandSender  # noqa: E402
from storage import OwnerStore  # noqa: E402
from tests.fakes import FakeClient, FakeEvent  # noqa: E402
from splusthon import types  # noqa: E402

GROUP_A, GROUP_B, GROUP_C = -1001, -2002, -3003
USER_1, USER_2, USER_3 = 111, 222, 333


def run(coro):
    return asyncio.run(coro)


class FlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "owner.sqlite3"
        self.cfg = Config()
        self.store = OwnerStore(self.db)
        self.client = FakeClient()
        self.core = BotCore(self.cfg, self.store, BrandSender(self.cfg))

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _claim_owner(self, user_id=USER_1, chat_id=GROUP_A):
        """ثبت یک مالک پیش‌فرض برای تست‌هایی که رفتار «بعد از فعال‌سازی» را می‌سنجند."""
        self.store.claim(user_id, chat_id=chat_id, message_id=1, display_name="مالک")

    async def send(self, text, *, user_id, chat_id, **kw):
        event = FakeEvent(text, user_id=user_id, chat_id=chat_id, **kw)
        await self.core.on_new_message(self.client, event)


class TestOwnerActivation(FlowTestCase):
    def test_1_first_ai_cod_becomes_global_owner_and_gets_brand_message(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))

        owner = self.store.get_owner()
        self.assertIsNotNone(owner, "مالک ثبت نشد")
        self.assertEqual(owner.user_id, USER_1)
        self.assertEqual(owner.claimed_chat_id, GROUP_A)

        # پیام فعال‌سازی ارسال شده است، دقیقاً یک‌بار و با همان قالب
        self.assertEqual(len(self.client.requests), 1)
        sent = self.client.sent_messages()[0]
        self.assertEqual(sent["message"], brand.FULL_TEXT)
        kinds = [type(e).__name__ for e in sent["entities"]]
        self.assertIn("MessageEntityBlockquote", kinds)   # نقل‌قول شیشه‌ای
        self.assertEqual(kinds.count("MessageEntityBold"), len(brand.BODY_LINES))

    def test_first_ai_cod_wins_even_in_a_different_group(self):
        run(self.send("AI  COD", user_id=USER_3, chat_id=GROUP_C))
        self.assertEqual(self.store.get_owner().user_id, USER_3)
        self.assertEqual(len(self.client.requests), 1)

    def test_2_second_user_in_another_group_cannot_become_owner(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        requests_after_owner = len(self.client.requests)

        run(self.send("ai cod", user_id=USER_2, chat_id=GROUP_B))

        self.assertEqual(self.store.get_owner().user_id, USER_1, "مالک عوض شد!")
        self.assertEqual(
            len(self.client.requests), requests_after_owner,
            "کاربر دوم باید کاملاً نادیده گرفته شود (هیچ پیامی ارسال نشود)",
        )

    def test_3_same_owner_repeating_toggles_global_suspend(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))   # claim
        owner_before = self.store.get_owner()
        self.assertTrue(self.store.is_active(), "بعد از claim باید ربات روشن باشد")

        # دومین «ai cod» → ربات خاموش می‌شود
        self.client.clear_requests()
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_B))
        self.assertEqual(owner_before, self.store.get_owner())     # رکورد دست‌نخورده
        self.assertTrue(self.store.is_global_suspended())
        self.assertFalse(self.store.is_active())
        self.assertEqual(self.client.text_messages(), [brand.BOT_OFF_TEXT])

        # سومین «ai cod» → ربات دوباره روشن می‌شود
        self.client.clear_requests()
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.assertFalse(self.store.is_global_suspended())
        self.assertTrue(self.store.is_active())
        self.assertEqual(self.client.text_messages(), [brand.BOT_ON_TEXT])

    def test_4_when_suspended_nothing_works_except_owner_ai_cod(self):
        # فعال می‌کنیم، بعد خاموش
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.assertTrue(self.store.is_global_suspended())
        self.client.clear_requests()

        # «کدرز»، پیام معمولی، ai online و PV هیچ پاسخی ندارند
        run(self.send("کدرز", user_id=USER_2, chat_id=GROUP_B))
        run(self.send("سلام", user_id=USER_2, chat_id=GROUP_A))
        run(self.send("ai online", user_id=USER_1, chat_id=GROUP_A))
        run(self.send("سلام", user_id=USER_3, chat_id=USER_3, is_group=False))
        self.assertEqual(self.client.requests, [],
                         "در حالت خاموش، همه باید بی‌پاسخ بمانند")

        # «ai cod» از غیرمالک هم اثری ندارد
        run(self.send("ai cod", user_id=USER_2, chat_id=GROUP_B))
        self.assertTrue(self.store.is_global_suspended())
        self.assertEqual(self.client.requests, [])

        # «ai cod» از مالک → دوباره روشن
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.assertFalse(self.store.is_global_suspended())
        self.assertEqual(self.client.text_messages(), [brand.BOT_ON_TEXT])

        # بعد از روشن شدن، «کدرز» دوباره کار می‌کند
        self.client.clear_requests()
        run(self.send("کدرز", user_id=USER_2, chat_id=GROUP_B))
        self.assertEqual(len(self.client.requests), 1)

    def test_4b_owner_can_toggle_from_pv(self):
        # از گروه claim می‌کنیم
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.client.clear_requests()
        # از PV «ai cod» می‌زنیم → باید ربات خاموش شود
        run(self.send("ai cod", user_id=USER_1, chat_id=USER_1, is_group=False))
        self.assertTrue(self.store.is_global_suspended())
        self.assertEqual(self.client.text_messages(), [brand.BOT_OFF_TEXT])
        # پیام‌های دیگر بی‌پاسخ
        run(self.send("کدرز", user_id=USER_2, chat_id=GROUP_A))
        self.assertEqual(len(self.client.requests), 1, "کدرز در حالت خاموش نباید پاسخ بدهد")
        # دوباره از PV روشن می‌کنیم
        run(self.send("ai cod", user_id=USER_1, chat_id=USER_1, is_group=False))
        self.assertFalse(self.store.is_global_suspended())
        self.assertIn(brand.BOT_ON_TEXT, self.client.text_messages())
        # کدرز دوباره کار می‌کند
        self.client.clear_requests()
        run(self.send("کدرز", user_id=USER_2, chat_id=GROUP_A))
        self.assertEqual(len(self.client.requests), 1)

    def test_5_suspend_persists_after_restart(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.assertTrue(self.store.is_global_suspended())
        self.store.close()

        store2 = OwnerStore(self.db)
        try:
            self.assertTrue(store2.is_global_suspended(),
                            "وضعیت خاموشی باید بعد از restart هم باقی بماند")
            self.assertFalse(store2.is_active())
            # روشن کردن مجدد
            core2 = BotCore(self.cfg, store2, BrandSender(self.cfg))
            client2 = FakeClient()
            run(core2.on_new_message(client2, FakeEvent(
                "ai cod", user_id=USER_1, chat_id=GROUP_A, is_group=True)))
            self.assertFalse(store2.is_global_suspended())
            self.assertTrue(store2.is_active())
            self.assertEqual(client2.text_messages(), [brand.BOT_ON_TEXT])
        finally:
            store2.close()
            self.store = OwnerStore(self.db)

    def test_owner_repeat_always_replies_with_toggle_message(self):
        # پیام تاگل «خاموش/روشن» صرف‌نظر از تنظیم announce_on_owner_repeat
        # همیشه فرستاده می‌شود (فرمان کنترلی است).
        cfg = dataclasses.replace(self.cfg, announce_on_owner_repeat=False)
        core = BotCore(cfg, self.store, BrandSender(cfg))
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        before = len(self.client.requests)

        event = FakeEvent("ai cod", user_id=USER_1, chat_id=GROUP_B)
        run(core.on_new_message(self.client, event))

        self.assertEqual(len(self.client.requests), before + 1)
        self.assertEqual(self.client.requests[-1].message, brand.BOT_OFF_TEXT)
        self.assertEqual(self.store.get_owner().user_id, USER_1)

    def test_owner_persists_after_restart(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.store.close()

        store2 = OwnerStore(self.db)           # ری‌استارت ربات
        try:
            self.assertEqual(store2.get_owner().user_id, USER_1)
            core2 = BotCore(self.cfg, store2, BrandSender(self.cfg))
            client2 = FakeClient()
            event = FakeEvent("ai cod", user_id=USER_2, chat_id=GROUP_C)
            run(core2.on_new_message(client2, event))

            self.assertEqual(store2.get_owner().user_id, USER_1)
            self.assertEqual(len(client2.requests), 0)
        finally:
            store2.close()
            self.store = OwnerStore(self.db)


class TestOwnerActivationInGroups(FlowTestCase):
    """«ai cod»/«ai code» فقط برای مالک سراسری است.

    رفتار نهایی:
      * اولین «ai cod» → claim می‌کند + پیام معرفی (برند) می‌فرستد و ربات را روشن می‌کند.
      * «ai cod» بعدی از مالک → تاگل خاموش/روشن (پیام کوتاه «ربات خاموش/روشن شد»).
      * غیرمالک → هیچ‌چیز.
    """

    def test_owner_first_activation_sends_intro(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))     # ثبت‌نام مالک
        self.assertEqual(self.store.get_owner().user_id, USER_1)
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)
        self.assertTrue(self.store.is_active())

    def test_ai_code_spelling_also_claims_owner_and_sends_intro(self):
        run(self.send("ai code", user_id=USER_1, chat_id=GROUP_B))
        self.assertEqual(self.store.get_owner().user_id, USER_1)
        self.assertEqual(self.client.text_messages(), [brand.FULL_TEXT])
        first = self.client.requests[0]
        self.assertTrue(first.entities, "متن معرفی باید همان قالب Blockquote+Bold را داشته باشد")

    def test_second_ai_cod_toggles_suspend(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        first_text = self.client.requests[0].message
        self.client.clear_requests()

        run(self.send("ai code", user_id=USER_1, chat_id=GROUP_B))    # خاموش
        self.assertEqual(self.client.text_messages(), [brand.BOT_OFF_TEXT])
        self.assertTrue(self.store.is_global_suspended())

        self.client.clear_requests()
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_C))     # روشن
        self.assertEqual(self.client.text_messages(), [brand.BOT_ON_TEXT])
        self.assertFalse(self.store.is_global_suspended())
        # دیگر خبری از متن FULL_TEXT در دفعات بعد نیست (فقط پیام تاگل)
        self.assertNotIn(first_text, self.client.text_messages())

    def test_non_owner_gets_nothing_and_changes_nothing(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.client.clear_requests()

        for text in ("ai cod", "ai code", "AI CODE"):
            run(self.send(text, user_id=USER_2, chat_id=GROUP_B))
            run(self.send(text, user_id=USER_3, chat_id=GROUP_C))

        self.assertEqual(self.client.requests, [], "غیرمالک نباید هیچ پیامی بگیرد")
        self.assertEqual(self.store.get_owner().user_id, USER_1)

    def test_activation_does_not_touch_ai_on_off_state(self):
        """روشن/خاموش‌کردن AI همچنان فقط با «ai online»/«ai of» است و «ai cod» آن را تغییر نمی‌دهد."""
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.assertFalse(self.store.ai_is_enabled(GROUP_A))

        self.client.clear_requests()
        run(self.send("ai code", user_id=USER_1, chat_id=GROUP_A))    # خاموش کلی
        self.assertFalse(self.store.ai_is_enabled(GROUP_A), "«ai cod» نباید وضعیت AI را عوض کند")
        self.assertTrue(self.store.is_global_suspended())

        # روشن کردن مجدد ربات
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.assertFalse(self.store.ai_is_enabled(GROUP_A))

        run(self.send("ai online", user_id=USER_1, chat_id=GROUP_A))
        self.assertTrue(self.store.ai_is_enabled(GROUP_A))

    def test_toggle_messages_are_sent_regardless_of_repeat_flag(self):
        cfg = dataclasses.replace(self.cfg, announce_on_owner_repeat=False)
        core = BotCore(cfg, self.store, BrandSender(cfg))
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        before = len(self.client.requests)

        # announce_on_owner_repeat برای تکرار «پیام برند» بود؛ حالا «ai cod»
        # فرمان کنترلی تاگل است و همیشه پیام «ربات خاموش/روشن» می‌فرستد.
        run(core.on_new_message(self.client, FakeEvent("ai cod", user_id=USER_1, chat_id=GROUP_B)))
        self.assertEqual(len(self.client.requests), before + 1)
        self.assertEqual(self.client.requests[-1].message, brand.BOT_OFF_TEXT)

    def test_pv_ai_cod_does_not_activate_or_claim_owner(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=USER_1, is_group=False))
        run(self.send("ai code", user_id=USER_2, chat_id=USER_2, is_group=False))

        self.assertIsNone(self.store.get_owner(), "در PV هیچ مالکی ثبت نمی‌شود")
        for group in (GROUP_A, GROUP_B):
            self.assertFalse(self.store.ai_is_enabled(group))

    def test_kodrez_still_works_for_everyone_in_every_group(self):
        """طبق تصمیم کاربر: «کدرز» برای همه آزاد می‌ماند؛ فقط دستورهای AI/مدیریتی مالک‌محورند."""
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))   # مالک: USER_1
        self.client.clear_requests()

        for user in (USER_1, USER_2, USER_3):
            for group in (GROUP_A, GROUP_B):
                self.client.clear_requests()
                run(self.send("کدرز", user_id=user, chat_id=group))
                self.assertEqual(len(self.client.requests), 1)
                self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)

    def test_ai_management_commands_stay_owner_only(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        self.client.clear_requests()

        for text in ("ai online", "ai of", "ai list", "ai list x", "تعداد اعضا", "لیست اعضا"):
            run(self.send(text, user_id=USER_2, chat_id=GROUP_B))

        self.assertEqual(self.client.requests, [], "هیچ دستور مدیریتی برای غیرمالک اجرا نمی‌شود")
        self.assertFalse(self.store.ai_is_enabled(GROUP_B))
        self.assertEqual(self.store.ai_allowed_users(GROUP_B), [])


class TestKodrez(FlowTestCase):
    def setUp(self):
        super().setUp()
        self._claim_owner()     # «کدرز» بعد از فعال‌سازی ربات کار می‌کند

    def test_9_kodrez_works_in_every_group_for_every_user(self):
        scenarios = [(USER_2, GROUP_A), (USER_3, GROUP_B), (USER_1, GROUP_C)]

        for user_id, chat_id in scenarios:
            run(self.send("کدرز", user_id=user_id, chat_id=chat_id))

        self.assertEqual(len(self.client.requests), 3, "برای هر گروه باید یک پیام ارسال شود")
        for sent in self.client.sent_messages():
            self.assertEqual(sent["message"], brand.FULL_TEXT)
            kinds = [type(e).__name__ for e in sent["entities"]]
            self.assertIn("MessageEntityBlockquote", kinds)
            self.assertEqual(kinds.count("MessageEntityBold"), len(brand.BODY_LINES))
            self.assertIn(brand.LINK_LINE, sent["message"])

    def test_kodrez_does_not_change_the_owner(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        run(self.send("کدرز", user_id=USER_2, chat_id=GROUP_B))
        self.assertEqual(self.store.get_owner().user_id, USER_1)

    def test_kodrez_with_half_space(self):
        run(self.send("ک\u200cدرز", user_id=USER_2, chat_id=GROUP_B))
        self.assertEqual(len(self.client.requests), 1)

    def test_kodrez_is_silent_before_owner_claim(self):
        # قبل از اولین «ai cod» هیچ پیامی (حتی «کدرز») نباید ارسال شود
        # برای این سناریو از یک store خالی استفاده می‌کنیم (setUp مالک را ثبت کرده)
        import tempfile as _tmp, pathlib as _pl
        with _tmp.TemporaryDirectory() as td:
            store = OwnerStore(_pl.Path(td) / "o.sqlite3")
            client = FakeClient()
            core = BotCore(self.cfg, store, BrandSender(self.cfg))
            async def go():
                await core.on_new_message(client, FakeEvent("کدرز", user_id=USER_2, chat_id=GROUP_A, is_group=True))
            run(go())
            self.assertEqual(len(client.requests), 0,
                             "قبل از فعال‌سازی «کدرز» باید بی‌صدا باشد")
            self.assertIsNone(store.get_owner())
            store.close()


class TestGuards(FlowTestCase):
    def test_own_messages_are_ignored(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A, out=True))
        self.assertIsNone(self.store.get_owner())
        self.assertEqual(len(self.client.requests), 0)

    def test_private_ai_cod_is_silent_before_owner_claim(self):
        # «ai cod» در PV قبل از فعال‌سازی: نه مالک ثبت می‌شود، نه پیام معرفی
        run(self.send("ai cod", user_id=USER_1, chat_id=USER_1, is_group=False))
        self.assertIsNone(self.store.get_owner())
        self.assertEqual(len(self.client.requests), 0,
                         "قبل از فعال‌سازی، PV باید کاملاً بی‌صدا باشد")

    def test_private_ai_cod_does_not_change_owner_but_sends_intro_after_activation(self):
        # بعد از فعال‌سازی، «ai cod» در PV مثل هر پیام PV دیگری برای کاربر جدید
        # رفتار می‌کند (معرفی + منو) و مالک را تغییر نمی‌دهد.
        self._claim_owner(user_id=USER_1)
        run(self.send("ai cod", user_id=USER_2, chat_id=USER_2, is_group=False))
        self.assertEqual(self.store.get_owner().user_id, USER_1)
        self.assertEqual(len(self.client.requests), 2)          # معرفی + منو
        self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)
        self.assertEqual(self.client.requests[1].message, brand.MENU_TEXT)

    def test_unrelated_text_is_ignored(self):
        run(self.send("سلام، ai cod چیه؟", user_id=USER_1, chat_id=GROUP_A))
        self.assertIsNone(self.store.get_owner())
        self.assertEqual(len(self.client.requests), 0)


class TestFormattingFallback(FlowTestCase):
    def setUp(self):
        super().setUp()
        self._claim_owner()

    def test_fallback_when_server_rejects_blockquote(self):
        client = FakeClient(reject_blockquote=True)
        core = BotCore(self.cfg, self.store, BrandSender(self.cfg))

        async def scenario():
            event = FakeEvent("کدرز", user_id=USER_1, chat_id=GROUP_A)
            await core.on_new_message(client, event)

        run(scenario())

        # blockquote رد شد → فقط یک درخواست (bold-only) ثبت شده است
        self.assertEqual(len(client.requests), 1)
        entities = client.requests[0].entities
        self.assertTrue(all(isinstance(e, types.MessageEntityBold) for e in entities))
        self.assertEqual(len(entities), len(brand.BODY_LINES))
        self.assertEqual(client.requests[0].message, brand.FULL_TEXT)

    def test_reply_quote_mode_builds_real_quote_reply(self):
        cfg = dataclasses.replace(self.cfg, quote_mode="reply")
        sender = BrandSender(cfg)
        client = FakeClient()

        async def scenario():
            return await sender.send(client, GROUP_A, quote_from_msg_id=555)

        report = run(scenario())

        self.assertTrue(report.ok)
        self.assertEqual(report.mode, "reply_quote+bold")
        req = client.requests[0]
        self.assertIsInstance(req.reply_to, types.InputReplyToMessage)
        self.assertEqual(req.reply_to.reply_to_msg_id, 555)
        self.assertEqual(req.reply_to.quote_text, brand.QUOTE_LINE)   # نقل‌قول واقعی
        self.assertTrue(req.reply_to.quote_entities)
        self.assertNotIn(brand.QUOTE_LINE, req.message)               # تیتر داخل قاب نقل‌قول
        self.assertIn(brand.LINK_LINE, req.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPrivateChat(FlowTestCase):
    """رفتار جدید: هر پیام خصوصی ورودی → همان پیام معرفی (بدون نیاز به دستور).

    توجه: این پاسخ‌ها فقط **بعد از فعال‌سازی ربات** (پس از اولین «ai cod» در گروه)
    ارسال می‌شوند؛ قبل از آن کاربر PV ثبت می‌شود ولی هیچ پیامی نمی‌گیرد.
    """

    def setUp(self):
        super().setUp()
        self._claim_owner()

    @staticmethod
    def _entity_signature(entities):
        return sorted((type(e).__name__, e.offset, e.length) for e in entities)

    def test_10_private_message_triggers_introduction(self):
        # اولین پیام PV → معرفی (همان قالب قبلی) + منوی انتخاب؛ هرکدام یک‌بار
        run(self.send("سلام، قیمت سایت چنده؟", user_id=USER_2, chat_id=USER_2, is_group=False))

        self.assertEqual(len(self.client.requests), 2, "باید معرفی + منو ارسال شود")
        sent = self.client.sent_messages()[0]
        self.assertEqual(sent["message"], brand.FULL_TEXT)
        kinds = [type(e).__name__ for e in sent["entities"]]
        self.assertIn("MessageEntityBlockquote", kinds)          # نقل‌قول شیشه‌ای
        self.assertEqual(kinds.count("MessageEntityBold"), len(brand.BODY_LINES))
        self.assertIn(brand.LINK_LINE, sent["message"])
        self.assertEqual(self.client.sent_messages()[1]["message"], brand.MENU_TEXT)

    def test_private_message_without_text_still_triggers(self):
        # «مهم نیست متن پیامش چیست» — حتی پیام بدون متن (مثلاً مدیا)
        run(self.send("", user_id=USER_3, chat_id=USER_3, is_group=False))
        self.assertEqual(len(self.client.requests), 2)          # معرفی + منو
        self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)
        self.assertEqual(self.client.requests[1].message, brand.MENU_TEXT)

    def test_private_reply_goes_only_to_the_sender(self):
        run(self.send("هر متنی", user_id=USER_2, chat_id=USER_2, is_group=False))

        self.assertEqual(len(self.client.requests), 2)          # معرفی + منو
        for req in self.client.requests:                        # همه فقط برای همان کاربر
            self.assertIsInstance(req.peer, types.InputPeerUser)
            self.assertEqual(req.peer.user_id, USER_2)

    def test_private_kodrez_sends_exactly_one_intro(self):
        # «کدرز» فقط دستور گروهی است؛ در PV گزینه‌ی منو نیست →
        # کاربر فقط همان معرفی + منوی اولین پیام را می‌گیرد (بدون پاسخ اضافه)
        run(self.send("کدرز", user_id=USER_2, chat_id=USER_2, is_group=False))
        self.assertEqual(self.client.text_messages(),
                         [brand.FULL_TEXT, brand.MENU_TEXT])
        self.assertEqual(self.store.get_owner().user_id, USER_1)

    def test_private_ai_cod_never_sets_owner(self):
        run(self.send("ai cod", user_id=USER_2, chat_id=USER_2, is_group=False))
        # «ai cod» در PV نباید مالک را تغییر دهد (مالک همچنان USER_1 از setUp است)
        self.assertEqual(self.store.get_owner().user_id, USER_1)

    def test_private_outgoing_is_ignored(self):
        # جلوگیری از loop: پاسخ خودِ یوزربات نباید دوباره پردازش شود
        run(self.send(brand.FULL_TEXT, user_id=USER_1, chat_id=USER_2,
                      is_group=False, out=True))
        self.assertEqual(len(self.client.requests), 0)

    def test_private_and_group_intro_are_byte_identical(self):
        run(self.send("کدرز", user_id=USER_2, chat_id=GROUP_A))
        run(self.send("سلام", user_id=USER_3, chat_id=USER_3, is_group=False))

        group_req, private_req = self.client.requests[0], self.client.requests[1]
        self.assertEqual(group_req.message, private_req.message)
        self.assertEqual(
            self._entity_signature(group_req.entities),
            self._entity_signature(private_req.entities),
            "قالب Blockquote + Bold در گروه و PV باید دقیقاً یکسان باشد",
        )

    def test_private_feature_can_be_disabled_by_config(self):
        cfg = dataclasses.replace(self.cfg, private_auto_reply=False)
        core = BotCore(cfg, self.store, BrandSender(cfg))
        event = FakeEvent("سلام", user_id=USER_2, chat_id=USER_2, is_group=False)
        run(core.on_new_message(self.client, event))
        self.assertEqual(len(self.client.requests), 0)


class TestGroupRegression(FlowTestCase):
    """اطمینان از اینکه رفتار گروه‌ها تغییر نکرده است."""

    def setUp(self):
        super().setUp()
        self._claim_owner()

    def test_group_message_without_commands_is_still_ignored(self):
        run(self.send("سلام به همه", user_id=USER_2, chat_id=GROUP_A))
        run(self.send("ai cod چیه؟", user_id=USER_3, chat_id=GROUP_B))
        self.assertEqual(len(self.client.requests), 0)
        self.assertEqual(self.store.get_owner().user_id, USER_1)

    def test_kodrez_in_group_still_works(self):
        run(self.send("کدرز", user_id=USER_2, chat_id=GROUP_A))
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)

    def test_ai_cod_in_group_still_sets_owner_once(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        run(self.send("ai cod", user_id=USER_2, chat_id=GROUP_B))
        self.assertEqual(self.store.get_owner().user_id, USER_1)
        self.assertEqual(len(self.client.requests), 1)
