"""
تست‌های جامع قابلیت رندر بومی بلاک‌های کد (Native Code-Block Rendering)
در کلاینت سروش پلاس / اس‌پلاس‌پلاس با استفاده از MessageEntityPre در MTProto.

پوشش موارد خواسته‌شده در Task:
  ۱) بلاک‌های کد HTML
  ۲) بلاک‌های کد CSS، JavaScript و Python با نرمال‌سازی شناسه زبان
  ۳) متن توضیحی فارسی قبل و بعد از بلاک کد به صورت مجزا
  ۴) چند بلاک کد مختلف در یک پاسخ هوش مصنوعی
  ۵) حصارهای کد خالی و ناقص/بسته‌نشده (Malformed / Unclosed Fences)
  ۶) تقسیم پیام‌ها و کدهای بسیار طولانی با رعایت سقف طول کاراکتر
  ۷) حفظ دقیق فاصله‌ها، تورفتگی‌ها (Indentation)، یونیکد و کاراکترهای ویژه
  ۸) سازگاری کامل با پاسخ‌های متنی معمولی (بدون بلاک کد)
  ۹) رفتار دکمه کپی کد در سطح کلاینت بر پایه MessageEntityPre
  ۱۰) آزمون یکپارچه End-to-End چت ربات و ارسال پیام با Entity بومی
"""

import asyncio
import unittest
from splusthon.tl.types import MessageEntityPre

import brand
from ai_service import GroupAI
from config import Config
from core import BotCore
from storage import OwnerStore
from tests.fakes import FakeClient, FakeEvent, FakeReplyMessage, FakeSender


def run(coro):
    return asyncio.run(coro)


