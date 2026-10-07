# گزارش بررسی پروتکل واقعی سروش پلاس (مبنای کد این پروژه)

هدف: قبل از نوشتن حتی یک خط کد، مشخص شود اتصال «بدون Bot Token» به سروش پلاس **واقعاً**
چگونه انجام می‌شود، سشن و لاگین چطور کار می‌کنند و Bold/Quote با چه سازنده‌های واقعی
MTProto ارسال می‌شوند. هیچ endpoint یا پارامتری در پروژه از خودمان اضافه نشده است.

## منابعی که بررسی شد

| منبع | نسخه/نشانی |
| --- | --- |
| SPlusthon (فورک Telethon مخصوص سروش پلاس) | `github.com/shayanheidari01/SPlusthon` — v1.1.4 (PyPI) |
| NSplusthon (فورک نگه‌داری‌شده) | `github.com/Amogrotex/NSplusthon` |
| TL Schema سروش پلاس | `splusthon_generator/data/api.tl` — **LAYER 182** |
| راهنمای رسمی سروش پلاس درباره‌ی «نقل قول» | `v8.splus.ir/guide_plus` |
| تجربه‌ی عملی پروژه‌های متن‌باز سروش | `github.com/splusesupor-oss/splus-aifox-bot` (blockquote+bold) |

## ۱) لایه‌ی انتقال و مسیر اتصال

| موضوع | مقدار واقعی | محل در سورس |
| --- | --- | --- |
| کلاس کلاینت | `SoroushClient` | `splusthon/client/soroushclient.py` |
| سرور پیش‌فرض | `im-server.splus.ir` | `splusthon/client/telegrambaseclient.py` (‏`DEFAULT_IPV4_IP`) |
| پورت | `443` | همان فایل، نگاشت DCها در `_get_dc()` |
| DC پیش‌فرض | `3` | `DEFAULT_DC_ID = 3` |
| انتقال | WebSocket اوبفاسکیت‌شده‌ی MTProto: `wss://{ip}:{port}/apiws` + هدر `Origin` | `splusthon/network/connection/websocket.py` |
| keepalive | heartbeat = 30s و reconnect دوره‌ای | همان فایل |
| کلیدهای RSA سرور سروش | ۵ کلید ثابت با کامنت «from Soroush JS client» | `splusthon/crypto/rsa.py` |
| api_id / api_hash | `1030400` / `6edb16cf88714a4e9a805e928c39c937` (پیش‌فرض کتابخانه؛ مقادیر عمومی کلاینت وب سروش، **نه Bot Token**) | `splusthon/client/telegrambaseclient.py` |
| نسخه‌ی اپ در InitConnection | `app_version = '3.9.2 A'`, `lang_code = 'fa'` | همان فایل |

## ۲) احراز هویت (بدون Bot Token)

فایل `splusthon/client/auth.py`:

1. `send_code_request(phone)` → `auth.sendCodeRequest`
2. `sign_in(phone, code, phone_code_hash)` → `auth.signInRequest`
3. در صورت فعال بودن رمز دو مرحله‌ای → `sign_in(password=...)` → `auth.checkPasswordRequest`
4. `_phone_code_hash` برای ادامه‌ی فرایند نگه داشته می‌شود.

پروژه این‌ها را با `client.start(phone=..., code_callback=..., password=...)` صدا می‌زند
(همان مسیر مستندِ خود کتابخانه) و در صورت نیاز می‌تواند کاملاً تعاملی در Termux کار کند.

## ۳) ذخیره‌ی سشن

| شکل | جزئیات |
| --- | --- |
| `SQLiteSession` (پیش‌فرض این پروژه) | فایل `*.session`؛ جدول `sessions` شامل `dc_id, server_address, port, auth_key, tmp_auth_key`؛ نسخه‌ی دیتابیس ۸ |
| `StringSession` | رشته‌ی قابل انتقال (`from splusthon.sessions import StringSession`) |

فایل سشن = دسترسی کامل به حساب کاربری؛ محرمانه بماند.

## ۴) ارسال و دریافت پیام

* ارسال: `messages.sendMessage#280d096f` (و `sendMedia`) — در `splusthon/client/messages.py`
  و اسکیمای `api.tl`.
  کتابخانه در `send_message()` مقدار `reply_to` را به `types.InputReplyToMessage(reply_to)`
  تبدیل می‌کند؛ برای استفاده از `quote_text` (نقل‌قول متنی) در این پروژه درخواست
  `messages.SendMessageRequest` **مستقیماً** ساخته می‌شود تا پارامترهای نقل‌قول قابل تنظیم باشند.
* دریافت: `events.NewMessage` روی همان اتصال؛ ویژگی‌های `chat_id`, `sender_id`, `raw_text`,
  `is_group`, `out` (برای نادیده‌گرفتن پیام‌های خودِ حساب).

## ۵) فرمت‌بندی: Bold و Quote/Glass Quote (سازنده‌های واقعی)

از `api.tl` (LAYER 182):

```
messageEntityBold#bd610bc9 offset:int length:int = MessageEntity;
messageEntityBlockquote#20df5d0 offset:int length:int = MessageEntity;

messageReplyHeader#afbc09db flags:# ... quote:flags.9?true ...
    quote_text:flags.6?string quote_entities:flags.7?Vector<MessageEntity>
    quote_offset:flags.10?int = MessageReplyHeader;

inputReplyToMessage#22c0f6d5 flags:# reply_to_msg_id:int top_msg_id:flags.0?int
    reply_to_peer_id:flags.1?InputPeer quote_text:flags.2?string
    quote_entities:flags.3?Vector<MessageEntity> quote_offset:flags.4?int = InputReplyTo;

messages.sendMessage#280d096f flags:# ... reply_to:flags.0?InputReplyTo
    message:string random_id:long ... entities:flags.3?Vector<MessageEntity> = Updates;
```

نتیجه‌گیری‌ها:

* «Bold» = `MessageEntityBold`؛ «نقل‌قول/Glass Quote» = `MessageEntityBlockquote` —
  هر دو واقعی و در اسکیمای سروش موجود.
* قابلیت رسمی «نقل قول» در خود اپ سروش پلاس (انتخاب متن → سه‌نقطه → نقل قول) معادل
  `inputReplyToMessage.quote_text/quote_entities` است.
* پارسر HTML خود کتابخانه (`splusthon/extensions/html.py`) تگ `<blockquote>` را به همین
  entity تبدیل می‌کند و آفست‌ها را با `add_surrogate/del_surrogate` در فضای **UTF-16**
  محاسبه می‌کند — همان استانداردی که در `brand.py` این پروژه هم رعایت شده است.
* اگر نسخه‌ای از اپ/سرور، entity نقل‌قول را رندر/قبول نکند، به‌هیچ‌وجه «جعلی» جایگزین
  نمی‌کنیم؛ فقط زنجیره‌ی fallback (`blockquote+bold` → `bold` → متن ساده) اجرا و در لاگ
  ثبت می‌شود تا معلوم باشد کدام حالت واقعاً پذیرفته شده است.

## چه چیزی قابل تأیید نبود؟

بدون یک حساب واقعی و اجرای زنده، نمی‌توان «پذیرش یا رد blockquote توسط سرور سروش» را از
پیش اثبات کرد؛ بنابراین در کد، منطق fallback + لاگ دقیق گذاشته شده و حالت‌ها همه بر اساس
سازنده‌های واقعی اسکیما ساخته شده‌اند (نه shim و نه API ساختگی).
