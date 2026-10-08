"""
تست‌های سیستم هوش مصنوعی گروه‌ها (فقط GROUP).

پوشش ۱۷ سناریوی درخواستی:
  ۱) ai online توسط مالک → فعال شدن AI همان گروه
  ۲) ai online توسط کاربر معمولی → هیچ تغییری
  ۳) ai of توسط مالک → غیرفعال شدن
  ۴) ai list با Reply → مجاز شدن همان user_id
  ۵) ai list بدون Reply → مجاز نشود
  ۶) ai list توسط کاربر معمولی → مجاز نشود
  ۷) ai list x با Reply → حذف مجوز
  ۸) کاربر غیرمجاز + Reply → پیام عدم دسترسی و بدون فراخوانی API
  ۹) کاربر مجاز + Reply → فراخوانی API و ارسال پاسخ
  ۱۰) پیام بدون Reply → بدون فراخوانی API
  ۱۱) PV → هیچ قابلیت AI فعال نشود
  ۱۲) مجوز گروه A روی گروه B اثر نگذارد
  ۱۳) AI خاموش → بدون فراخوانی API
  ۱۴) سهمیه تمام‌شده → پیام سهمیه و بدون درخواست اضافی (داخلی و از سمت Cloudflare)
  ۱۵) پیام بسیار طولانی سهمیه را نامحدود مصرف نکند
  ۱۶) restart → وضعیت per-group از storage از بین نرود
  ۱۷) رگرسیون: همه‌ی تست‌های قبلی پروژه (در فایل‌های دیگر) + بررسی اضافی اینجا
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import brand  # noqa: E402
from ai_client import AIConfigError, AIError, AIQuotaExceeded  # noqa: E402
from ai_service import GroupAI, utc_day, tehran_day  # noqa: E402
from config import Config  # noqa: E402
from core import BotCore  # noqa: E402
from sender import BrandSender  # noqa: E402
from storage import OwnerStore  # noqa: E402
from splusthon import functions, types  # noqa: E402
from tests.fakes import FakeAI, FakeClient, FakeEvent, FakeReplyMessage  # noqa: E402

GROUP_A, GROUP_B = -1001, -2002
OWNER, USER_1, USER_2 = 111, 501, 502


def run(coro):
    return asyncio.run(coro)


class AITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "state.sqlite3"
        self.cfg = Config()
        self.store = OwnerStore(self.db)
        self.client = FakeClient()
        self.ai_client = FakeAI(reply="سلام! این پاسخ تستی است.")
        self.sender = BrandSender(self.cfg)
        self.ai = GroupAI(self.cfg, self.store, self.sender, self.ai_client)
        self.core = BotCore(self.cfg, self.store, self.sender, ai=self.ai)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    # ------------------------------------------------------------- ابزارها
    def make_owner(self, user_id=OWNER):
        self.store.claim(user_id, chat_id=GROUP_A, message_id=1, display_name="مالک")

    async def send(
        self, text, *, user_id, chat_id=GROUP_A, is_group=True, out=False,
        reply_to=None, username=None, display_name=None,
    ):
        event = FakeEvent(
            text,
            user_id=user_id,
            chat_id=chat_id,
            is_group=is_group,
            out=out,
            reply_to=reply_to,
            username=username,
            display_name=display_name,
        )
        await self.core.on_new_message(self.client, event)

    async def pv(self, text, *, user_id, **kw):
        await self.send(text, user_id=user_id, chat_id=user_id, is_group=False, **kw)

    def texts(self):
        return self.client.text_messages()

    def ai_calls(self):
        return len(self.ai_client.calls)

    def reply_from(self, user_id, *, username=None, display_name="کاربر", msg_id=77, text="", out=False):
        return FakeReplyMessage(sender_id=user_id, username=username,
                                display_name=display_name, msg_id=msg_id, text=text,
                                out=out)

    def bot_reply(self, *, msg_id=77, text="..."):
        """پیام مرجع متعلق به خود ربات (reply ای که AI باید به آن پاسخ بدهد)."""
        return FakeReplyMessage(
            sender_id=self.client.me_id,
            display_name="acod",
            msg_id=msg_id,
            text=text,
            out=True,
        )


class TestToggle(AITestCase):
    def test_1_owner_enables_ai_for_group(self):
        self.make_owner()
        self.assertFalse(self.store.ai_is_enabled(GROUP_A))

        run(self.send("ai online", user_id=OWNER))

        self.assertTrue(self.store.ai_is_enabled(GROUP_A))
        self.assertEqual(self.texts(), [brand.AI_ENABLED_TEXT])

    def test_2_non_owner_cannot_enable(self):
        self.make_owner()
        run(self.send("ai online", user_id=USER_1))

        self.assertFalse(self.store.ai_is_enabled(GROUP_A), "غیرمالک نباید AI را روشن کند")
        self.assertEqual(self.client.requests, [], "هیچ پیامی نباید ارسال شود")

    def test_3_owner_disables_ai(self):
        self.make_owner()
        run(self.send("ai online", user_id=OWNER))
        self.client.clear_requests()

        run(self.send("ai of", user_id=OWNER))
        self.assertFalse(self.store.ai_is_enabled(GROUP_A))
        self.assertEqual(self.texts(), [brand.AI_DISABLED_TEXT])

    def test_3b_non_owner_cannot_disable(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        run(self.send("ai of", user_id=USER_2))

        self.assertTrue(self.store.ai_is_enabled(GROUP_A), "غیرمالک نباید AI را خاموش کند")
        self.assertEqual(self.client.requests, [])

    def test_3c_toggle_variants_are_recognized(self):
        self.make_owner()
        run(self.send("AI  ONLINE", user_id=OWNER))
        self.assertTrue(self.store.ai_is_enabled(GROUP_A))
        run(self.send(" ai   of ", user_id=OWNER))
        self.assertFalse(self.store.ai_is_enabled(GROUP_A))


class TestPermissions(AITestCase):
    def test_4_owner_allows_user_by_reply(self):
        self.make_owner()
        run(self.send("ai list", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username="osine")))

        self.assertTrue(self.store.ai_is_allowed(GROUP_A, USER_1))
        self.assertEqual(self.texts(), [brand.AI_ALLOWED_TEXT.format(user="@osine")])

    def test_5_without_reply_nothing_is_allowed(self):
        self.make_owner()
        run(self.send("ai list", user_id=OWNER))

        self.assertEqual(self.store.ai_allowed_users(GROUP_A), [])
        self.assertEqual(self.texts(), [brand.AI_NEED_REPLY_TEXT])

    def test_6_non_owner_cannot_allow_anyone(self):
        self.make_owner()
        # کاربر معمولی حتی با Reply روی پیام خودش هم نباید مجاز شود
        run(self.send("ai list", user_id=USER_2,
                      reply_to=self.reply_from(USER_2, username="elism")))

        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_2))
        self.assertEqual(self.client.requests, [])

    def test_7_owner_revokes_permission(self):
        self.make_owner()
        run(self.send("ai list", user_id=OWNER, reply_to=self.reply_from(USER_1, username="osine")))
        self.assertTrue(self.store.ai_is_allowed(GROUP_A, USER_1))
        self.client.clear_requests()

        run(self.send("ai list x", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username="osine")))

        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_1))
        self.assertEqual(self.texts(), [brand.AI_REVOKED_TEXT.format(user="@osine")])

    def test_7b_revoke_when_user_was_not_allowed_sends_same_of_template(self):
        """برای «ai list x» فقط همان پیام حالت مربوطه ارسال می‌شود (بدون متن اضافه)."""
        self.make_owner()
        run(self.send("ai list x", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username="osine")))
        self.assertEqual(self.texts(), [brand.AI_REVOKED_TEXT.format(user="@osine")])
        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_1))

    def test_12b_permission_is_by_user_id_not_username(self):
        self.make_owner()
        run(self.send("ai list", user_id=OWNER, reply_to=self.reply_from(USER_1, username="osine")))
        # همان user_id با username متفاوت → همچنان مجاز
        self.assertTrue(self.store.ai_is_allowed(GROUP_A, USER_1))
        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_2))


class TestChat(AITestCase):
    def setUp(self):
        super().setUp()
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)

    def test_8_unauthorized_user_gets_denial_without_api_call(self):
        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))

        self.assertEqual(self.ai_calls(), 0, "برای کاربر غیرمجاز نباید API صدا زده شود")
        self.assertEqual(self.texts(), [brand.AI_DENIED_TEXT])

    def test_9_authorized_user_gets_ai_reply(self):
        self.store.ai_allow_user(GROUP_A, USER_1, username="osine")
        run(self.send("هوای تهران چطوره؟", user_id=USER_1,
                      reply_to=self.bot_reply(msg_id=77)))

        self.assertEqual(self.ai_calls(), 1)
        self.assertEqual(self.texts(), [self.ai_client.reply])
        # پاسخ باید Reply روی همان پیام کاربر باشد و در همان گروه
        req = self.client.requests[-1]
        self.assertEqual(req.peer.chat_id, abs(GROUP_A))
        self.assertIsInstance(req.reply_to, types.InputReplyToMessage)
        self.assertEqual(req.reply_to.reply_to_msg_id, 1)   # id پیام کاربر در FakeEvent

    def test_9b_owner_can_talk_to_ai(self):
        run(self.send("سلام", user_id=OWNER, reply_to=self.bot_reply()))
        self.assertEqual(self.ai_calls(), 1)
        self.assertEqual(self.texts(), [self.ai_client.reply])

    def test_10_message_without_reply_never_calls_api(self):
        self.store.ai_allow_user(GROUP_A, USER_1)
        run(self.send("سلام بدون ریپلای", user_id=USER_1))

        self.assertEqual(self.ai_calls(), 0)
        self.assertEqual(self.client.requests, [])

    def test_13_disabled_ai_never_calls_api(self):
        self.store.ai_set_enabled(GROUP_A, False)
        self.store.ai_allow_user(GROUP_A, USER_1)
        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))

        self.assertEqual(self.ai_calls(), 0)
        self.assertEqual(self.client.requests, [])

    def test_13b_media_or_empty_text_is_ignored(self):
        self.store.ai_allow_user(GROUP_A, USER_1)
        run(self.send("   ", user_id=USER_1, reply_to=self.bot_reply()))

        self.assertEqual(self.ai_calls(), 0)
        self.assertEqual(self.client.requests, [])


class TestTypingIndicator(AITestCase):
    """تست‌های وضعیت «در حال نوشتن…» برای مسیر AI.

    این تست‌ها FakeClient استفاده می‌کنند؛ هیچ تماس شبکه‌ای انجام نمی‌شود.
    """

    def setUp(self):
        super().setUp()
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_allow_user(GROUP_A, USER_1, username="user1")

    async def _ask(self, text="سلام", *, client=None, user_id=USER_1, chat_id=GROUP_A):
        c = client or self.client
        event = FakeEvent(text, user_id=user_id, chat_id=chat_id, is_group=True,
                          reply_to=FakeReplyMessage(sender_id=user_id, msg_id=99))
        # از core.on_new_message رد نمی‌کنیم تا admin/kodrez paths تداخل نداشته باشد
        await self.ai.handle_chat(c, event)

    def test_16_typing_starts_before_ai_request_and_stops_after_reply(self):
        """قبل از درخواست AI باید TypingAction، و در پایان CancelAction ارسال شده باشد."""

        class SlowFakeAI:
            configured = True

            async def chat(self_inner, messages, *, max_tokens=None, **_kw):
                await asyncio.sleep(0)  # yield به event loop تا _run typing یک دور بزند
                from ai_client import AIResponse
                return AIResponse(text="پاسخ")

            async def close(self_inner):
                pass

        core = BotCore(self.cfg, self.store, self.sender, ai=GroupAI(
            self.cfg, self.store, self.sender, SlowFakeAI()))

        client = FakeClient()
        run(core.on_new_message(client, FakeEvent(
            "سلام", user_id=USER_1, chat_id=GROUP_A, is_group=True,
            reply_to=FakeReplyMessage(sender_id=USER_1, msg_id=7))))

        actions = client.typing_actions()
        self.assertTrue(actions, "باید حداقل یک TypingAction ارسال شده باشد")
        self.assertIn("SendMessageTypingAction", actions,
                       "باید TypingAction قبل/حین درخواست ارسال شده باشد")
        self.assertEqual(actions[-1], "SendMessageCancelAction",
                         "پس از پایان باید typing با CancelAction متوقف شود")
        # typing فقط برای همان گروه (GROUP_A = -1001 → InputPeerChat chat_id=1001)
        peers = client.typing_peer_ids()
        self.assertTrue(peers, "باید peer مشخص شده باشد")
        self.assertTrue(all(pid == 1001 for pid in peers),
                        "typing فقط باید در همان چت ارسال شود (نه چت‌های دیگر)")

    def test_16b_typing_stops_on_ai_error(self):
        """اگر Cloudflare خطا بدهد، typing نباید باقی بماند."""
        from ai_client import AIError

        class ErrorFakeAI:
            configured = True
            async def chat(self_inner, messages, *, max_tokens=None, **_kw):
                await asyncio.sleep(0)
                raise AIError("شکست ساختگی تست")

            async def close(self_inner):
                pass

        ai_service = GroupAI(self.cfg, self.store, self.sender, ErrorFakeAI())
        core = BotCore(self.cfg, self.store, self.sender, ai=ai_service)

        run(core.on_new_message(self.client, FakeEvent(
            "سلام", user_id=USER_1, chat_id=GROUP_A, is_group=True,
            reply_to=FakeReplyMessage(sender_id=USER_1, msg_id=7))))

        self.assertEqual(self.client.typing_actions()[-1], "SendMessageCancelAction",
                         "حتی در صورت خطا باید typing با CancelAction خاتمه یابد")

    def test_16c_typing_stops_on_quota_error(self):
        """در صورت اتمام سهمیه Cloudflare هم typing باید متوقف شود."""
        from ai_client import AIQuotaExceeded

        class QuotaFakeAI:
            configured = True
            async def chat(self_inner, messages, *, max_tokens=None, **_kw):
                await asyncio.sleep(0)
                raise AIQuotaExceeded("quota fake")

        ai_service = GroupAI(self.cfg, self.store, self.sender, QuotaFakeAI())
        core = BotCore(self.cfg, self.store, self.sender, ai=ai_service)

        run(core.on_new_message(self.client, FakeEvent(
            "سلام", user_id=USER_1, chat_id=GROUP_A, is_group=True,
            reply_to=FakeReplyMessage(sender_id=USER_1, msg_id=7))))

        self.assertEqual(self.client.typing_actions()[-1], "SendMessageCancelAction")

    def test_16d_non_ai_commands_do_not_send_typing(self):
        """دستورهای «ai online» / «ai of» / «ai list» / «کدرز» / مالک نباید typing بفرستند."""
        run(self.send("ai online", user_id=OWNER))
        run(self.send("کدرز", user_id=OWNER))
        # اطمینان: در این مسیرها هیچ SetTypingRequest ای ارسال نشده
        self.assertEqual(self.client.typing_requests(), [],
                         "دستورهای غیر-AI نباید وضعیت typing را فعال کنند")

    def test_16e_typing_persists_across_retry_and_ends_with_cancel(self):
        """اگر کل عملیات AI کمی طول بکشد، typing فعال می‌ماند و در پایان با
        CancelAction خاتمه می‌پذیرد. (بازه‌ی refresh هر ۴.۵ ثانیه است؛ در تست
        ۰.۱ ثانیه sleep حداقل یک TypingAction و در پایان CancelAction کافی است.)
        """
        class LongAI:
            configured = True

            async def chat(self_inner, messages, *, max_tokens=None, **_kw):
                await asyncio.sleep(0.05)
                from ai_client import AIResponse
                return AIResponse(text="پاسخ طولانی")

            async def close(self_inner):
                pass

        ai_service = GroupAI(self.cfg, self.store, self.sender, LongAI())
        core = BotCore(self.cfg, self.store, self.sender, ai=ai_service)

        client = FakeClient()
        run(core.on_new_message(client, FakeEvent(
            "یک سوال طولانی", user_id=USER_1, chat_id=GROUP_A, is_group=True,
            reply_to=FakeReplyMessage(sender_id=USER_1, msg_id=7))))

        actions = client.typing_actions()
        self.assertIn("SendMessageTypingAction", actions,
                      "باید حداقل یک TypingAction در طول عملیات ارسال شده باشد")
        self.assertEqual(actions[-1], "SendMessageCancelAction",
                         "پس از پایان پاسخ باید typing با CancelAction خاتمه یابد")


class TestQuota(AITestCase):
    def setUp(self):
        super().setUp()
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_allow_user(GROUP_A, USER_1, username="osine")

    def test_14_internal_quota_exhaustion_blocks_further_requests(self):
        cfg_small = Config.from_env()
        # سهمیه‌ی کوچک: ۲ درخواست در روز برای این گروه
        object.__setattr__(cfg_small, "ai_daily_quota", 2)
        ai = GroupAI(cfg_small, self.store, self.sender, self.ai_client)
        core = BotCore(cfg_small, self.store, self.sender, ai=ai)

        async def ask(text):
            event = FakeEvent(text, user_id=USER_1, chat_id=GROUP_A, is_group=True,
                              reply_to=self.bot_reply())
            await core.on_new_message(self.client, event)

        run(ask("یک"))
        run(ask("دو"))
        self.assertEqual(self.ai_calls(), 2)
        self.client.clear_requests()

        run(ask("سه"))          # سهمیه تمام شده
        self.assertEqual(self.ai_calls(), 2, "بعد از پایان سهمیه نباید درخواست برود")
        self.assertEqual(self.texts(), [brand.AI_QUOTA_TEXT])

        run(ask("چهار"))
        self.assertEqual(self.ai_calls(), 2)
        self.assertEqual(self.texts(), [brand.AI_QUOTA_TEXT, brand.AI_QUOTA_TEXT])

    def test_14b_cloudflare_quota_error_marks_day_as_exhausted(self):
        self.ai_client.error = AIQuotaExceeded("HTTP 429 | daily free allocation of 10,000 neurons")

        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))

        self.assertEqual(self.texts(), [brand.AI_QUOTA_TEXT])
        self.assertEqual(self.store.ai_usage(GROUP_A, tehran_day()), self.cfg.ai_daily_quota)

        self.client.clear_requests()
        run(self.send("سلام دوباره", user_id=USER_1, reply_to=self.bot_reply()))
        self.assertEqual(self.texts(), [brand.AI_QUOTA_TEXT])   # دیگر درخواست نمی‌رود
        self.assertEqual(self.ai_calls(), 1)

    def test_14c_config_error_is_reported(self):
        self.ai_client.error = AIConfigError("token نیست")
        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))
        self.assertEqual(self.texts(), [brand.AI_CONFIG_ERROR_TEXT])

    def test_15_long_message_is_truncated_per_request(self):
        long_text = "س" * 10_000
        run(self.send(long_text, user_id=USER_1, reply_to=self.bot_reply()))

        self.assertEqual(self.ai_calls(), 1, "یک پیام بلند باید فقط یک درخواست بسازد")
        sent = self.ai_client.calls[0]
        user_message = [m for m in sent if m["role"] == "user"][-1]
        self.assertLessEqual(len(user_message["content"]), self.cfg.ai_max_input_chars)
        self.assertEqual(self.store.ai_usage(GROUP_A, tehran_day()), 1)

    def test_15b_history_is_bounded(self):
        for i in range(6):
            run(self.send(f"پیام {i}", user_id=USER_1, reply_to=self.bot_reply()))

        last_call = self.ai_client.calls[-1]
        # system + حداکثر (ai_history_pairs * 2) پیام
        self.assertLessEqual(
            len(last_call), 1 + self.cfg.ai_history_pairs * 2 + 1
        )
        self.assertTrue(self.store.ai_is_enabled(GROUP_A))


class TestDailyQuotaValue(AITestCase):
    """سهمیه‌ی پیش‌فرض هر گروه: ۵۰۰۰ درخواست در روز (به‌تفکیک روز UTC و مستقل per-group)."""

    def test_default_quota_is_5000_per_group(self):
        self.assertEqual(Config().ai_daily_quota, 5000)
        self.assertEqual(Config.from_env().ai_daily_quota, 5000)

    def test_group_can_use_exactly_5000_requests_in_a_day(self):
        day = tehran_day()
        granted = 0
        while self.store.ai_consume_quota(GROUP_A, day, self.cfg.ai_daily_quota):
            granted += 1
            if granted > 6000:          # محافظ تست: از حلقه‌ی بی‌نهایت جلوگیری می‌کند
                break

        self.assertEqual(granted, 5000, "هر گروه باید روزانه دقیقاً ۵۰۰۰ درخواست مجاز داشته باشد")
        self.assertEqual(self.store.ai_usage(GROUP_A, day), 5000)
        # درخواست ۵۰۰۰اُم به بعد بسته است
        self.assertFalse(self.store.ai_consume_quota(GROUP_A, day, self.cfg.ai_daily_quota))
        self.assertEqual(self.store.ai_usage(GROUP_A, day), 5000)

    def test_5000_quota_is_per_group_and_per_day(self):
        day_a, day_b = tehran_day(), "2026-10-10"
        for _ in range(5000):
            self.assertTrue(self.store.ai_consume_quota(GROUP_A, day_a, 5000))
        self.assertFalse(self.store.ai_consume_quota(GROUP_A, day_a, 5000))   # روز A تمام است

        self.assertTrue(self.store.ai_consume_quota(GROUP_A, day_b, 5000))    # روز بعد آزاد است
        self.assertTrue(self.store.ai_consume_quota(GROUP_B, day_a, 5000))    # گروه دیگر مستقل است
        self.assertEqual(self.store.ai_usage(GROUP_B, day_a), 1)

    def test_whole_chat_flow_works_at_boundary_of_daily_quota(self):
        """جریان واقعی چت: تا ۵۰۰۰ پاسخ می‌گیرد و بعد از آن پیام دقیق سهمیه."""
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_allow_user(GROUP_A, USER_1)

        async def ask(text="سلام"):
            event = FakeEvent(text, user_id=USER_1, chat_id=GROUP_A, is_group=True,
                              reply_to=self.bot_reply())
            await self.core.on_new_message(self.client, event)

        # پر کردن سهمیه‌ی امروز با مصرف مستقیم (سریع‌تر از ۵۰۰۰ درخواست شبکه‌ای)
        day = tehran_day()
        for _ in range(5000):
            self.store.ai_consume_quota(GROUP_A, day, 5000)

        run(ask())
        self.assertEqual(self.ai_calls(), 0, "بعد از ۵۰۰۰ درخواست، دیگر به API نمی‌رود")
        self.assertEqual(self.texts(), [brand.AI_QUOTA_TEXT])

        # گروه دیگر همان روز همچنان کار می‌کند
        self.store.ai_set_enabled(GROUP_B, True)
        self.store.ai_allow_user(GROUP_B, USER_1)
        run(self.send("سلام", user_id=USER_1, chat_id=GROUP_B, reply_to=self.bot_reply()))
        self.assertEqual(self.ai_calls(), 1)


class TestExactSystemTemplates(AITestCase):
    """مقایسه‌ی خروجی با «متن دقیق قالب‌ها» (کپی مستقل، بدون استفاده از ثابت‌های brand).

    نکات دقیقِ قالب که این تست‌ها نگه می‌دارند:
      * online: «{ 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅}» بدون فاصله قبل از } — offline: «{ 𝗮𝗰𝗼𝗱 𝗳𝗼𝗫 }» با فاصله
      * دو فاصله بین «𝗔𝗜» و «𝗨𝗭𝗘𝗥» در قالب حذف دسترسی
      * 𝖢𝖮︎𝖣︎𝖤︎𝖱︎ با Variation Selector، دو فاصله تا 𝖠︎𝖨︎، و «بایدمالک» سرِهم
    """

    EXACT_ONLINE = "֍ 𝗢𝗡𝗟𝗜𝗡𝗘 { 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅} 🏕"
    EXACT_OFFLINE = "֎ 𝗢𝗙𝗙𝗟𝗜𝗡𝗘 { 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅 } 🏜"
    EXACT_ALLOWED = "☰ 𝗔𝗜 𝗨𝗭𝗘𝗥 : 「 {user} 」\n๏ 𝗳𝗼𝘅 𝗮𝗶 𝗰𝗼𝗱𝗲 🍂"
    EXACT_REVOKED = "☰ 𝗢𝗙 𝗔𝗜  𝗨𝗭𝗘𝗥 : 「 {user} 」\n๏ 𝗳𝗼𝘅 𝗮𝗶 𝗰𝗼𝗱𝗲 🪴"
    EXACT_DENIED = ("شما مجاز به صحبت کردن با هوش مصنوعی "
                    "𝖢𝖮︎𝖣︎𝖤︎𝖱︎  𝖠︎𝖨︎ نیستید برای صحبت بایدمالک به شما دسترسی بدهد 🦦🎊")

    def setUp(self):
        super().setUp()
        self.make_owner()

    # ---------------------------------------------------------------- online/of
    def test_online_message_is_exactly_the_requested_template(self):
        run(self.send("ai online", user_id=OWNER))
        self.assertEqual(self.texts(), [self.EXACT_ONLINE])
        self.assertEqual(self.client.requests[-1].message, "֍ 𝗢𝗡𝗟𝗜𝗡𝗘 { 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅} 🏕")
        self.assertTrue(self.store.ai_is_enabled(GROUP_A))

    def test_offline_message_is_exactly_the_requested_template(self):
        self.store.ai_set_enabled(GROUP_A, True)
        run(self.send("ai of", user_id=OWNER))
        self.assertEqual(self.texts(), [self.EXACT_OFFLINE])
        self.assertFalse(self.store.ai_is_enabled(GROUP_A))

    # -------------------------------------------------------------- ai list
    def test_allow_message_is_exactly_the_requested_template_with_username(self):
        run(self.send("ai list", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username="ali")))
        expected = self.EXACT_ALLOWED.format(user="@ali")
        self.assertEqual(self.client.text_messages(), [expected])
        self.assertEqual(self.client.requests[-1].message,
                         "☰ 𝗔𝗜 𝗨𝗭𝗘𝗥 : 「 @ali 」\n๏ 𝗳𝗼𝘅 𝗮𝗶 𝗰𝗼𝗱𝗲 🍂")
        self.assertTrue(self.store.ai_is_allowed(GROUP_A, USER_1))

    def test_allow_message_uses_display_name_when_no_username(self):
        run(self.send("ai list", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username=None,
                                               display_name="علی رضایی")))
        self.assertEqual(self.texts(), [self.EXACT_ALLOWED.format(user="علی رضایی")])

    def test_allow_message_uses_safe_fallback_from_real_user_id(self):
        run(self.send("ai list", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username=None, display_name="")))
        self.assertEqual(self.texts(),
                         [self.EXACT_ALLOWED.format(user=f"کاربر {USER_1}")])

    # ------------------------------------------------------------ ai list x
    def test_revoke_message_is_exactly_the_requested_template(self):
        run(self.send("ai list", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username="ali")))
        self.client.clear_requests()

        run(self.send("ai list x", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username="ali")))
        expected = self.EXACT_REVOKED.format(user="@ali")
        self.assertEqual(self.client.text_messages(), [expected])
        self.assertEqual(self.client.requests[-1].message,
                         "☰ 𝗢𝗙 𝗔𝗜  𝗨𝗭𝗘𝗥 : 「 @ali 」\n๏ 𝗳𝗼𝘅 𝗮𝗶 𝗰𝗼𝗱𝗲 🪴")
        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_1))

    # ------------------------------------------------------------ غیرمجاز
    def test_denied_message_is_exactly_the_requested_template_and_no_api_call(self):
        self.store.ai_set_enabled(GROUP_A, True)
        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))

        self.assertEqual(self.texts(), [self.EXACT_DENIED])
        self.assertEqual(self.ai_calls(), 0, "برای کاربر غیرمجاز نباید API صدا زده شود")
        self.assertIn("𝖢𝖮︎𝖣︎𝖤︎𝖱︎  𝖠︎𝖨︎", self.texts()[0])    # دو فاصله + Variation Selector
        self.assertIn("بایدمالک", self.texts()[0])
        self.assertNotIn("باید مالک", self.texts()[0])

    # ------------------------------------------------- جزئیات دقیق کاراکترها
    def test_double_space_between_of_ai_and_user_is_preserved(self):
        self.assertIn("𝗢𝗙 𝗔𝗜  𝗨𝗭𝗘𝗥", self.EXACT_REVOKED)     # دو فاصله عمدی
        self.assertNotIn("𝗢𝗙 𝗔𝗜 𝗨𝗭𝗘𝗥", self.EXACT_REVOKED)

    def test_online_has_no_space_before_closing_brace_but_offline_has(self):
        self.assertIn("{ 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅}", self.EXACT_ONLINE)
        self.assertNotIn("{ 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅 }", self.EXACT_ONLINE)
        self.assertIn("{ 𝗮𝗰𝗼𝗱 𝗳𝗼𝘅 }", self.EXACT_OFFLINE)

    def test_templates_in_tests_match_brand_constants_exactly(self):
        """این تست تضمین می‌کند کپی‌های مستقلِ داخل تست با ثابت‌های پروژه یکی‌اند."""
        self.assertEqual(brand.AI_ENABLED_TEXT, self.EXACT_ONLINE)
        self.assertEqual(brand.AI_DISABLED_TEXT, self.EXACT_OFFLINE)
        self.assertEqual(brand.AI_ALLOWED_TEXT, self.EXACT_ALLOWED)
        self.assertEqual(brand.AI_REVOKED_TEXT, self.EXACT_REVOKED)
        self.assertEqual(brand.AI_DENIED_TEXT, self.EXACT_DENIED)

    def test_braces_and_curly_quotes_and_emojis_are_exact(self):
        self.assertTrue(self.EXACT_ONLINE.endswith("🏕"))
        self.assertTrue(self.EXACT_OFFLINE.endswith("🏜"))
        self.assertTrue(self.EXACT_ALLOWED.startswith("☰ 𝗔𝗜 𝗨𝗭𝗘𝗥 : 「 "))
        self.assertTrue(self.EXACT_ALLOWED.endswith("🍂"))
        self.assertTrue(self.EXACT_REVOKED.startswith("☰ 𝗢𝗙 𝗔𝗜  𝗨𝗭𝗘𝗥 : 「 "))
        self.assertTrue(self.EXACT_REVOKED.endswith("🪴"))
        self.assertTrue(self.EXACT_ONLINE.startswith("֍"))
        self.assertTrue(self.EXACT_OFFLINE.startswith("֎"))

    # ------------------------------------------------------ قالب‌بندی پیام‌ها
    def test_ai_system_messages_are_bold_without_glass_quote(self):
        """طبق درخواست صریح کاربر: Bold هست، ولی «نقل‌قول شیشه‌ای» (Blockquote) نیست."""
        scenarios = [
            ("ai online", OWNER, None),
            ("ai of", OWNER, None),
            ("ai list", OWNER, self.reply_from(USER_1, username="ali")),
            ("ai list x", OWNER, self.reply_from(USER_1, username="ali")),
        ]
        self.store.ai_set_enabled(GROUP_A, True)
        for text, user, reply in scenarios:
            with self.subTest(command=text):
                self.client.clear_requests()
                run(self.send(text, user_id=user, reply_to=reply))
                msg = self.client.requests[-1]
                kinds = [type(e).__name__ for e in (msg.entities or [])]
                self.assertIn("MessageEntityBold", kinds, f"Bold برای {text}")
                self.assertNotIn(
                    "MessageEntityBlockquote", kinds,
                    f"«{text}» نباید داخل نقل‌قول شیشه‌ای باشد",
                )
                # هر خط غیرخالی یک Bold مستقل دارد و آفست‌ها UTF-16 هستند
                bolds = [e for e in msg.entities if type(e).__name__ == "MessageEntityBold"]
                self.assertEqual(len(bolds), len([l for l in msg.message.split("\n") if l.strip()]))
                self.assertEqual(bolds[0].offset, 0)

    def test_no_glass_quote_in_any_ai_system_message(self):
        """هیچ‌کدام از پیام‌های سیستمی AI نباید Blockquote داشته باشند."""
        self.store.ai_set_enabled(GROUP_A, True)
        run(self.send("ai list", user_id=OWNER, reply_to=self.reply_from(USER_1, username="ali")))
        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))     # عدم دسترسی
        run(self.send("ai of", user_id=OWNER))
        run(self.send("ai list", user_id=OWNER))                                     # بدون Reply

        self.assertTrue(self.client.requests)
        for msg in self.client.requests:
            kinds = [type(e).__name__ for e in (msg.entities or [])]
            self.assertNotIn("MessageEntityBlockquote", kinds, msg.message)

    def test_denied_message_has_bold_without_glass_quote(self):
        self.store.ai_set_enabled(GROUP_A, True)
        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))
        kinds = [type(e).__name__ for e in (self.client.requests[-1].entities or [])]
        self.assertEqual(kinds.count("MessageEntityBold"), 1)
        self.assertNotIn("MessageEntityBlockquote", kinds)

    def test_username_has_spaces_inside_the_brackets(self):
        """قالب درخواستی: 「 @Aifox 」 — با فاصله در دو طرف نام (نه چسبیده)."""
        run(self.send("ai list", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username="Aifox")))
        self.assertEqual(self.client.requests[-1].message,
                         "☰ 𝗔𝗜 𝗨𝗭𝗘𝗥 : 「 @Aifox 」\n๏ 𝗳𝗼𝘅 𝗮𝗶 𝗰𝗼𝗱𝗲 🍂")
        self.assertIn("「 @Aifox 」", self.client.requests[-1].message)

        self.client.clear_requests()
        run(self.send("ai list x", user_id=OWNER,
                      reply_to=self.reply_from(USER_1, username="Aifox")))
        self.assertIn("「 @Aifox 」", self.client.requests[-1].message)

    def test_brackets_never_stick_to_the_name(self):
        for template in (self.EXACT_ALLOWED, self.EXACT_REVOKED):
            self.assertNotIn("「{user}", template)
            self.assertIn("「 {user} 」", template)

    # ------------------------------------------------- فقط پیام همان حالت
    def test_only_the_matching_message_is_sent_for_each_state(self):
        self.store.ai_set_enabled(GROUP_A, True)

        cases = [
            ("ai online", None, self.EXACT_ONLINE),
            ("ai list", self.reply_from(USER_1, username="ali"),
             self.EXACT_ALLOWED.format(user="@ali")),
            ("ai list x", self.reply_from(USER_1, username="ali"),
             self.EXACT_REVOKED.format(user="@ali")),
            ("ai of", None, self.EXACT_OFFLINE),
        ]
        for command, reply, expected in cases:
            with self.subTest(command=command):
                self.client.clear_requests()
                run(self.send(command, user_id=OWNER, reply_to=reply))
                self.assertEqual(len(self.client.requests), 1)
                self.assertEqual(self.client.requests[0].message, expected)

    def test_no_extra_message_is_sent_on_non_matching_events(self):
        """پیام بدون Reply / بدون متن / غیرمجاز → فقط پیام حالت خودش."""
        self.store.ai_set_enabled(GROUP_A, True)
        run(self.send("ai list", user_id=OWNER))            # بدون Reply
        self.assertEqual(self.client.requests[-1].message, brand.AI_NEED_REPLY_TEXT)

        self.client.clear_requests()
        # ریپلای روی پیام یک کاربر دیگر (نه ربات) → نباید هیچ پیامی (حتی عدم دسترسی) بفرستد
        run(self.send("سلام", user_id=USER_2, reply_to=self.reply_from(USER_1)))
        self.assertEqual(self.texts(), [])

        self.client.clear_requests()
        # ریپلای روی پیام ربات ولی کاربر مجاز نیست → باید پیام عدم دسترسی بدهد
        run(self.send("سلام", user_id=USER_2, reply_to=self.bot_reply()))
        self.assertEqual(self.texts(), [self.EXACT_DENIED])   # کاربر مجاز نیست


class TestPerGroupIsolation(AITestCase):
    def test_12_permission_in_group_a_does_not_apply_to_group_b(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_set_enabled(GROUP_B, True)
        self.store.ai_allow_user(GROUP_A, USER_1, username="osine")

        # گروه A → پاسخ می‌گیرد
        run(self.send("سلام", user_id=USER_1, chat_id=GROUP_A,
                      reply_to=self.bot_reply()))
        self.assertEqual(self.texts(), [self.ai_client.reply])
        self.client.clear_requests()

        # گروه B → همان کاربر مجاز نیست
        run(self.send("سلام", user_id=USER_1, chat_id=GROUP_B,
                      reply_to=self.bot_reply()))
        self.assertEqual(self.texts(), [brand.AI_DENIED_TEXT])

    def test_12b_enable_in_group_a_does_not_enable_group_b(self):
        self.make_owner()
        run(self.send("ai online", user_id=OWNER, chat_id=GROUP_A))

        self.assertTrue(self.store.ai_is_enabled(GROUP_A))
        self.assertFalse(self.store.ai_is_enabled(GROUP_B))

    def test_12c_disable_in_group_a_keeps_group_b_enabled(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_set_enabled(GROUP_B, True)

        run(self.send("ai of", user_id=OWNER, chat_id=GROUP_A))

        self.assertFalse(self.store.ai_is_enabled(GROUP_A))
        self.assertTrue(self.store.ai_is_enabled(GROUP_B))

    def test_12d_quota_is_counted_per_group(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_set_enabled(GROUP_B, True)
        self.store.ai_allow_user(GROUP_A, USER_1)
        self.store.ai_allow_user(GROUP_B, USER_1)

        run(self.send("الف", user_id=USER_1, chat_id=GROUP_A, reply_to=self.bot_reply()))
        run(self.send("ب", user_id=USER_1, chat_id=GROUP_B, reply_to=self.bot_reply()))

        day = tehran_day()
        self.assertEqual(self.store.ai_usage(GROUP_A, day), 1)
        self.assertEqual(self.store.ai_usage(GROUP_B, day), 1)


class TestPVIsolation(AITestCase):
    def test_11_pv_never_triggers_ai_features(self):
        self.make_owner()

        for text in ("ai online", "ai of", "ai list", "ai list x"):
            run(self.pv(text, user_id=OWNER))

        self.assertFalse(self.store.ai_is_enabled(GROUP_A))
        self.assertFalse(self.store.ai_is_enabled(OWNER))
        self.assertEqual(self.store.ai_allowed_users(GROUP_A), [])
        self.assertEqual(self.ai_calls(), 0)
        # PV رفتار خودش را دارد: هر کاربر جدید → معرفی + منو
        self.assertEqual(self.texts()[:2], [brand.FULL_TEXT, brand.MENU_TEXT])

    def test_11b_pv_chat_does_not_call_ai_even_with_reply(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_allow_user(GROUP_A, OWNER)

        run(self.pv("سلام", user_id=OWNER, reply_to=self.bot_reply()))

        self.assertEqual(self.ai_calls(), 0, "PV نباید AI را فعال کند")
        self.assertEqual(self.texts(), [brand.FULL_TEXT, brand.MENU_TEXT])   # رفتار PV

    def test_11c_ai_commands_do_not_break_pv_menu(self):
        self.make_owner()
        run(self.pv("سازنده", user_id=USER_1))     # اولین پیام → معرفی + منو
        self.client.clear_requests()

        run(self.pv("سازنده", user_id=USER_1))     # پیام بعدی → پاسخ منو
        self.assertEqual(self.texts(), ["@osine2"])


class TestPersistence(AITestCase):
    def test_16_state_survives_restart(self):
        self.make_owner()
        run(self.send("ai online", user_id=OWNER, chat_id=GROUP_A))
        run(self.send("ai list", user_id=OWNER, chat_id=GROUP_A,
                      reply_to=self.reply_from(USER_1, username="osine")))
        run(self.send("سلام", user_id=USER_1, chat_id=GROUP_A,
                      reply_to=self.bot_reply()))     # یک مصرف سهمیه
        self.store.close()

        store2 = OwnerStore(self.db)                            # ری‌استارت
        try:
            self.assertTrue(store2.ai_is_enabled(GROUP_A))
            self.assertTrue(store2.ai_is_allowed(GROUP_A, USER_1))
            self.assertEqual(store2.ai_usage(GROUP_A, tehran_day()), 1)
            self.assertFalse(store2.ai_is_enabled(GROUP_B))

            ai_client2 = FakeAI(reply="پاسخ بعد از ری‌استارت")
            ai2 = GroupAI(self.cfg, store2, self.sender, ai_client2)
            core2 = BotCore(self.cfg, store2, self.sender, ai=ai2)
            client2 = FakeClient()

            async def scenario():
                event = FakeEvent("سلام بعد از ری‌استارت", user_id=USER_1,
                                  chat_id=GROUP_A, is_group=True,
                                  reply_to=self.bot_reply())
                await core2.on_new_message(client2, event)

            run(scenario())
            self.assertEqual(len(ai_client2.calls), 1, "کاربر مجاز بعد از ری‌استارت هم مجاز است")
            self.assertEqual(client2.text_messages(), ["پاسخ بعد از ری‌استارت"])
        finally:
            store2.close()
            self.store = OwnerStore(self.db)


class TestSafety(AITestCase):
    def test_17_group_commands_unchanged(self):
        # رگرسیون: دستورهای گروهی قبلی با وجود AI سالم بمانند
        self.make_owner()
        run(self.send("کدرز", user_id=USER_2))
        self.assertEqual(self.client.requests[-1].message, brand.FULL_TEXT)

        self.client.clear_requests()
        run(self.send("ai cod", user_id=USER_2))          # مالک قبلی ثابت می‌ماند
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.store.get_owner().user_id, OWNER)

    def test_17b_non_ai_group_messages_are_ignored(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        run(self.send("سلام به همه", user_id=USER_1))

        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.ai_calls(), 0)

    def test_17c_user_cannot_self_authorize_with_plain_text(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        run(self.send("ai list", user_id=USER_1, reply_to=self.reply_from(USER_1)))

        self.assertFalse(self.store.ai_is_allowed(GROUP_A, USER_1))

    def test_17d_ai_commands_require_owner_even_with_reply(self):
        self.make_owner()
        run(self.send("ai list x", user_id=USER_1, reply_to=self.bot_reply()))
        self.assertEqual(self.client.requests, [])

    def test_17e_outgoing_messages_are_ignored(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_allow_user(GROUP_A, USER_1)
        run(self.send("ai online", user_id=OWNER, out=True))
        run(self.send("سلام", user_id=USER_1, out=True, reply_to=self.bot_reply()))

        self.assertEqual(self.ai_calls(), 0)

    def test_17f_ai_model_default_matches_module_default(self):
        from config import AI_MODEL
        self.assertEqual(self.cfg.ai_model, AI_MODEL)

    def test_17h_output_budget_has_reasoning_headroom(self):
        """مدل استدلالی است؛ بودجه‌ی پیش‌فرض باید به‌اندازه‌ی کافی برای پاسخ هم جا داشته باشد."""
        self.assertGreaterEqual(Config().ai_max_output_tokens, 1024)
        self.assertGreaterEqual(Config.from_env().ai_max_output_tokens, 1024)

    def test_17g_system_prompt_is_sent_first(self):
        self.make_owner()
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_allow_user(GROUP_A, USER_1)
        run(self.send("سلام", user_id=USER_1, reply_to=self.bot_reply()))

        messages = self.ai_client.calls[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], brand.AI_SYSTEM_PROMPT)


class TestBackwardCompatibility(unittest.TestCase):
    """دیتابیس ساخته‌شده در Phase 6 باید بدون خرابی با نسخه‌ی جدید باز شود."""

    OLD_SCHEMA = """
    CREATE TABLE global_owner (
        slot INTEGER PRIMARY KEY CHECK (slot = 1),
        user_id INTEGER NOT NULL UNIQUE,
        username TEXT,
        display_name TEXT,
        claimed_chat_id INTEGER NOT NULL,
        claimed_message_id INTEGER,
        claimed_at TEXT NOT NULL
    );
    CREATE TABLE owner_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER,
        display_name TEXT,
        result TEXT NOT NULL,
        at TEXT NOT NULL
    );
    CREATE TABLE pv_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        username TEXT,
        display_name TEXT,
        first_seen_at TEXT NOT NULL
    );
    """

    def test_old_database_upgrades_cleanly_and_keeps_data(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite3"
            raw = sqlite3.connect(db)
            raw.executescript(self.OLD_SCHEMA)
            raw.execute(
                "INSERT INTO global_owner (slot, user_id, username, display_name,"
                " claimed_chat_id, claimed_message_id, claimed_at)"
                " VALUES (1, 111, 'osine', 'مالک', -1001, 5, '2026-01-01T00:00:00+00:00')"
            )
            raw.execute(
                "INSERT INTO pv_users (user_id, username, display_name, first_seen_at)"
                " VALUES (501, 'user_1', 'کاربر ۱', '2026-01-01T00:00:00+00:00')"
            )
            raw.commit()
            raw.close()

            store = OwnerStore(db)          # ← جدول‌های AI باید خودکار ساخته شوند
            try:
                self.assertEqual(store.get_owner().user_id, 111)      # مالک حفظ شده
                self.assertEqual(store.count_pv_users(), 1)           # کاربران PV حفظ شده
                self.assertTrue(store.register_pv_user(502))    # ثبت کاربر جدید PV (True = جدید)
                self.assertFalse(store.register_pv_user(502))    # تکراری ⇒ جدید نیست

                # و متدهای AI روی همان دیتابیس کار کنند
                store.ai_set_enabled(-1001, True)
                store.ai_allow_user(-1001, 501, username="user_1")
                self.assertTrue(store.ai_is_enabled(-1001))
                self.assertTrue(store.ai_is_allowed(-1001, 501))
                self.assertTrue(store.ai_consume_quota(-1001, "2026-10-08", 3))
                self.assertEqual(store.ai_usage(-1001, "2026-10-08"), 1)
            finally:
                store.close()


class TestRealClientIntegration(AITestCase):
    """مسیر کامل GroupAI → CloudflareAI → HTTP (جعلی) → پارس پاسخ → ارسال."""

    def test_17h_end_to_end_with_real_client_and_fake_http(self):
        from ai_client import CloudflareAI
        from tests.test_ai_client import FakeSession

        session = FakeSession(payload={
            "result": {"response": "تهران آفتابی است."}, "success": True,
            "errors": [], "messages": [],
        })
        real_client = CloudflareAI(
            "acct-123", "tok-abc", "@cf/zai-org/glm-4.7-flash",
            timeout=5, max_output_tokens=128, session=session,
        )
        ai = GroupAI(self.cfg, self.store, self.sender, real_client)
        core = BotCore(self.cfg, self.store, self.sender, ai=ai)

        self.store.claim(OWNER, chat_id=GROUP_A, message_id=1, display_name="مالک")
        self.store.ai_set_enabled(GROUP_A, True)
        self.store.ai_allow_user(GROUP_A, USER_1, username="osine")

        async def scenario():
            event = FakeEvent("هوای تهران چطوره؟", user_id=USER_1, chat_id=GROUP_A,
                              is_group=True, reply_to=self.bot_reply())
            await core.on_new_message(self.client, event)

        run(scenario())

        # درخواست واقعی به endpoint واقعی Cloudflare رفته و پاسخ همان مدل برگشته
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(
            session.calls[0]["url"],
            "https://api.cloudflare.com/client/v4/accounts/acct-123/ai/run/"
            "@cf/zai-org/glm-4.7-flash",
        )
        self.assertEqual(session.calls[0]["body"]["max_completion_tokens"], 128)
        self.assertEqual(self.client.text_messages(), ["تهران آفتابی است."])


if __name__ == "__main__":
    unittest.main(verbosity=2)