class TestCodeBlockRendering(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.from_env()

    # -----------------------------------------------------------------------
    # ۱) بلاک کد HTML
    # -----------------------------------------------------------------------
    def test_01_html_code_block(self):
        sample = (
            "سلام! در ادامه یک نمونه کد HTML برای شما آماده شده است:\n"
            "```html\n"
            "<!DOCTYPE html>\n"
            "<html>\n"
            "<head>\n"
            "    <title>صفحه آزمایشی</title>\n"
            "</head>\n"
            "<body>\n"
            "    <h1>سلام به دنیای برنامه‌نویسی!</h1>\n"
            "</body>\n"
            "</html>\n"
            "```\n"
            "این کد یک صفحه معتبر وب است."
        )

        chunks = brand.format_ai_response_chunks(sample, max_chars=3500)
        self.assertEqual(len(chunks), 1)
        text, entities = chunks[0]

        # بررسی وجود entity کد بومی
        self.assertEqual(len(entities), 1)
        self.assertIsInstance(entities[0], MessageEntityPre)
        self.assertEqual(entities[0].language, "html")

        # استخراج قطعه کد با آفست و طول UTF-16
        enc = text.encode("utf-16-le")
        start = entities[0].offset * 2
        end = (entities[0].offset + entities[0].length) * 2
        code_extracted = enc[start:end].decode("utf-16-le")

        self.assertIn("<h1>سلام به دنیای برنامه‌نویسی!</h1>", code_extracted)
        self.assertNotIn("سلام! در ادامه", code_extracted)
        self.assertNotIn("این کد یک صفحه معتبر", code_extracted)

    # -----------------------------------------------------------------------
    # ۲) بلاک‌های کد CSS، JavaScript و Python و نرمال‌سازی شناسه زبان
    # -----------------------------------------------------------------------
    def test_02_css_js_python_code_blocks(self):
        # تست پایتون با نام مستعار py
        sample_py = "```py\ndef add(a, b):\n    return a + b\n```"
        chunks_py = brand.format_ai_response_chunks(sample_py)
        self.assertEqual(chunks_py[0][1][0].language, "python")

        # تست سی‌اس‌اس
        sample_css = "```CSS\n.btn {\n    color: #fff;\n    background: #007bff;\n}\n```"
        chunks_css = brand.format_ai_response_chunks(sample_css)
        self.assertEqual(chunks_css[0][1][0].language, "css")

        # تست جاوااسکریپت با نام مستعار js
        sample_js = "```js\nconst greeting = 'درود';\nconsole.log(greeting);\n```"
        chunks_js = brand.format_ai_response_chunks(sample_js)
        self.assertEqual(chunks_js[0][1][0].language, "javascript")

    # -----------------------------------------------------------------------
    # ۳) متن توضیحی فارسی قبل و بعد از بلاک کد به صورت مجزا
    # -----------------------------------------------------------------------
    def test_03_persian_text_before_and_after_code_block(self):
        pre_text = "این توضیح فارسی قبل از کد است که باید کاملاً معمولی نمایش داده شود.\n"
        post_text = "\nاین هم توضیحات تکمیلی و جمع‌بندی نهایی بعد از بلاک کد است."
        code_body = "x = [1, 2, 3]\nprint(sum(x))"

        full_prompt = f"{pre_text}```python\n{code_body}\n```{post_text}"
        chunks = brand.format_ai_response_chunks(full_prompt)
        text, entities = chunks[0]

        # متن پیام باید توضیحات را در بر داشته باشد
        self.assertTrue(text.startswith("این توضیح فارسی قبل از کد است"))
        self.assertTrue(text.endswith("جمع‌بندی نهایی بعد از بلاک کد است."))

        # اما entity نباید توضیحات را شامل شود
        e = entities[0]
        enc = text.encode("utf-16-le")
        code_extracted = enc[e.offset * 2 : (e.offset + e.length) * 2].decode("utf-16-le")
        self.assertEqual(code_extracted, code_body)

    # -----------------------------------------------------------------------
    # ۴) چند بلاک کد مختلف در یک پاسخ هوش مصنوعی
    # -----------------------------------------------------------------------
    def test_04_multiple_code_blocks_in_one_response(self):
        response = (
            "ابتدا فایل HTML:\n"
            "```html\n"
            "<button id=\"myBtn\">کلیک کنید</button>\n"
            "```\n"
            "سپس استایل CSS:\n"
            "```css\n"
            "#myBtn { padding: 8px 16px; border-radius: 4px; }\n"
            "```\n"
            "و در پایان کدهای جاوااسکریپت:\n"
            "```javascript\n"
            "document.getElementById('myBtn').onclick = () => alert('کلیک شد');\n"
            "```\n"
            "پروژه شما آماده است!"
        )

        chunks = brand.format_ai_response_chunks(response)
        self.assertEqual(len(chunks), 1)
        text, entities = chunks[0]

        self.assertEqual(len(entities), 3)
        self.assertEqual(entities[0].language, "html")
        self.assertEqual(entities[1].language, "css")
        self.assertEqual(entities[2].language, "javascript")

        # بررسی عدم تداخل آفست‌ها و صحت هر بلاک
        enc = text.encode("utf-16-le")
        code1 = enc[entities[0].offset * 2 : (entities[0].offset + entities[0].length) * 2].decode("utf-16-le")
        code2 = enc[entities[1].offset * 2 : (entities[1].offset + entities[1].length) * 2].decode("utf-16-le")
        code3 = enc[entities[2].offset * 2 : (entities[2].offset + entities[2].length) * 2].decode("utf-16-le")

        self.assertEqual(code1, '<button id="myBtn">کلیک کنید</button>')
        self.assertEqual(code2, "#myBtn { padding: 8px 16px; border-radius: 4px; }")
        self.assertEqual(code3, "document.getElementById('myBtn').onclick = () => alert('کلیک شد');")

    # -----------------------------------------------------------------------
    # ۵) حصارهای کد خالی و ناقص/بسته‌نشده (Empty and Malformed Code Fences)
    # -----------------------------------------------------------------------
    def test_05_empty_and_malformed_code_fences(self):
        # ۱) بلاک کد کاملاً خالی
        empty_sample = "متن تست:\n```python\n```\nادامه متن."
        chunks_empty = brand.format_ai_response_chunks(empty_sample)
        # نباید کرش کند و entity به طول ۰ تولید نشود (جلوگیری از خطای سرور)
        self.assertEqual(len(chunks_empty), 1)
        self.assertEqual(chunks_empty[0][1], [])

        # ۲) حصار بسته‌نشده در انتهای متن
        unclosed_sample = (
            "کد زیر تا انتها آمده اما تگ بسته نشده است:\n"
            "```python\n"
            "def broken():\n"
            "    print('ناقص')"
        )
        chunks_unclosed = brand.format_ai_response_chunks(unclosed_sample)
        self.assertEqual(len(chunks_unclosed), 1)
        text, entities = chunks_unclosed[0]
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].language, "python")
        self.assertIn("def broken():", text)

        # ۳) بدون تعیین نام زبان
        no_lang_sample = "```\necho 'hello world'\n```"
        chunks_no_lang = brand.format_ai_response_chunks(no_lang_sample)
        self.assertEqual(chunks_no_lang[0][1][0].language, "")

    # -----------------------------------------------------------------------
    # ۶) کدهای طولانی و تقسیم پیام با حفظ سقف طول
    # -----------------------------------------------------------------------
    def test_06_long_code_and_message_splitting(self):
        # ساخت یک اسکریپت طولانی به اندازه بیش از ۴۰۰۰ کاراکتر
        long_lines = [f"    item_{i} = calculate_value({i}, factor=2.5)  # محاسبات مورد {i}" for i in range(120)]
        long_code = "def process_all():\n" + "\n".join(long_lines) + "\n    return True"
        raw_msg = f"کد پردازش سنگین پایتون:\n```python\n{long_code}\n```\nپایان کد."

        # تقسیم با سقف ۲۰۰۰ کاراکتر برای شبیه‌سازی
        chunks = brand.format_ai_response_chunks(raw_msg, max_chars=2000)
        self.assertGreater(len(chunks), 1)

        # تمام قطعات باید سقف طول را رعایت کرده باشند
        for idx, (chunk_text, chunk_ents) in enumerate(chunks):
            u_len = brand.utf16_len(chunk_text)
            self.assertLessEqual(u_len, 2000, f"تکه شماره {idx} از سقف مجاز بیشتر است")
            # هر تکه‌ای که دارای کد است باید entity معتبر MessageEntityPre داشته باشد
            for e in chunk_ents:
                self.assertIsInstance(e, MessageEntityPre)
                self.assertEqual(e.language, "python")
                # بررسی اعتبارسنجی آفست و طول داخل همان تکه
                enc = chunk_text.encode("utf-16-le")
                extracted = enc[e.offset * 2 : (e.offset + e.length) * 2].decode("utf-16-le")
                self.assertTrue(len(extracted) > 0)

    # -----------------------------------------------------------------------
    # ۷) حفظ دقیق فاصله‌ها، تورفتگی‌ها (Indentation)، یونیکد و کاراکترهای ویژه
    # -----------------------------------------------------------------------
    def test_07_exact_preservation_of_indentation_unicode_special_chars(self):
        complex_code = (
            "class ComplexStructure:\n"
            "\tdef __init__(self, data: dict) -> None:\n"
            "        # کامنت فارسی با کاراکترهای خاص: «تست»، <XML>, &amp;, 'quotes' and \"double\"\n"
            "        self.data = data\n"
            "        self.symbols = ['🚀', '🦊', '✨', '✓']\n"
            "\n"
            "    def get_formatted(self) -> str:\n"
            "        return f\"{self.symbols[0]} -> {self.data.get('کلید', 0)}\""
        )
        full_sample = f"کلاس مورد نظر به شرح زیر است:\n```python\n{complex_code}\n```"
        chunks = brand.format_ai_response_chunks(full_sample)
        text, entities = chunks[0]

        e = entities[0]
        enc = text.encode("utf-16-le")
        extracted = enc[e.offset * 2 : (e.offset + e.length) * 2].decode("utf-16-le")

        # باید کاراکتر به کاراکتر با کد اصلی برابر باشد
        self.assertEqual(extracted, complex_code)
        self.assertIn("\tdef __init__", extracted)
        self.assertIn("        self.data", extracted)
        self.assertIn("<XML>, &amp;", extracted)
        self.assertIn("🦊", extracted)

    # -----------------------------------------------------------------------
    # ۸) سازگاری کامل با پاسخ‌های متنی معمولی (بدون بلاک کد)
    # -----------------------------------------------------------------------
    def test_08_compatibility_with_normal_text_responses(self):
        plain_text = "سلام! حال شما چطوره؟ امروز چطور می‌تونم کمکتون کنم؟"
        chunks = brand.format_ai_response_chunks(plain_text)
        self.assertEqual(len(chunks), 1)
        text, entities = chunks[0]
        self.assertEqual(text, plain_text)
        self.assertEqual(entities, [])

    # -----------------------------------------------------------------------
    # ۹) رفتار دکمه کپی کد و فیلدهای MessageEntityPre در API
    # -----------------------------------------------------------------------
    def test_09_native_copy_button_documentation_and_entity_structure(self):
        pre = MessageEntityPre(offset=10, length=50, language="javascript")
        d = pre.to_dict()
        self.assertEqual(d["_"], "MessageEntityPre")
        self.assertEqual(d["offset"], 10)
        self.assertEqual(d["length"], 50)
        self.assertEqual(d["language"], "javascript")

    # -----------------------------------------------------------------------
    # ۱۰) آزمون جریان چت یکپارچه و ارسال پاسخ با MessageEntityPre
    # -----------------------------------------------------------------------
    def test_10_end_to_end_ai_chat_flow_with_code_block(self):
        import tempfile
        from tests.fakes import FakeAI

        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as f:
            store = OwnerStore(f.name)
            client = FakeClient()
            ai_client = FakeAI(
                reply=(
                    "این یک کد ساده HTML است:\n"
                    "```html\n"
                    "<h1>سلام جهان</h1>\n"
                    "```\n"
                    "پایان توضیحات."
                )
            )

            owner_id = 9999
            group_id = -100123
            store.claim(owner_id, chat_id=group_id, display_name="global_owner")
            store.activate_group(group_id, activated_by=owner_id, group_name="گروه تست برنامه‌نویسی")
            store.ai_set_enabled(group_id, True)

            from sender import BrandSender

            sender = BrandSender(self.cfg)
            ai_service = GroupAI(
                cfg=self.cfg,
                store=store,
                sender=sender,
                ai_client=ai_client,
            )
            core = BotCore(self.cfg, store, sender=sender, ai=ai_service)

            # ارسال پیام پرسش به ربات با ریپلای
            msg_ev = FakeEvent(
                "ai یه کد html ساده بده",
                user_id=owner_id,
                chat_id=group_id,
                msg_id=301,
                reply_to=FakeReplyMessage(sender_id=client.me_id, msg_id=300, out=True),
            )

            client.clear_requests()
            run(core.on_new_message(client, msg_ev))

            # بررسی پیام ارسالی توسط کلاینت
            sent = client.sent_messages()
            self.assertGreaterEqual(len(sent), 1)
            last_msg = sent[-1]

            # متن پیام باید بدون تگ‌های حصاری ``` باشد
            self.assertIn("<h1>سلام جهان</h1>", last_msg["message"])
            self.assertNotIn("```html", last_msg["message"])

            # entity باید حاوی MessageEntityPre با زبان html باشد
            ents = last_msg["entities"]
            pre_ents = [e for e in ents if isinstance(e, MessageEntityPre)]
            self.assertEqual(len(pre_ents), 1)
            self.assertEqual(pre_ents[0].language, "html")


if __name__ == "__main__":
    unittest.main()
