"""
تست‌های جامع ویژگی‌های جدید:
۱) فیلتر کلمات تبلیغاتی و ضد دور زدن (Anti-Bypass Filter)
۲) دستورات Flter، x و list flter
۳) حذف پیام، تگ مالک، و فرمت پیام هشدار تبلیغات
۴) تزریق کانتکست کاربری که پیام روی او ریپلای شده (Replied User Query Context)
"""

import asyncio
import os
import unittest
from splusthon import types
from splusthon.tl.types import MessageEntityBlockquote, MessageEntityBold, MessageEntitySpoiler

from config import Config
from storage import OwnerStore
from core import BotCore, match_filter_command
from brand import (
    build_filter_pattern,
    contains_filtered_word,
    build_ad_warning_message,
    build_filter_list_message,
)
from sender import BrandSender
from ai_service import GroupAI
from tests.fakes import FakeClient, FakeReplyMessage, FakeAI


def run(coro):
    return asyncio.run(coro)


class FakeEvent:
    def __init__(
        self,
        text: str,
        sender_id: int,
        chat_id: int = -100123456,
        is_group: bool = True,
        is_private: bool = False,
        reply_to=None,
        msg_id: int = 42,
        sender=None,
    ):
        self.raw_text = text
        self.text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.is_group = is_group
        self.is_private = is_private
        self.id = msg_id
        self.reply_to = reply_to
        self.is_reply = reply_to is not None
        self.out = False
        self.sender = sender

    async def get_reply_message(self):
        return self.reply_to


class FakeSenderUser:
    def __init__(self, user_id: int, username: str = None, first_name: str = "User", last_name: str = ""):
        self.id = user_id
        self.username = username
        self.first_name = first_name
        self.last_name = last_name


