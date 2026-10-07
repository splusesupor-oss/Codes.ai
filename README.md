# ربات «ai cod» سروش پلاس — بدون Bot Token

ربات بسیار ساده با **دو دستور گروهی** («`ai cod`» و «`کدرز`») **+ معرفی/منوی یک‌بارِ PV، پاسخ ۶ گزینه و مدیریت کاربران PV** که مستقیم با **حساب کاربری سروش پلاس**
به سرور سروش وصل می‌شود. در این پروژه **هیچ Bot Token، هیچ Telegram Bot API و هیچ سرویس واسطه‌ای**
استفاده نشده است.

> ⚠️ ابتدا پروتکل واقعی سروش پلاس بررسی شد و کد بر اساس همان نوشته شده — هیچ endpoint یا پارامتر
> ساختگی در پروژه وجود ندارد. جزئیات این بررسی در بخش [«۱. بررسی API واقعی»](#۱-بررسی-api-واقعی-سروش-پلاس) آمده است.

---

## فهرست

1. [بررسی API واقعی سروش پلاس](#۱-بررسی-api-واقعی-سروش-پلاس)
2. [پاسخ به ۵ پرسش فنی](#۲-پاسخ-به-۵-پرسش-فنی)
3. [ساختار پروژه](#۳-ساختار-پروژه)
4. [نصب و اجرا در Termux](#۴-نصب-و-اجرا-در-termux)
5. [دستورات ربات و قوانین مالکیت (شامل مسیر ۵: هوش مصنوعی گروه‌ها)](#۵-دستورات-ربات-و-قوانین-مالکیت)
6. [حافظه‌ی دائمی و ثبت اتمیک](#۶-حافظهی-دائمی-و-ثبت-اتمیک)
7. [تست‌ها](#۷-تستها)
8. [تنظیمات](#۸-تنظیمات)
9. [محدودیت‌ها و نکات صادقانه](#۹-محدودیتها-و-نکات-صادقانه)
10. [عیب‌یابی](#۱۰-عیبیابی)

---

## ۱. بررسی API واقعی سروش پلاس

### چه چیزی بررسی شد؟

| منبع | چه چیزی از آن استخراج شد |
| --- | --- |
| `SPlusthon` (کتابخانه، فورک Telethon مخصوص سروش پلاس) — نسخه ۱.۱.۴ | معماری کامل اتصال، لاگین، سشن، ارسال/دریافت پیام |
| `splusthon_generator/data/api.tl` (TL Schema لایه ۱۸۲) | سازنده‌های واقعی فرمت‌بندی و نقل‌قول |
| `NSplusthon` (فورک نگه‌داری‌شده‌ی همان کتابخانه) | تأیید ساختار و یادگیری از مثال‌هایش |
| مستندات/راهنمای رسمی سروش پلاس (`v8.splus.ir/guide_plus`) | تعریف رسمی «نقل قول» در خود اپلیکیشن |
| پروژه‌های متن‌باز سروش (مثل `splus-aifox-bot`) | تجربه‌ی عملی ارسال «نقل‌قول شیشه‌ای (blockquote) + Bold» |

### خلاصه‌ی معماری واقعی (هیچ فرضی بدون سورس)

```
┌────────────────┐   MTProto over WebSocket (Obfuscated)   ┌─────────────────────────┐
│  ربات (پایتون) │ ──────────────────────────────────────▶ │ wss://im-server.splus.ir│
│  SoroushClient │        TL Schema — Layer 182            │       :443/apiws        │
└────────────────┘ ◀────────────────────────────────────── └─────────────────────────┘
        │  لاگین با شماره تلفن + کد (+ رمز ۲مرحله‌ای) و ذخیره‌ی سشن در SQLite
        └─ بدون Bot Token، بدون api_id/api_hash اختصاصی
```

* **انتقال:** WebSocket رمزنگاری‌شده با MTProto؛ فایل `splusthon/network/connection/websocket.py`
  (اتصال به `wss://{ip}:{port}/apiws` با هدر `Origin`).
* **سرور/DC:** `DEFAULT_IPV4_IP = 'im-server.splus.ir'` و پورت `443` — در
  `splusthon/client/telegrambaseclient.py` (همه‌ی DCها به همان دامنه مسیر می‌شوند).
* **کلیدهای RSA سرور سروش:** به‌صورت ثابت داخل `splusthon/crypto/rsa.py` قرار دارند
  (با کامنت «Soroush server RSA public keys (from Soroush JS client)»).
* **TL Schema:** لایه‌ی ۱۸۲ — همان اسکیمای سروش پلاس (شامل `messageEntityBold`،
  `messageEntityBlockquote`، `inputReplyToMessage` و ...).
* **نیاز به api_id/api_hash ندارد:** پیش‌فرض‌های خود کتابخانه
  (`api_id = 1030400`, `api_hash = '6edb16cf88714a4e9a805e928c39c937'`) همان مقادیر عمومی
  کلاینت وب سروش پلاس‌اند و در `config.py` این پروژه هم به‌همان شکل قرار گرفته‌اند.
  **این‌ها Bot Token نیستند؛ فقط برای هندشیک MTProto لازم‌اند.**

---

## ۲. پاسخ به ۵ پرسش فنی

### ۱) احراز هویت حساب چگونه انجام می‌شود؟

با MTProto و «شماره تلفن» — دقیقاً مثل نسخه‌ی وب/دسکتاپ سروش پلاس (کد در `splusthon/client/auth.py`):

```python
client = SoroushClient("session_name")          # بدون Bot Token
await client.start(
    phone=lambda: input("شماره: "),             # sendCode
    code_callback=lambda: input("کد: "),        # signIn
    password=lambda: getpass.getpass("2FA: "),  # فقط اگر رمز دو مرحله‌ای فعال باشد
)
```

* `auth.sendCodeRequest` → ارسال کد به شماره.
* `auth.signInRequest(phone, code, phone_code_hash)` → ورود.
* اگر حساب رمز دو مرحله‌ای داشته باشد → `auth.checkPasswordRequest`.
* هندشیک کلید (auth key) با RSA سرور سروش انجام می‌شود (داخل کتابخانه).

### ۲) Session/Authentication چگونه ذخیره می‌شود؟

دو شکل، هر دو موجود در `splusthon/sessions/`:

* **SQLiteSession** (پیش‌فرض این پروژه) → فایل `data/acod_userbot.session`؛ شامل `auth_key`، `dc_id`،
  آدرس سرور و کش موجودیت‌ها. با اجرای بعدی، **دیگر کد ورود لازم نیست**.
* **StringSession** → رشته‌ی قابل کپی (`from splusthon.sessions import StringSession`).

> 🔐 فایل سشن = **دسترسی کامل به حساب**. آن را در گیت‌هاب نگذارید و به کسی ندهید.
> برای لغو دسترسی، از داخل اپ سروش پلاس «پایان همه‌ی نشست‌ها» را بزنید.

### ۳) ارسال و دریافت پیام چگونه انجام می‌شود؟

* **ارسال:** درخواست واقعی `messages.sendMessage` (سازنده‌ی `280d096f`):

```python
functions.messages.SendMessageRequest(
    peer=peer,                     # InputPeer مقصد
    message=text,
    entities=[...],                # لیست MessageEntity
    reply_to=...,                  # InputReplyTo (اختیاری)
    no_webpage=...,                # پیش‌نمایش لینک
)
```

* **دریافت:** کلاینت روی همان اتصال WebSocket آپدیت می‌گیرد:

```python
@client.on(events.NewMessage(incoming=True))
async def handler(event):
    print(event.chat_id, event.sender_id, event.raw_text)
```

### ۴) دریافت پیام‌های گروه چگونه انجام می‌شود؟

همان `events.NewMessage`؛ گروه‌بودن چت با `event.is_group` مشخص می‌شود و برای محدودکردن به یک/چند
گروه خاص می‌توان از `events.NewMessage(chats=[...])` استفاده کرد. در این پروژه فقط پیام‌های گروهی
پردازش می‌شوند (`GROUPS_ONLY = True`) و پیام‌های خودِ ربات (`event.out`) نادیده گرفته می‌شوند تا
حلقه‌ی بی‌پایان ایجاد نشود.

### ۵) Bold و Quote/Glass Quote چگونه ارسال می‌شود؟

هر دو، **سازنده‌های واقعی TL Schema** سروش پلاس هستند:

| قابلیت | سازنده‌ی واقعی MTProto |
| --- | --- |
| **Bold** | `messageEntityBold#bd610bc9 offset:int length:int` |
| **نقل‌قول / Quote / Glass Quote** | `messageEntityBlockquote#20df5d0 offset:int length:int` |
| **نقل‌قولِ متنی واقعی (Reply+Quote)** | `inputReplyToMessage#22c0f6d5 … quote_text:flags.2?string quote_entities:flags.3?Vector<MessageEntity> quote_offset:flags.4?int` |

سه نکته‌ی فنی مهم که در پیاده‌سازی رعایت شده:

1. **آفست‌ها بر حسب واحد UTF-16 هستند، نه تعداد کاراکتر پایتون.** خط تیتر این پیام ایموجی‌های
   بیرون از BMP دارد (`🦊` و `🧑‍💻`)، پس `len()` معمولی جای entity را جابه‌جا می‌کند. در
   `brand.py` از `utf16_len()`/`utf16_offset()` استفاده شده و تست‌ها همین را بررسی می‌کنند.
2. **خط تیتر داخل `blockquote` و خطوط توضیحی `Bold`** — و **لینک بدون هیچ entity** می‌ماند تا
   عیناً دست‌نخورده بماند.
3. کتابخانه‌ی SPlusthon با `parse_mode='html'` هم از `<blockquote>` و `<b>` پشتیبانی می‌کند
   (`splusthon/extensions/html.py`). در این پروژه entityها مستقیم ساخته می‌شوند (شفاف‌تر و
   قابل‌تست)، و یک تست، خروجی سازنده‌ی دستی را با **پارسر خودِ کتابخانه** مقایسه می‌کند تا مطمئن
   شویم دقیقاً یکسان است.

---

## ۳. ساختار پروژه

```
soroush_ai_cod_bot/
├── bot.py              # ورودی اجرا: اتصال + لاگین + ثبت هندلرها
├── core.py             # منطق دو دستور «ai cod» و «کدرز» (بدون وابستگی به شبکه → تست‌پذیر)
├── storage.py          # حافظه‌ی دائمی مالک سراسری روی SQLite + ثبت اتمیک
├── brand.py            # متن دقیق پیام + ساخت entityهای واقعی (blockquote + bold)
├── sender.py           # ارسال با زنجیره‌ی fallback (نقل‌قول شیشه‌ای → Bold → متن ساده)
├── config.py           # همه‌ی تنظیمات در یک جا (بدون Bot Token)
├── manage.py           # ابزار CLI: نمایش/خروجی/ریست مالک
├── requirements.txt    # splusthon
├── run_all_tests.sh    # اجرای همه‌ی تست‌ها
├── tests/
│   ├── fakes.py        # کلاینت و ایونت جعلی (بدون شبکه) برای تست کامل
│   ├── test_storage.py # پایداری، عدم وابستگی به گروه، اتمیک‌بودن (نخ/پروسه)
│   ├── test_brand.py   # متن دقیق، آفست‌های UTF-16، اعتبارسنجی با پارسر کتابخانه
│   ├── test_commands.py# تشخیص دستورها و حالت‌های نرمال‌سازی
│   ├── test_pv_users.py# مدیریت کاربران PV (ثبت، نمایش، آمار، تکه‌تکه‌کردن)
│   ├── test_pv_menu.py # رفتار اولین پیام PV (معرفی+منو) و ۶ گزینه‌ی منو
│   └── test_flow.py    # سناریوهای ۷، ۸، ۹ + fallback + حالت نقل‌قول واقعی
└── data/               # (خودکار) سشن و دیتابیس — در .gitignore
```

---

## ۴. نصب و اجرا در Termux

```bash
# ۱) پیش‌نیازهای پایه
pkg update -y && pkg upgrade -y
pkg install -y python clang libffi openssl git termux-api

# ۲) نصب کتابخانه‌ی اتصال (کلاینت MTProto مخصوص سروش پلاس)
pip install --upgrade pip
pip install splusthon
# اگر aiohttp از سورس کامپایل شد و خطا داد، clang/libffi/openssl را نصب کنید (بالا) و دوباره بزنید.

# ۳) پروژه را در خانه کپی کنید (یا git clone کنید) و اجرا کنید
cd ~/soroush_ai_cod_bot
./run_bot.sh          # یا: python bot.py
```

> متغیرهای محیطی اختیاری در فایل `.env.example` فهرست شده‌اند؛ برای استفاده، آن را به
> `.env` کپی کنید (فایل `.env` در `.gitignore` است و هرگز commit نمی‌شود).

برای فعال‌کردن **هوش مصنوعی گروه‌ها** (بخش ۵.۵)، دو مقدار Cloudflare را در `.env` بگذارید:

```bash
cp .env.example .env
nano .env      # CLOUDFLARE_ACCOUNT_ID و CLOUDFLARE_API_TOKEN را پر کنید
```

* `CLOUDFLARE_ACCOUNT_ID` → از داشبورد Cloudflare (سمت راست صفحه‌ی Workers AI).
* `CLOUDFLARE_API_TOKEN` → یک توکن با دسترسی **Workers AI** (My Profile → API Tokens).
* ⚠️ فایل `.env` **هرگز** نباید commit یا جایی منتشر شود؛ خودِ کد هیچ توکنی ندارد.

**اولین اجرا** از شما می‌پرسد:

```
شماره حساب سروش پلاس (مثل 09123456789):
کد ورود ارسال‌شده را وارد کنید:
رمز دو مرحله‌ای (اگر فعال نیست فقط Enter بزنید):
```

پس از ورود، سشن در `data/acod_userbot.session` ذخیره می‌شود و اجراهای بعدی **بدون کد** بالا می‌آیند.

**برای اجرای دائمی در Termux:**

```bash
termux-wake-lock                 # جلوگیری از خواب رفتن پردازنده
pkg install -y tmux && tmux new -s acod
python bot.py                    # داخل tmux؛ با Ctrl+B بعد D از آن خارج شوید
# بازگشت: tmux attach -t acod
```

اگر می‌خواهید لاگین غیرتعاملی باشد (مثلاً در اسکریپت):

```bash
export ACOD_PHONE=09123456789
export ACOD_PASSWORD='رمز دو مرحله‌ای'   # فقط اگر فعال است
python bot.py
```

> **مهم:** حسابی که ربات با آن وارد می‌شود باید **عضو همان گروه‌ها** باشد. همچنین چون این یک
> «یوزربات» است، دستورها را با همان حساب یا حساب دیگری در گروه بفرستید (پیام‌های خودِ حسابِ ربات
> پردازش نمی‌شوند). توصیه‌ی عملی: یک حساب اختصاصی برای ربات بسازید.

### اجرا روی لینوکس/ویندوز

```bash
python -m venv .venv && source .venv/bin/activate   # ویندوز: .venv\Scripts\activate
pip install -r requirements.txt
python bot.py
```

---

## ۵. دستورات ربات و قوانین مالکیت

### دستور ۱ — `ai cod` (فعال‌سازی مالک سراسری)

| قانون | وضعیت در پیاده‌سازی |
| --- | --- |
| اولین کاربری که در هر گروهی `ai cod` بفرستد، مالک می‌شود | ✅ `OwnerStore.claim()` |
| مالکیت وابسته به گروه نیست | ✅ فقط `user_id` ملاک است (`test_ownership_is_not_group_dependent`) |
| بعد از تعیین مالک، هیچ کاربر دیگری در هیچ گروهی مالک نمی‌شود | ✅ `already_other_owner` → کاملاً نادیده گرفته می‌شود |
| اگر همان مالک در گروه دیگر `ai cod` بزند، مالک جدید ساخته نمی‌شود | ✅ `already_same_owner` (پیش‌فرض: بدون پاسخ؛ با `ANNOUNCE_ON_OWNER_REPEAT=True` فقط پیام معرفی) |
| ثبت اتمیک | ✅ `BEGIN IMMEDIATE` + `PRIMARY KEY CHECK(slot=1)` + تست چندپروسه‌ای |
| پایداری با ری‌استارت | ✅ فایل `data/owner.sqlite3` (`test_owner_survives_restart`) |

پس از ثبت موفق مالک، **پیام فعال‌سازی** (همان پیام معرفی) در همان گروه ارسال می‌شود.

### دستور ۲ — `کدرز` (معرفی ربات)

هر کاربری در هر گروهی `کدرز` بفرستد، ربات همان پیام معرفی را با قالب یکسان ارسال می‌کند
(بدون نیاز به مالک‌بودن).

### مسیر ۳ — پیام خصوصی (PV / Direct Message)

**اولین پیام هر کاربر** (فارغ از متن پیام) دقیقاً دو پاسخ می‌گیرد و بعد از آن دیگر تکرار نمی‌شود:

1. همان **پیام معرفی** فعلی پروژه (یک‌بار برای همیشه‌ی آن کاربر)
2. بلافاصله **منوی انتخاب** (یک‌بار):

```
برای انتخاب فقط دستورات زیر را ارسال کنید

سازنده
کانال روباه
ربات پشتیبانی
سایت خرید
کانال دانلود
ربات روباه
```

**پیام‌های بعدی همان کاربر:**

| متن ارسالی | پاسخ |
| --- | --- |
| `سازنده` | `@osine2` |
| `کانال روباه` | `@ai_fox` |
| `ربات پشتیبانی` | `@Aifox_bot` |
| `سایت خرید` | `https://foxbot.osine2.workers.dev/` |
| `کانال دانلود` | `https://splus.ir/Orderawebsite` |
| `ربات روباه` | `ربات های روباه` + سه خط نسخه‌ها (پایین) |
| هر متن دیگری | **بدون پاسخ** (نه معرفی دوباره، نه منو دوباره، نه پاسخ اختراعی) |

پاسخ `ربات روباه`:

```
ربات های روباه

نسخه یک🔹 @fox_bot
نسخه دو🔹 @aifox
نسخه سه🔹 @bot_fox
```

فرمت همه‌ی این پیام‌ها (منو و ۶ پاسخ) با **همان سیستم قالب‌بندی موجود پروژه** است:
کل متن داخل یک **نقل‌قول شیشه‌ای** (`MessageEntityBlockquote`) و هر خط **Bold**
(`MessageEntityBold`) — از `brand.quote_bold_entities()` و `sender.send_styled()` که همان زنجیره‌ی
fallback معرفی را دارند (`blockquote+bold → bold-only → plain`). هیچ سیستم قالب‌بندی جدیدی ساخته نشده.

نکات پیاده‌سازی:

* «اولین پیام» با **`user_id` واقعی** تعیین می‌شود (نه username) و در جدول دائمی `pv_users`
  ذخیره می‌شود؛ پس بعد از **restart** و بعد از **تغییر username** کاربر «جدید» حساب نمی‌شود.
* تشخیص PV با API واقعی کتابخانه: `event.is_private`
  (در سورس: `splusthon/tl/custom/chatgetter.py` → `isinstance(_chat_peer, types.PeerUser)`).
* `GROUPS_ONLY` **فقط روی دستورها** اثر دارد: `ai cod` و `کدرز` همچنان فقط در گروه کار می‌کنند؛
  مسیر PV از آن مستقل است.
* پاسخ فقط به همان چت/کاربر (`InputPeerUser` همان فرستنده) می‌رود.
* برای جلوگیری از حلقه، پیام‌های `out` (خروجیِ خود یوزربات) هرگز پردازش نمی‌شوند.
* مالک سراسری اولویت دارد: «تعداد اعضا»/«لیست اعضا» از مالک حتی در اولین پیامش هم اجرا می‌شود.
* در PV **هیچ ویژگی اضافه‌ای** جز همین‌ها نیست (نه دکمه، نه ذخیره‌ی متن کاربر، نه منوی دیگر).
* خاموش‌کردن کل پاسخ‌های خودکار PV: `ACOD_PRIVATE_AUTO_REPLY=0` (ثبت کاربر در آمار همچنان انجام می‌شود).

---

### مسیر ۴ — مدیریت کاربران PV (فقط مالک سراسری)

هر کاربری که **حداقل یک‌بار در PV** به ربات پیام بدهد، به‌صورت دائمی در جدول `pv_users`
همان دیتابیس ثبت می‌شود — **هر کاربر فقط یک‌بار** (`user_id` یکتاست؛ پیام‌های بعدی تعداد
را افزایش نمی‌دهند). کاربران گروهی هرگز وارد این آمار نمی‌شوند، و ثبت‌شدن در PV هیچ
مالکیت/ادمینی/عضویتی ایجاد نمی‌کند.

دو دستور، **فقط برای مالک سراسری و فقط در PV**:

| دستور | پاسخ |
| --- | --- |
| `تعداد اعضا` | تعداد کل **+ فهرست کامل** کاربران PV (یک خط خالی بین آن‌ها) |
| `لیست اعضا` | فقط خطوط شماره‌گذاری‌شده، به ترتیب اولین ثبت |

نمونه‌ی خروجی `تعداد اعضا`:

```
تعداد اعضا : 3

1 : @osine
2 : ali
3 : @elism
```

نمونه‌ی «لیست اعضا»:

```
1 : @osine
2 : ali
3 : @elism
```

* اگر کاربر `username` داشته باشد → `@username`؛ در غیر این صورت نام نمایشی.
* اگر هیچ‌کدام نبود → `کاربر بدون نام` (مقدار امن، قابل تغییر با `ACOD_PV_UNKNOWN_NAME`).
* **لیست طولانی:** خطوط به قطعه‌های حداکثر ۳۵۰۰ کاراکتری شکسته و در چند پیام ارسال
  می‌شوند (سقف واقعی سرور در کتابخانه `utils.split_text(..., limit=4096)` است)، پس
  با محدودیت طول پیام خطا نمی‌خوریم. عدد با `ACOD_MAX_MESSAGE_CHARS` قابل تنظیم است.
  **شماره‌گذاری قبل از تکه‌تکه‌کردن محاسبه می‌شود، پس در پیام‌های متوالی ادامه‌دار
  می‌ماند** (۱، ۲، ۳ … بدون پرش یا تکرار). این برای هر دو دستور `تعداد اعضا` و
  `لیست اعضا` برقرار است.
* کاربر غیرمالک اگر همین متن را بفرستد، فقط همان **پاسخ خودکار معرفی** را می‌گیرد
  (دستور مدیریتی اجرا نمی‌شود). در گروه‌ها هم این دو دستور اجرا نمی‌شوند.
* مسیر دستورها کاملاً جدا از منطق مالک سراسری/SQLite قبلی است و چیزی در آن تغییر نکرده.

---

### مسیر ۵ — هوش مصنوعی گروه‌ها (فقط GROUP — بی‌ارتباط با PV)

هوش مصنوعی با **Cloudflare Workers AI** و مدل واقعی
`@cf/zai-org/glm-4.7-flash` کار می‌کند (API واقعی:
`POST https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}` با
`Authorization: Bearer …`).

| دستور (فقط مالک سراسری، فقط در گروه) | کار |
| --- | --- |
| `ai online` | روشن‌کردن AI برای **همان گروه** |
| `ai of` | خاموش‌کردن AI برای **همان گروه** |
| `ai list` (با Reply روی پیام کاربر) | مجاز کردن همان کاربر برای AI در همان گروه |
| `ai list x` (با Reply روی پیام کاربر) | حذف مجوز همان کاربر در همان گروه |

قواعد گفت‌وگو:

* فقط وقتی AI گروه **روشن** باشد و پیام کاربر **Reply** باشد و **متن** داشته باشد؛
  پیام بدون Reply، پیام بدون متن (مدیا/استیکر) و پیام‌های خودِ حساب ربات نادیده گرفته می‌شوند.
* کاربر مجاز با **`user_id` واقعی** تشخیص داده می‌شود (username هیچ اعتباری ندارد) و مجوز
  **per-group** است؛ مجوز گروه A روی گروه B اثری ندارد.
* کاربر غیرمجاز دقیقاً این متن را می‌گیرد و **هیچ درخواستی به Cloudflare نمی‌رود**:
  «شما مجاز به صحبت کردن با هوش مصنوعی ai fox acod نیستید مالک باید به شما اجازه صحبت بدهد»
* پاسخ مدل در **همان گروه** و به‌صورت **Reply روی همان پیام کاربر** ارسال می‌شود.
* **سهمیه‌ی داخلی روزانه به تفکیک هر گروه** (پیش‌فرض **۵۰۰۰** درخواست برای هر گروه، `ACOD_AI_DAILY_QUOTA`)
  بر اساس **روز UTC**؛ اگر سهمیه‌ی داخلی تمام شود یا خود Cloudflare خطای
  سهمیه/محدودیت (HTTP 429 / کدهای 4006 و 3036 — «daily free allocation of 10,000 neurons»)
  برگرداند، دقیقاً این متن ارسال و سهمیه‌ی آن روز بسته می‌شود:
  «سهمیه روزانه هوش مصنوعی به پایان رسیده است.»
* **کنترل مصرف:** هر درخواست حداکثر `ACOD_AI_MAX_OUTPUT_TOKENS` (پیش‌فرض ۲۵۶) توکن خروجی
  دارد، متن ورودی هر پیام به `ACOD_AI_MAX_INPUT_CHARS` (پیش‌فرض ۸۰۰) کاراکتر بریده می‌شود و
  از تاریخچه فقط `ACOD_AI_HISTORY_PAIRS` (پیش‌فرض ۲) جفت گفت‌وگوی آخر فرستاده می‌شود؛
  پس یک پیام بسیار بلند نمی‌تواند سهمیه را یک‌جا بسوزاند (تست `test_15_…`).
* **هیچ‌کدام از این دستورها در PV کار نمی‌کنند** و مسیر PV (معرفی، منو، کاربران PV،
  «تعداد اعضا»/«لیست اعضا») دست‌نخورده است.

نکته‌ی مالکیت: مالک سراسری همان مالکِ «ai cod» است و به‌صورت پیش‌فرض خودش هم می‌تواند با AI
صحبت کند (`ACOD_AI_OWNER_ALLOWED=0` برای غیرفعال‌کردن این رفتار). مجوز کاربران با
`ai list` باز و با `ai list x` بسته می‌شود و هیچ کاربری با username نمی‌تواند خودش را مجاز کند.

نصب/تنظیم: `CLOUDFLARE_ACCOUNT_ID` و `CLOUDFLARE_API_TOKEN` در `.env`. اگر تنظیم نشده باشند،
دستورهای مدیریتی کار می‌کنند ولی هنگام گفت‌وگو پیام «تنظیم نشده» برگردانده می‌شود و هیچ
درخواست شکست‌خورده‌ای ارسال نمی‌شود.

---

### قالب پیام (دقیقاً همان چیزی که خواسته شد)

```
🦊 ❂ | 𝗮𝗰𝗼𝗱 .𝗮𝗶 #plus            ← داخل «نقل‌قول شیشه‌ای» (MessageEntityBlockquote)

🧑‍💻 : انجمن برنامه نویسی روباه در سروش پلاس    ← Bold
ساخت و طراحی سایت و برنامه                       ← Bold
ساخت ربات های سروش پلاس                          ← Bold
برای دریافت خدمات و محصولات بیشتر                ← Bold
از کانال زیر برنامه را نصب کنید                  ← Bold
https://splus.ir/Orderawebsite                   ← بدون تغییر، بدون entity
```

نرمال‌سازی دستورها: فاصله‌های اضافی، بزرگی/کوچکی حروف لاتین، «ك» عربی و نیم‌فاصله («کدرز») در
نظر گرفته می‌شود؛ ولی متن داخل پیام (مثل «ai cod» در وسط جمله) دستور شمرده نمی‌شود.

---

## ۶. حافظه‌ی دائمی و ثبت اتمیک

اسکیمای SQLite در `storage.py`:

```sql
CREATE TABLE global_owner (
    slot               INTEGER PRIMARY KEY CHECK (slot = 1),  -- فقط یک مالک، همیشه
    user_id            INTEGER NOT NULL UNIQUE,               -- شناسه یکتای حساب
    username           TEXT,
    display_name       TEXT,
    claimed_chat_id    INTEGER NOT NULL,
    claimed_message_id INTEGER,
    claimed_at         TEXT NOT NULL
);
CREATE TABLE owner_attempts (...);   -- تاریخچه‌ی همه‌ی تلاش‌ها

CREATE TABLE pv_users (              -- کاربرانی که در PV پیام داده‌اند (آمار)
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL UNIQUE,   -- یکتا ⇒ هر کاربر فقط یک‌بار
    username      TEXT,
    display_name  TEXT,
    first_seen_at TEXT NOT NULL
);

-- هوش مصنوعی گروه‌ها: همه‌ی وضعیت‌ها per-group
CREATE TABLE ai_group_state (        -- روشن/خاموش بودن AI هر گروه
    chat_id    INTEGER PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE ai_allowed_users (      -- کاربران مجاز هر گروه (کلید = chat_id + user_id)
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    username     TEXT,
    display_name TEXT,
    added_at     TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE ai_usage (              -- مصرف روزانه‌ی هر گروه (day بر اساس UTC)
    chat_id  INTEGER NOT NULL,
    day      TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, day)
);
```

بنابراین روشن/خاموش بودن، مجوز کاربران و سهمیه‌ی مصرف **همه per-group** هستند و با ری‌استارت
از بین نمی‌روند (`test_16_state_survives_restart`).

`BEGIN IMMEDIATE` + کلید اصلی `slot=1` باعث می‌شود اگر دو پیام `ai cod` **هم‌زمان** (حتی در دو
پروسه‌ی جدا) برسند، **فقط یکی** ثبت شود و دومی با `IntegrityError` به‌درستی رد شود.

مدیریت مالک از خط فرمان:

```bash
python manage.py owner            # نمایش مالک فعلی
python manage.py attempts         # تاریخچه‌ی تلاش‌ها
python manage.py export           # خروجی JSON
python manage.py reset-owner --yes  # فقط برای تست
```

---

## ۷. تست‌ها

```bash
./run_all_tests.sh          # یا: python -m unittest discover -s tests -t . -v   (161 تست)
```

خروجی واقعی اجرای تست‌ها در همین محیط:

```
Ran 165 tests in 0.55s
OK
```

تست‌های **Phase 7** (هوش مصنوعی گروه‌ها):

| خواسته | تست |
| --- | --- |
| `ai online` توسط مالک → روشن شدن همان گروه | `test_1_owner_enables_ai_for_group` |
| `ai online` توسط کاربر معمولی → بدون تغییر | `test_2_non_owner_cannot_enable` |
| `ai of` توسط مالک → خاموش شدن | `test_3_owner_disables_ai` |
| `ai list` با Reply → مجاز شدن همان `user_id` | `test_4_owner_allows_user_by_reply` |
| `ai list` بدون Reply → مجاز نشود | `test_5_without_reply_nothing_is_allowed` |
| `ai list` توسط کاربر معمولی → مجاز نشود | `test_6_non_owner_cannot_allow_anyone` |
| `ai list x` با Reply → حذف مجوز | `test_7_owner_revokes_permission` |
| کاربر غیرمجاز + Reply → پیام دقیق + بدون فراخوانی API | `test_8_unauthorized_user_gets_denial_without_api_call` |
| کاربر مجاز + Reply → فراخوانی API + Reply در همان گروه | `test_9_authorized_user_gets_ai_reply` |
| پیام بدون Reply → بدون فراخوانی API | `test_10_message_without_reply_never_calls_api` |
| PV → هیچ قابلیت AI | `test_11_pv_never_triggers_ai_features` (+۲ تست دیگر) |
| مجوز/روشن‌بودن/سهمیه‌ی گروه A روی B اثر نکند | `test_12_…` تا `test_12d_…` |
| AI خاموش → بدون فراخوانی API | `test_13_disabled_ai_never_calls_api` |
| سهمیه‌ی داخلی تمام → پیام دقیق + بدون درخواست اضافه | `test_14_internal_quota_exhaustion_blocks_further_requests` |
| خطای سهمیه‌ی Cloudflare → پیام دقیق + بستن سهمیه‌ی روز | `test_14b_cloudflare_quota_error_marks_day_as_exhausted` |
| پیام بسیار بلند سهمیه را یک‌جا نسوزاند | `test_15_long_message_is_truncated_per_request` |
| تاریخچه‌ی محدود | `test_15b_history_is_bounded` |
| بعد از restart وضعیت per-group باقی بماند | `test_16_state_survives_restart` |
| سهمیه‌ی پیش‌فرض هر گروه = ۵۰۰۰ درخواست در روز | `test_default_quota_is_5000_per_group` / `test_group_can_use_exactly_5000_requests_in_a_day` |
| ۵۰۰۰ مستقل per-group و per-day (و بسته‌شدن درخواست ۵۰۰۱اُم) | `test_5000_quota_is_per_group_and_per_day` |
| جریان واقعی چت در مرز سهمیه‌ی روزانه | `test_whole_chat_flow_works_at_boundary_of_daily_quota` |
| سازگاری دیتابیس قدیمی (Phase 6) با جداول AI | `test_old_database_upgrades_cleanly_and_keeps_data` |
| خودکار مجاز نشدن با username / دستورها فقط مالک | `test_17c_user_cannot_self_authorize_with_plain_text` / `test_17d_…` |
| صحت endpoint/پارس پاسخ/تشخیص سهمیه‌ی Cloudflare | `tests/test_ai_client.py` (۱۲ تست، آفلاین) |

تست‌های کلیدی مطابق خواسته‌ی شما:

| خواسته | تست |
| --- | --- |
| «فقط اولین کاربر مالک می‌شود» | `test_1_first_ai_cod_becomes_global_owner_and_gets_brand_message` |
| «کاربر دوم در گروه دیگر نمی‌تواند مالک شود» | `test_2_second_user_in_another_group_cannot_become_owner` |
| «همان مالک در گروه دیگر → مالک جدید نه» | `test_3_same_owner_repeating_does_not_create_new_owner` |
| «کدرز در گروه‌های مختلف کار می‌کند» | `test_9_kodrez_works_in_every_group_for_every_user` |
| ثبت اتمیک (۲۰ نخ) | `test_atomicity_with_threads` |
| ثبت اتمیک (۸ پروسه‌ی هم‌زمان) | `test_atomicity_with_processes` |
| پایداری بعد از ری‌استارت | `test_owner_survives_restart` / `test_owner_persists_after_restart` |
| صحت entityهای Bold/Blockquote و آفست UTF-16 | `tests/test_brand.py` |
| زنجیره‌ی fallback وقتی سرور blockquote را رد کند | `test_fallback_when_server_rejects_blockquote` |
| حالت نقل‌قول واقعی (`quote_text`) | `test_reply_quote_mode_builds_real_quote_reply` |
| «پیام خصوصی → ارسال خودکار معرفی» | `test_10_private_message_triggers_introduction` |
| «PV بدون متن (مدیا) هم پاسخ می‌گیرد» | `test_private_message_without_text_still_triggers` |
| «پاسخ PV فقط به همان کاربر» | `test_private_reply_goes_only_to_the_sender` |
| «ai cod در PV مالک تعیین نمی‌کند» | `test_private_ai_cod_never_sets_owner` / `test_private_ai_cod_does_not_set_owner_but_sends_intro` |
| «outgoing خود ربات → بدون پاسخ (جلوگیری از loop)» | `test_private_outgoing_is_ignored` |
| «پیام گروهی بدون دستور همچنان نادیده» | `test_group_message_without_commands_is_still_ignored` |
| «یکسان‌بودن قالب گروه و PV» | `test_private_and_group_intro_are_byte_identical` |
| «ثبت یک‌بارِ کاربر PV + جلوگیری از تکرار» | `test_11_…` / `test_12_repeated_messages_do_not_duplicate` |
| «نمایش @username / نام نمایشی / کاربر بدون نام» | `test_18` / `test_19` / `test_20_unknown_name_fallback` |
| «شماره‌گذاری چند کاربر» | `test_22_multiple_users_are_numbered_in_order` |
| «تعداد اعضا» (تعداد + فهرست کامل) | `test_23_count_command_shows_count_and_full_list` |
| «لیست اعضا» برای مالک | `test_24_list_command_for_owner` |
| «فرمت دقیق خروجی تعداد اعضا» | `test_36_format_is_exactly_count_blank_line_then_numbered_list` |
| «شماره‌گذاری از ۱ و به ترتیب اولین ثبت» | `test_37_numbering_starts_at_1_in_registration_order` |
| «تعداد اعضا: چند پیام با شماره‌گذاری ادامه‌دار» | `test_43_long_list_is_split_with_continuous_numbering` |
| «لیست اعضا دست‌نخورده» | `test_44_list_command_is_still_unchanged` |
| «کاربر غیرمالک نتواند اجرا کند» | `test_25_non_owner_cannot_use_admin_commands` |
| «پیام گروهی وارد آمار PV نشود» | `test_16_group_messages_never_enter_pv_stats` |
| «تکه‌تکه‌شدن لیست بلند» | `test_29` / `test_30_long_list_is_sent_in_multiple_messages` |
| «اولین پیام PV → معرفی + منو» | `test_1_first_message_sends_intro_then_menu` / `test_1b_menu_content_is_exact` |
| «پیام دوم → بدون معرفی/منو» | `test_2_second_message_does_not_resend_intro_or_menu` |
| «restart → کاربر دوباره new نشود» | `test_3_restart_does_not_make_user_new_again` |
| «تغییر username → همان کاربر» | `test_4_username_change_keeps_user_same` |
| «۶ گزینه → پاسخ دقیق هر کدام» | `test_7_…` تا `test_12_robot_roobah_full_structure` |
| «Bold و نقل‌قول شیشه‌ای در همه‌ی پاسخ‌ها» | `test_13_all_replies_are_bold` / `test_14_all_replies_are_inside_glass_quote` / `test_15_menu_message_is_bold_and_quoted` |
| «صحت آفست UTF-16 با ایموجی 🔹» | `test_16_utf16_offsets_are_correct` |
| «پایداری کاربران PV بعد از ری‌استارت» | `test_15_users_are_persisted_across_restarts` |

تست‌ها **آفلاین** هستند و برای اجرا به حساب واقعی نیازی ندارند (از `tests/fakes.py` استفاده می‌کنند).

---

## ۸. تنظیمات

همه‌ی تنظیمات در `config.py` و با متغیر محیطی قابل تغییرند:

| متغیر | پیش‌فرض | توضیح |
| --- | --- | --- |
| `ACOD_DATA_DIR` | `./data` | محل سشن و دیتابیس |
| `ACOD_PHONE` / `ACOD_PASSWORD` | — | لاگین غیرتعاملی |
| `ACOD_OWNER_COMMAND` | `ai cod` | متن دستور مالک |
| `ACOD_KODREZ_COMMAND` | `کدرز` | متن دستور معرفی |
| `ACOD_GROUPS_ONLY` | `1` | دستورها (`ai cod`/`کدرز`) فقط در گروه پردازش شوند (بی‌اثر روی PV) |
| `ACOD_PRIVATE_AUTO_REPLY` | `1` | هر پیام خصوصی ورودی → ارسال خودکار پیام معرفی |
| `ACOD_PV_COUNT_COMMAND` | `تعداد اعضا` | متن دستور شمارش کاربران PV |
| `ACOD_PV_LIST_COMMAND` | `لیست اعضا` | متن دستور فهرست کاربران PV |
| `ACOD_PV_UNKNOWN_NAME` | `کاربر بدون نام` | نام جایگزین وقتی نام کاربر خالی است |
| `ACOD_MAX_MESSAGE_CHARS` | `3500` | سقف طول هر پیام (برای تکه‌تکه‌کردن لیست بلند) |
| `ACOD_QUOTE_MODE` | `entity` | `entity` (نقل‌قول شیشه‌ای داخل پیام) / `reply` (ریپلای + quote_text) / `off` |
| `CLOUDFLARE_ACCOUNT_ID` | — | شناسه‌ی حساب Cloudflare (برای AI؛ داخل کد نیست، فقط `.env`) |
| `CLOUDFLARE_API_TOKEN` | — | توکن Workers AI (برای AI؛ داخل کد نیست، فقط `.env`) |
| `ACOD_AI_MODEL` | `@cf/zai-org/glm-4.7-flash` | مدل Workers AI |
| `ACOD_AI_DAILY_QUOTA` | `5000` | سهمیه‌ی داخلی روزانه‌ی **هر گروه** (روز UTC، مستقل از گروه‌های دیگر) |
| `ACOD_AI_MAX_OUTPUT_TOKENS` | `256` | سقف توکن خروجی هر پاسخ |
| `ACOD_AI_MAX_INPUT_CHARS` | `800` | حداکثر طول متن ورودی هر پیام (مازاد بریده می‌شود) |
| `ACOD_AI_HISTORY_PAIRS` | `2` | تعداد جفت گفت‌وگوی فرستاده‌شده به مدل (۰ = بدون تاریخچه) |
| `ACOD_AI_TIMEOUT` | `45` | مهلت پاسخ مدل (ثانیه) |
| `ACOD_AI_OWNER_ALLOWED` | `1` | مالک سراسری خودکار مجاز باشد؟ |

---

## ۸.۵ امنیت و Secretها (مهم)

این ریپازیتوری **هیچ** شماره تلفن، کد ورود، رمز ۲مرحله‌ای، فایل سشن یا اطلاعات
احراز هویت ندارد و این موارد در `.gitignore` مسدود شده‌اند:

| مورد | وضعیت |
| --- | --- |
| فایل سشن (`data/*.session`) | در `.gitignore` |
| دیتابیس رانتایم (`*.sqlite3`, `data/`) | در `.gitignore` |
| `.env` و `.env.*` (به‌جز `.env.example`) | در `.gitignore` |
| لاگ‌ها، کلیدها (`*.key`, `*.pem`)، `*.log`, `logs/` | در `.gitignore` |

قبل از هر commit مطمئن شوید `git status` هیچ فایل سشن/دیتابیسی نشان نمی‌دهد. اگر
فایل سشن به‌اشتباه منتشر شد، فوراً از داخل اپ سروش پلاس «پایان همه‌ی نشست‌ها» را بزنید.

---

## ۹. محدودیت‌ها و نکات صادقانه

1. **کتابخانه غیررسمی است.** `SPlusthon` یک فورک Telethon برای سروش پلاس است (نه SDK رسمی سروش).
   چون سروش Bot Token ارائه نمی‌دهد، «اتصال به‌عنوان حساب کاربری» عملاً تنها راه بدون واسطه است.
   اگر API رسمی ربات سروش (`bot.sapp.ir`) را ترجیح می‌دهید، آن روش نیازمند توکن ربات و واسطه است
   که شما آن را نمی‌خواهید.
2. **درباره‌ی «نقل‌قول شیشه‌ای»:** سازنده‌ی `messageEntityBlockquote` و مکانیزم
   `quote_text` هر دو در TL Schema واقعی سروش پلاس (لایه ۱۸۲) وجود دارند و در پروژه استفاده
   شده‌اند؛ ولی اینکه رابط کاربری سروش پلاس این entity را دقیقاً با ظاهر «شیشه‌ای» رندر کند
   به **نسخه‌ی اپ** بستگی دارد. برای همین سه حالت پشت سر هم امتحان می‌شود
   (`blockquote+bold` → `bold` → متن ساده) و نتیجه در لاگ نوشته می‌شود:
   `پیام معرفی ارسال شد (حالت: blockquote+bold)`.
   اگر در عمل حالت اول رد شد، کافی است لاگ را ببینید؛ هیچ‌چیز به‌صورت جعلی شبیه‌سازی نشده است.
3. **حالت `reply`** (نقل‌قول واقعی MTProto با `quote_text`) نیاز دارد پیام مرجعی وجود داشته باشد
   که تیتر داخلش باشد؛ چون در سناریوی این ربات چنین پیامی وجود ندارد، پیش‌فرض روی `entity` است و
   حالت `reply` به‌عنوان گزینه‌ی اختیاری (`sender.send(..., quote_from_msg_id=…)`) پیاده شده است.
4. **حساب کاربری:** ربات با هویت شما پیام می‌فرستد؛ مسئولیت استفاده و رعایت قوانین سروش پلاس با
   خودتان است. سشن را محرمانه نگه دارید و از حساب اختصاصی استفاده کنید.
5. **لینک** `https://splus.ir/Orderawebsite` عیناً و بدون هیچ entity/تغییری ارسال می‌شود
   (تست `test_link_is_untouched`).
6. **هوش مصنوعی به سهمیه‌ی حساب Cloudflare شما وابسته است.** سهمیه‌ی داخلی روزانه (per-group) از
   خرج‌شدن بی‌رویه جلوگیری می‌کند، ولی سقف واقعی همان سهمیهٔ رایگان/پلن حساب Cloudflare است؛
   اگر آن تمام شود، متن «سهمیه روزانه هوش مصنوعی به پایان رسیده است.» ارسال و آن روز بسته می‌شود.
   کدهای خطای واقعی Cloudflare در عمل ‎4006‎ (و در مستندات ‎3036‎) با HTTP 429 هستند؛ کد پروژه
   هم با کد، هم با HTTP 429 و هم با کلیدواژه‌های پیام خطا تشخیص می‌دهد.
7. **تاریخچه‌ی گفت‌وگو در حافظه است، نه دیتابیس** (عمداً): با ری‌استارت پاک می‌شود، ولی
   روشن‌بودن گروه، مجوز کاربران و مصرف روزانه در SQLite می‌مانند.

---

## ۱۰. عیب‌یابی

| مشکل | راه‌حل |
| --- | --- |
| `ImportError: No module named 'aiohttp'` | `pip install aiohttp` (در Termux: `pkg install clang libffi openssl` سپس دوباره) |
| `Wrong code. Retry.` هنگام ورود | کد منقضی شده؛ دوباره اجرا کنید |
| `SessionPasswordNeededError` | رمز دو مرحله‌ای را وارد کنید (`ACOD_PASSWORD`) |
| ربات به پیام‌های گروه پاسخ نمی‌دهد | حسابِ ربات باید عضو گروه باشد و پیام «ورودی» و دقیقاً `ai cod` یا `کدرز` باشد (`GROUPS_ONLY`) |
| به پیام خصوصی پاسخ نمی‌دهد | `ACOD_PRIVATE_AUTO_REPLY` باید `1` باشد و پیام «ورودی» باشد (نه ارسالیِ خود ربات) |
| کاربر بعد از پیام اول پاسخ نمی‌گیرد | رفتار عمدی است: معرفی/منو فقط یک‌بار؛ در پیام‌های بعدی فقط ۶ گزینه‌ی منو پاسخ دارند |
| متن منو/پاسخ‌ها را باید عوض کنم | در `brand.py` بخش `MENU_*` و `PV_REPLIES` (قالب‌بندی خودکار حفظ می‌شود) |
| `پیام معرفی در همه‌ی حالت‌ها ناموفق بود` | لاگ تلاش‌ها را ببینید؛ معمولاً مجوز ارسال در گروه یا محدودیت موقت سرور است |
| مالک اشتباهی ثبت شده | `python manage.py reset-owner --yes` (فقط برای تست) |
| `⚠️ هوش مصنوعی تنظیم نشده است` | `CLOUDFLARE_ACCOUNT_ID` و `CLOUDFLARE_API_TOKEN` در `.env` پر نشده‌اند |
| «سهمیه روزانه … به پایان رسیده است.» | سهمیه‌ی داخلی روز (`ACOD_AI_DAILY_QUOTA`) یا سهمیه‌ی Cloudflare تمام شده؛ تا ۰۰:۰۰ UTC صبر کنید |
| AI جواب نمی‌دهد | باید AI همان گروه روشن باشد (`ai online`)، پیام **Reply** باشد و کاربر با `ai list` مجاز شده باشد |
| دستور `ai online` اثری ندارد | فقط **مالک سراسری** اجازه دارد و فقط در **گروه** (در PV اجرا نمی‌شود) |
| پاسخ AI اشتباه/قطعی است | مدل را با `ACOD_AI_MODEL` عوض کنید و `ACOD_AI_HISTORY_PAIRS=0` را امتحان کنید |
