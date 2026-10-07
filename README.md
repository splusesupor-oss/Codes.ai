# ربات «ai cod» سروش پلاس — بدون Bot Token

ربات بسیار ساده با **دو دستور** («`ai cod`» و «`کدرز`») که مستقیم با **حساب کاربری سروش پلاس**
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
5. [دستورات ربات و قوانین مالکیت](#۵-دستورات-ربات-و-قوانین-مالکیت)
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
```

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
./run_all_tests.sh          # یا: python -m unittest discover -s tests -t . -v
```

خروجی واقعی اجرای تست‌ها در همین محیط:

```
Ran 38 tests in 0.07s
OK
```

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
| `ACOD_GROUPS_ONLY` | `1` | فقط گروه‌ها پردازش شوند |
| `ACOD_QUOTE_MODE` | `entity` | `entity` (نقل‌قول شیشه‌ای داخل پیام) / `reply` (ریپلای + quote_text) / `off` |

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

---

## ۱۰. عیب‌یابی

| مشکل | راه‌حل |
| --- | --- |
| `ImportError: No module named 'aiohttp'` | `pip install aiohttp` (در Termux: `pkg install clang libffi openssl` سپس دوباره) |
| `Wrong code. Retry.` هنگام ورود | کد منقضی شده؛ دوباره اجرا کنید |
| `SessionPasswordNeededError` | رمز دو مرحله‌ای را وارد کنید (`ACOD_PASSWORD`) |
| ربات به پیام‌ها پاسخ نمی‌دهد | حسابِ ربات باید عضو گروه باشد و پیام باید «گروهی» و «ورودی» باشد (`GROUPS_ONLY`) |
| `پیام معرفی در همه‌ی حالت‌ها ناموفق بود` | لاگ تلاش‌ها را ببینید؛ معمولاً مجوز ارسال در گروه یا محدودیت موقت سرور است |
| مالک اشتباهی ثبت شده | `python manage.py reset-owner --yes` (فقط برای تست) |