class TestAdFilterAndRepliedUser(unittest.TestCase):
    def setUp(self):
        self.db_path = f"test_filter_{os.getpid()}_{id(self)}.db"
        self.store = OwnerStore(self.db_path)
        self.cfg = Config()
        self.sender = BrandSender(self.cfg)
        self.client = FakeClient()
        self.ai_client = FakeAI()
        self.ai = GroupAI(self.cfg, self.store, self.sender, self.ai_client)
        self.core = BotCore(self.cfg, self.store, sender=self.sender, ai=self.ai)

        self.owner_id = 111111
        self.group_id = -100123456
        self.store.claim(self.owner_id, chat_id=self.group_id, message_id=1, display_name="مالک", username="global_owner")
        self.store.activate_group(self.group_id, self.owner_id, group_name="گروه تست")
        self.store.ai_set_enabled(self.group_id, True)

    def tearDown(self):
        for f in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    # =========================================================================
    # ۱) تست‌های تطبیق و الگوی ضد دور زدن (Anti-Bypass Regex)
    # =========================================================================
    def test_01_filter_pattern_anti_bypass(self):
        word = "کانال"
        pat = build_filter_pattern(word)

        bypass_attempts = [
            "سلام به کانال ما بپیوندید",
            "کــــانــــال",
            "ک.ا.ن.ا.ل",
            "ک/ا/ن/ا/ل",
            "ک#ا#ن#ا#ل",
            "ک_ا_ن_ا_ل",
            "ک-ا-ن-ا-ل",
            "ک٫ا٫ن٫ا٫ل",
            "ک   ا  ن  ا  ل",
            "ک\u200cا\u200cن\u200cا\u200cل",
            "ککککاااانننااال",
            "کـــ.ـا#ن_ا..ل",
            "كـانـال",  # عربی
        ]
        for text in bypass_attempts:
            self.assertTrue(
                bool(pat.search(text)),
                f"الگو موفق به شناسایی تلاش دور زدن «{text}» نشد!"
            )

        # پیام‌های عادی بدون کلمه نباید فالس‌پازیتیو بدهند
        self.assertFalse(bool(pat.search("دکان الهیه")))
        self.assertFalse(bool(pat.search("سلام حال شما چطوره؟")))

    def test_02_contains_filtered_word(self):
        filtered = ["کانال", "لینک", "فروشگاه"]
        self.assertEqual(contains_filtered_word("عضو ک.ا.ن.ا.ل شوید", filtered), "کانال")
        self.assertEqual(contains_filtered_word("اینم لـیـنـک ما", filtered), "لینک")
        self.assertIsNone(contains_filtered_word("سلام روز بخیر", filtered))

    # =========================================================================
    # ۲) فرمت پیام هشدار و اسپویلر در لیست فیلتر
    # =========================================================================
    def test_03_build_ad_warning_message_entities(self):
        user_label = "@spammer_bot"
        text, entities = build_ad_warning_message(user_label)

        self.assertIn("⚠️ کاربر  : « @spammer_bot »", text)
        self.assertIn("در حال ارسال کلمات تبلیغاتی و هرزنامه هست", text)

        # خط ۱ باید هم Blockquote و هم Bold باشد
        blockquotes = [e for e in entities if isinstance(e, MessageEntityBlockquote)]
        bolds = [e for e in entities if isinstance(e, MessageEntityBold)]

        self.assertEqual(len(blockquotes), 1)
        self.assertGreaterEqual(len(bolds), 2)
        self.assertEqual(blockquotes[0].offset, 0)
        self.assertEqual(bolds[0].offset, 0)

    def test_04_build_filter_list_message_spoiler_entities(self):
        words = ["کانال", "فروشگاه"]
        text, entities = build_filter_list_message(words)

        self.assertIn("کانال", text)
        self.assertIn("فروشگاه", text)

        spoilers = [e for e in entities if isinstance(e, MessageEntitySpoiler)]
        self.assertEqual(len(spoilers), 2)

    # =========================================================================
    # ۳) دستورات Flter / x / list flter
    # =========================================================================
    def test_05_match_filter_command(self):
        self.assertEqual(match_filter_command("Flter کانال"), ("add", "کانال"))
        self.assertEqual(match_filter_command("filter لینک"), ("add", "لینک"))
        self.assertEqual(match_filter_command("x کانال"), ("remove", "کانال"))
        self.assertEqual(match_filter_command("list flter"), ("list", ""))
        self.assertEqual(match_filter_command("list filter"), ("list", ""))
        self.assertIsNone(match_filter_command("سلام"))

    def test_06_filter_commands_execution(self):
        # مالک کلمه «کانال» را اضافه می‌کند
        ev_add = FakeEvent("Flter کانال", sender_id=self.owner_id, chat_id=self.group_id)
        run(self.core.on_new_message(self.client, ev_add))
        self.assertIn("✅ کلمه «کانال» به لیست فیلتر تبلیغات این گروه اضافه شد.", self.client.text_messages()[-1])
        self.assertIn("کانال", self.store.get_filtered_words(self.group_id))

        # مشاهده لیست فیلتر
        ev_list = FakeEvent("list flter", sender_id=self.owner_id, chat_id=self.group_id)
        run(self.core.on_new_message(self.client, ev_list))
        last_req = self.client.last_request()
        self.assertTrue(any(isinstance(e, MessageEntitySpoiler) for e in getattr(last_req, "entities", [])))

        # حذف کلمه
        ev_rem = FakeEvent("x کانال", sender_id=self.owner_id, chat_id=self.group_id)
        run(self.core.on_new_message(self.client, ev_rem))
        self.assertIn("✅ کلمه «کانال» از لیست فیلتر تبلیغات این گروه حذف شد.", self.client.text_messages()[-1])
        self.assertNotIn("کانال", self.store.get_filtered_words(self.group_id))

    def test_07_unauthorized_user_cannot_run_filter_commands(self):
        unauth_user = 999
        ev_add = FakeEvent("Flter کانال", sender_id=unauth_user, chat_id=self.group_id)
        run(self.core.on_new_message(self.client, ev_add))
        self.assertEqual(self.store.get_filtered_words(self.group_id), [])

    # =========================================================================
    # ۴) شناسایی تبلیغات، حذف پیام، تگ مالک و ارسال پیام هشدار
    # =========================================================================
    def test_08_ad_detection_deletes_message_tags_owner_and_sends_warning(self):
        # افزودن کلمه فیلتر
        self.store.add_filtered_word(self.group_id, "کانال", self.owner_id)

        spammer_id = 555
        spammer_sender = FakeSenderUser(spammer_id, username="bad_spammer", first_name="اسپمر")
        ad_ev = FakeEvent(
            "سلام دوستان به کـــــ.ا.ن.ا.ل ما سر بزنید",
            sender_id=spammer_id,
            chat_id=self.group_id,
            msg_id=777,
            sender=spammer_sender,
        )

        self.client.clear_requests()
        run(self.core.on_new_message(self.client, ad_ev))

        # ۱) پیام باید حذف شده باشد
        self.assertEqual(len(self.client.deleted_messages), 1)
        del_chat, del_ids = self.client.deleted_messages[0]
        self.assertEqual(del_chat, self.group_id)
        self.assertEqual(del_ids, [777])

        # ۲) تگ مالک ارسال شده باشد
        texts = self.client.text_messages()
        self.assertTrue(any("@global_owner" in t for t in texts))

        # ۳) پیام هشدار با فرمت دقیق ارسال شده باشد
        warn_msg = [t for t in texts if "⚠️ کاربر" in t]
        self.assertEqual(len(warn_msg), 1)
        self.assertIn("⚠️ کاربر  : « @bad_spammer »", warn_msg[0])
        self.assertIn("در حال ارسال کلمات تبلیغاتی و هرزنامه هست", warn_msg[0])

        # ۴) عدم تکرار هشدار برای همان شناسه پیام (Idempotent)
        self.client.clear_requests()
        run(self.core.on_new_message(self.client, ad_ev))
        self.assertEqual(self.client.text_messages(), [])

    def test_09_owners_are_exempt_from_ad_filter(self):
        self.store.add_filtered_word(self.group_id, "کانال", self.owner_id)
        owner_ev = FakeEvent("کلمه کانال را تست می‌کنم", sender_id=self.owner_id, chat_id=self.group_id)

        self.client.clear_requests()
        run(self.core.on_new_message(self.client, owner_ev))
        # نباید پیامی حذف شود یا هشداری تولید شود
        self.assertEqual(self.client.deleted_messages, [])

    # =========================================================================
    # ۵) ریپلای روی پیام سایر کاربران جهت پرسش از هوش مصنوعی (Replied User Query)
    # =========================================================================
    def test_10_authorized_user_asking_about_replied_user(self):
        target_user_id = 888
        target_sender = FakeSenderUser(target_user_id, username="target_member", first_name="هدف")
        replied_msg = FakeReplyMessage(
            sender_id=target_user_id,
            text="سلام من عضو جدید هستم و در حوزه پایتون کار می‌کنم",
            msg_id=123,
            sender=target_sender,
        )

        # پیام‌های قبلی این کاربر در گروه ثبت شده باشد
        self.ai.record_group_message(self.group_id, "@target_member", "پیام ۱ هدف", user_id=target_user_id)
        self.ai.record_group_message(self.group_id, "@target_member", "پیام ۲ هدف", user_id=target_user_id)

        # مالک روی پیام این کاربر ریپلای کرده و با ذکر «ai» از هوش مصنوعی سوال می‌پرسد
        query_ev = FakeEvent(
            "ai نام کاربری این کاربر چیه و تحلیل رفتاریش چطوره؟",
            sender_id=self.owner_id,
            chat_id=self.group_id,
            reply_to=replied_msg,
            msg_id=124,
        )

        self.client.clear_requests()
        run(self.core.on_new_message(self.client, query_ev))

        # هوش مصنوعی باید فراخوانی شده باشد
        self.assertGreater(len(self.ai_client.calls), 0)
        system_content = self.ai_client.calls[-1][0]["content"]

        # اطلاعات کاربری که روی او ریپلای شده باید در کانتکست موجود باشد
        self.assertIn("اطلاعات کاربری که پیام روی او ریپلای شده است", system_content)
        self.assertIn("@target_member", system_content)
        self.assertIn(str(target_user_id), system_content)
        self.assertIn("سلام من عضو جدید هستم", system_content)

    def test_11_unauthorized_user_replying_to_another_member_stays_silent(self):
        unauth_user = 333
        other_user = 444
        replied_msg = FakeReplyMessage(sender_id=other_user, text="سلام", msg_id=50)

        query_ev = FakeEvent(
            "ai سلام چطوری؟",
            sender_id=unauth_user,
            chat_id=self.group_id,
            reply_to=replied_msg,
            msg_id=51,
        )

        self.client.clear_requests()
        run(self.core.on_new_message(self.client, query_ev))
        # نباید هیچ پیامی (حتی خطای عدم دسترسی) فرستاده شود
        self.assertEqual(self.client.text_messages(), [])

    def test_12_authorized_user_replying_without_ai_stays_silent(self):
        target_user_id = 888
        replied_msg = FakeReplyMessage(
            sender_id=target_user_id,
            text="سلام به همه",
            msg_id=123,
        )
        # مالک بدون ذکر ai به این کاربر جواب می‌دهد (چت عادی)
        normal_reply_ev = FakeEvent(
            "سلام، خوش آمدید به گروه",
            sender_id=self.owner_id,
            chat_id=self.group_id,
            reply_to=replied_msg,
            msg_id=125,
        )
        self.client.clear_requests()
        run(self.core.on_new_message(self.client, normal_reply_ev))
        # هوش مصنوعی نباید در چت معمولی بین افراد گروه دخالت کند
        self.assertEqual(self.client.text_messages(), [])

    def test_13_system_prompt_includes_fonts_prompts_and_group_advice(self):
        import brand
        prompt = brand.AI_SYSTEM_PROMPT
        self.assertIn("فونت", prompt)
        self.assertIn("پرامپت", prompt)
        self.assertIn("عکس", prompt)
        self.assertIn("بهبود گروه", prompt)
        self.assertIn("Display Name", prompt)
        self.assertIn("تحلیل", prompt)
        self.assertIn("کدنویسی", prompt)

    def test_14_model_profiles_have_high_token_limits(self):
        from models import MODEL_PROFILES
        for pid, p in MODEL_PROFILES.items():
            self.assertGreaterEqual(p.max_output_tokens, 2048, f"مدل {pid} سقف توکن خروجی پایینی دارد")

    def test_15_ai_query_cleans_ai_prefix_and_reads_display_name(self):
        target_user_id = 999
        target_sender = FakeSenderUser(target_user_id, username=None, first_name="محسن", last_name="رضایی")
        replied_msg = FakeReplyMessage(
            sender_id=target_user_id,
            text="سلام به همه دوستان",
            msg_id=130,
            sender=target_sender,
        )
        query_ev = FakeEvent(
            "ai این کاربر چرا ساکته",
            sender_id=self.owner_id,
            chat_id=self.group_id,
            reply_to=replied_msg,
            msg_id=131,
        )
        self.client.clear_requests()
        run(self.core.on_new_message(self.client, query_ev))

        self.assertGreater(len(self.ai_client.calls), 0)
        messages = self.ai_client.calls[-1]
        system_content = messages[0]["content"]
        user_prompt = messages[-1]["content"]

        # متن پرسش باید پاکسازی شده و پیشوند ai برداشته شده باشد
        self.assertEqual(user_prompt, "این کاربر چرا ساکته")
        # نام نمایشی کاربر در کانتکست سیستم وجود داشته باشد
        self.assertIn("محسن رضایی", system_content)

    def test_16_help_command_new_filter_items_and_blockquotes(self):
        import brand
        text, entities = brand.build_help_message()

        # بررسی وجود بخش‌های جدید فیلتر
        self.assertIn("برای فیلتر کردن کلمات تبلیغاتی:", text)
        self.assertIn("Flter بعد پیام رو بنویسید\nFlter بیو چک", text)
        self.assertIn("برای دیدن لیست فیلتر ها:", text)
        self.assertIn("list flter", text)
        self.assertIn("برای برداشتن جمله از فیلتر ها:", text)
        self.assertIn("x بعد جمله رو بنویسید\nx بیوچک", text)

        # استخراج متن داخل تمام انتیتی‌های blockquote
        blockquotes = [e for e in entities if isinstance(e, MessageEntityBlockquote)]
        bq_texts = [
            text.encode("utf-16-le")[e.offset * 2 : (e.offset + e.length) * 2].decode("utf-16-le")
            for e in blockquotes
        ]

        # بررسی اینکه Flter و x به صورت کامل با هم در یک نقل‌قول هستند
        self.assertIn("Flter بعد پیام رو بنویسید\nFlter بیو چک", bq_texts)
        self.assertIn("x بعد جمله رو بنویسید\nx بیوچک", bq_texts)
        self.assertIn("list flter", bq_texts)

    def test_17_ai_find_user_in_group(self):
        # ثبت مشخصات کاربر با نام «گلناز» در اعضای گروه
        golnaz_id = 778899
        self.ai.record_group_member(self.group_id, golnaz_id, "گلناز محمدی", username="golnaz_m")
        self.ai.set_group_metadata(self.group_id, "گروه تست", "مالک", ["ادمین"])

        # کاربر مجاز سوال می‌پرسد که آیا کاربری به نام گلناز در گروه هست
        ask_ev = FakeEvent(
            "ai یه کاربر با اسم گلناز هست تو گروه پیداش کن",
            sender_id=self.owner_id,
            chat_id=self.group_id,
            msg_id=140,
            reply_to=FakeReplyMessage(sender_id=self.client.me_id, msg_id=139, out=True),
        )

        self.client.clear_requests()
        run(self.core.on_new_message(self.client, ask_ev))

        self.assertGreater(len(self.ai_client.calls), 0)
        messages = self.ai_client.calls[-1]
        system_content = messages[0]["content"]

        # فهرست اعضا و مشخصات کاربر باید در کانتکست موجود باشد
        self.assertIn("فهرست اعضا و کاربران شناخته‌شده در گروه", system_content)
        self.assertIn("گلناز محمدی", system_content)
        self.assertIn("@golnaz_m", system_content)
        self.assertIn(str(golnaz_id), system_content)
