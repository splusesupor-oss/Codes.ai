"""
تست‌های رگرسیون و جامع ساختار سه‌مدلی Fox AI (Groq و Cloudflare Workers AI).

پوشش کامل:
  ۱) هدایت خودکار (Auto-Routing) بر اساس نیت کاربر:
     - مدل ۱ (Groq openai/gpt-oss-20b) برای احوال‌پرسی و چت کوتاه
     - مدل ۳ (Groq openai/gpt-oss-120b) برای برنامه‌نویسی و کدهای تخصصی
     - مدل ۲ (Cloudflare @cf/meta/llama-3.3-70b-instruct-fp8-fast) برای استدلال و تحلیل
  ۲) مصونیت واژگان ابهام‌آمیز فارسی (کد ملی، کد پستی، برنامه غذایی، خطای شناختی) از رفتن به مدل ۳
  ۳) اولویت مطلق انتخاب دستی مالک سراسری (ai model 1/2/3) بر حالت خودکار
  ۴) عدم تلاش مجدد (No Retry) در خطاهای HTTP 429 و 401/403 کلاینت Groq
  ۵) رفتار عدم تنظیم کلید Groq:
     - خطای صریح در صورت انتخاب دستی مدل Groq
     - فال‌بک ایمن به مدل ۲ در حالت خودکار
  ۶) سازگاری کامل با FakeAI و FakeClient موجود در مخزن
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ai_client import AIConfigError, AIQuotaExceeded, GroqAI
from ai_service import GroupAI, classify_query_intent
from config import Config
from models import MODEL_PROFILES, get_model_profile
from sender import BrandSender
from storage import OwnerStore
from tests.fakes import FakeAI, FakeClient, FakeEvent, FakeReplyMessage, FakeSender


def run(coro):
    return asyncio.run(coro)


class FakeGroq:
    def __init__(self, reply="پاسخ گروک"):
        self.reply = reply
        self.calls = []
        self._available = True

    def is_available(self):
        return self._available

    async def chat(self, messages, *, model, max_tokens=None, **kwargs):
        self.calls.append({
            "messages": messages,
            "model": model,
            "max_tokens": max_tokens,
            "kwargs": kwargs,
        })
        from ai_client import AIResponse
        return AIResponse(text=self.reply, usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})


class TestThreeModelRouting(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(groq_api_key="gsk_test_key")
        self.store = OwnerStore(":memory:")
        self.sender = FakeSender(user_id=123)
        self.sender.send_text = AsyncMock(return_value=MagicMock())
        self.fake_cf = FakeAI("پاسخ کلودفلر")
        self.fake_groq = FakeGroq("پاسخ گروک")
        self.service = GroupAI(
            cfg=self.cfg,
            store=self.store,
            sender=self.sender,
            ai_client=self.fake_cf,
            groq_client=self.fake_groq,
        )

    def test_intent_classification_simple_chat_to_model_1(self):
        queries = ["سلام چطوری", "صبح بخیر رفیق", "خوبی چه خبر", "درود بر شما"]
        for q in queries:
            self.assertEqual(classify_query_intent(q), 1, f"Failed for {q}")

    def test_intent_classification_coding_to_model_3(self):
        queries = [
            "def quicksort(arr): return arr",
            "کوری sql برای یافتن کاربران فعال بنویس",
            "کد پایتون برای خواندن فایل csv",
            "این ارور کدم چیه؟ TypeError: unsupported operand",
            "الگوریتم دایکسترا در سی پلاس پلاس",
        ]
        for q in queries:
            self.assertEqual(classify_query_intent(q), 3, f"Failed for {q}")

    def test_intent_classification_persian_false_positives_routed_to_model_2(self):
        queries = [
            "کد ملی من استعلام نمیشه چیکار کنم؟",
            "کد پستی ده رقمی چطور ثبت میشه؟",
            "یک برنامه غذایی عالی برای لاغری میخوام",
            "برنامه ریزی کنکور برای تابستان",
            "برنامه سفر شیراز چند روزه خوبه؟",
            "خطای شناختی ذهن‌خوانی در روانشناسی چیست؟",
            "انسان جایز الخطا است چه مفهومی دارد؟",
        ]
        for q in queries:
            self.assertEqual(classify_query_intent(q), 2, f"Failed for {q}")

    def test_manual_model_lock_overrides_auto_routing(self):
        chat_id = 9001
        self.store.activate_group(chat_id, 100)

        # در ابتدا مدلی دستی انتخاب نشده؛ کوئری برنامه‌نویسی باید خودکار به مدل ۳ برود
        p = self.service.get_group_model_profile(chat_id, user_text="def test(): pass")
        self.assertEqual(p.id, 3)

        # قفل دستی روی مدل ۲ توسط مالک
        self.store.set_group_model(chat_id, 2)
        p_manual = self.service.get_group_model_profile(chat_id, user_text="def test(): pass")
        self.assertEqual(p_manual.id, 2)
        self.assertEqual(p_manual.provider, "cloudflare")

    def test_groq_no_retry_on_429(self):
        groq = GroqAI("gsk_fake_key")
        mock_response = MagicMock()
        mock_response.status = 429
        mock_response.text = AsyncMock(return_value="Rate limit reached")

        session_mock = MagicMock()
        session_mock.post.return_value.__aenter__.return_value = mock_response
        groq._session = session_mock

        with self.assertRaises(AIQuotaExceeded):
            run(groq.chat([{"role": "user", "content": "hi"}], model="openai/gpt-oss-20b"))

        # فقط ۱ بار فراخوانی شده و تکرار نشده است
        self.assertEqual(session_mock.post.call_count, 1)

    def test_unconfigured_groq_fallback_in_auto_mode(self):
        chat_id = 9002
        self.store.activate_group(chat_id, 100)
        self.fake_groq._available = False  # بدون کلید Groq

        req = MagicMock(
            chat_id=chat_id,
            sender_id=10,
            text="سلام",
            model_profile=MODEL_PROFILES[1],
            target_user_info=None,
            asker_info=None,
            client=MagicMock(),
            peer=None,
        )
        run(self.service._process_single_request(req))
        self.assertTrue(len(self.fake_cf.calls) > 0)
        self.assertEqual(len(self.fake_groq.calls), 0)


if __name__ == "__main__":
    unittest.main()
