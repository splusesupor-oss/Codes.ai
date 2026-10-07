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

    def test_3_same_owner_repeating_does_not_create_new_owner(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        owner_before = self.store.get_owner()
        requests_before = len(self.client.requests)

        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_B))

        self.assertEqual(owner_before, self.store.get_owner())   # رکورد دست‌نخورده
        self.assertEqual(len(self.client.requests), requests_before)

    def test_owner_repeat_with_announce_flag(self):
        cfg = dataclasses.replace(self.cfg, announce_on_owner_repeat=True)
        core = BotCore(cfg, self.store, BrandSender(cfg))
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        before = len(self.client.requests)

        event = FakeEvent("ai cod", user_id=USER_1, chat_id=GROUP_B)
        run(core.on_new_message(self.client, event))

        self.assertEqual(len(self.client.requests), before + 1)    # فقط پیام معرفی
        self.assertEqual(self.store.get_owner().user_id, USER_1)   # مالک جدید نه

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


class TestKodrez(FlowTestCase):
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


class TestGuards(FlowTestCase):
    def test_own_messages_are_ignored(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A, out=True))
        self.assertIsNone(self.store.get_owner())
        self.assertEqual(len(self.client.requests), 0)

    def test_private_ai_cod_does_not_set_owner_but_sends_intro(self):
        # «ai cod» فقط در گروه مالک تعیین می‌کند؛ در PV مالک ثبت نمی‌شود
        # ولی طبق رفتار جدید، پیام معرفی برای همان کاربر ارسال می‌شود.
        run(self.send("ai cod", user_id=USER_1, chat_id=USER_1, is_group=False))
        self.assertIsNone(self.store.get_owner())
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)

    def test_unrelated_text_is_ignored(self):
        run(self.send("سلام، ai cod چیه؟", user_id=USER_1, chat_id=GROUP_A))
        self.assertIsNone(self.store.get_owner())
        self.assertEqual(len(self.client.requests), 0)


class TestFormattingFallback(FlowTestCase):
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
    """رفتار جدید: هر پیام خصوصی ورودی → همان پیام معرفی (بدون نیاز به دستور)."""

    @staticmethod
    def _entity_signature(entities):
        return sorted((type(e).__name__, e.offset, e.length) for e in entities)

    def test_10_private_message_triggers_introduction(self):
        run(self.send("سلام، قیمت سایت چنده؟", user_id=USER_2, chat_id=USER_2, is_group=False))

        self.assertEqual(len(self.client.requests), 1, "باید دقیقاً یک پاسخ ارسال شود")
        sent = self.client.sent_messages()[0]
        self.assertEqual(sent["message"], brand.FULL_TEXT)
        kinds = [type(e).__name__ for e in sent["entities"]]
        self.assertIn("MessageEntityBlockquote", kinds)          # نقل‌قول شیشه‌ای
        self.assertEqual(kinds.count("MessageEntityBold"), len(brand.BODY_LINES))
        self.assertIn(brand.LINK_LINE, sent["message"])

    def test_private_message_without_text_still_triggers(self):
        # «مهم نیست متن پیامش چیست» — حتی پیام بدون متن (مثلاً مدیا)
        run(self.send("", user_id=USER_3, chat_id=USER_3, is_group=False))
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)

    def test_private_reply_goes_only_to_the_sender(self):
        run(self.send("هر متنی", user_id=USER_2, chat_id=USER_2, is_group=False))

        req = self.client.requests[0]
        self.assertIsInstance(req.peer, types.InputPeerUser)
        self.assertEqual(req.peer.user_id, USER_2)      # فقط برای همان کاربر
        self.assertEqual(len(self.client.requests), 1)  # و برای هیچ چت دیگری نه

    def test_private_kodrez_sends_exactly_one_intro(self):
        run(self.send("کدرز", user_id=USER_2, chat_id=USER_2, is_group=False))
        self.assertEqual(len(self.client.requests), 1)   # دوبار ارسال نمی‌شود
        self.assertIsNone(self.store.get_owner())

    def test_private_ai_cod_never_sets_owner(self):
        run(self.send("ai cod", user_id=USER_2, chat_id=USER_2, is_group=False))
        self.assertIsNone(self.store.get_owner())

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

    def test_group_message_without_commands_is_still_ignored(self):
        run(self.send("سلام به همه", user_id=USER_2, chat_id=GROUP_A))
        run(self.send("ai cod چیه؟", user_id=USER_3, chat_id=GROUP_B))
        self.assertEqual(len(self.client.requests), 0)
        self.assertIsNone(self.store.get_owner())

    def test_kodrez_in_group_still_works(self):
        run(self.send("کدرز", user_id=USER_2, chat_id=GROUP_A))
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(self.client.requests[0].message, brand.FULL_TEXT)

    def test_ai_cod_in_group_still_sets_owner_once(self):
        run(self.send("ai cod", user_id=USER_1, chat_id=GROUP_A))
        run(self.send("ai cod", user_id=USER_2, chat_id=GROUP_B))
        self.assertEqual(self.store.get_owner().user_id, USER_1)
        self.assertEqual(len(self.client.requests), 1)
