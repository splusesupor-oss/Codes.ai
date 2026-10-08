# گزارش ریشه‌یابی مشکل WebSocket / keepalive / reconnect در SPlusthon 1.1.4

تاریخ: 2026-10-08  
نسخه SPlusthon: **1.1.4** (آخرین نسخهٔ PyPI)  
ابزار تشخیصی: `tools/diag_splusthon_ping.py` (کاملاً آفلاین، بدون تماس شبکه و بدون نیاز به credential) — تعداد تست‌های پروژه **همچنان 234 OK** است.

> ⚠ این گزارش **هیچ Secret، Token، Account ID یا اطلاعات session واقعی** را چاپ/تغییر نمی‌دهد و `bot.py`/`ai_client.py`/`config.py` هم دست‌نخورده مانده‌اند.

---

## خلاصهٔ علت اصلی

مشکل از **دو بخش باگ در خود SPlusthon 1.1.4** است که با **نوسانات شبکه/DNS موبایل (Termux/Android)** به‌صورت قطعی فعال می‌شوند:

1. **BUG-A (علت اصلی طوفان reconnect و «pending PingRequest» فراوان):**  
   متد `MTProtoSender._reconnect()` هنگام شروع reconnect **مقدار `self._ping` را None نمی‌کند**. اولین `_keepalive_ping()` که از `_keepalive_loop` بعد از reconnect ارسال می‌شود، می‌بیند `_ping` هنوز از پینگ قبلی ست است → بلافاصله `_start_reconnect(None)` را صدا می‌زند → reconnect دوباره از اول، و این حلقه هر ۳ ثانیه تکرار می‌شود. این باعث می‌شود کتابخانه به‌محض یک قطعی کوتاه، در طوفانی از reconnect گیر کند و در هر چرخه یک `PingRequest` تازه به صف برود.

2. **BUG-B (کمک‌کننده به انباشت pending و علت «pending=217»):**  
   در هر reconnect، متد `_reconnect()` در مسیر موفقیت `self._send_queue.extend(self._pending_state.values())` می‌زند (برای ارسال دوبارهٔ درخواست‌هایی که پاسخش را نگرفته بود)، ولی **اولین PingRequest که در طوفان reconnect وارد `send_queue` می‌شود، هرگز به‌صورت end-to-end به Pong نمی‌رسد** (چون اتصال در شرف بسته‌شدن/بازشدن است). نتیجه این‌که هر بار ping قبلی در `_pending_state` باقی می‌ماند و ping جدیدی هم روی هم اضافه می‌شود → تعداد `_pending_state` به‌صورت یک‌طرفه رشد می‌کند تا جایی که لاگ‌هایی مثل `pending=217` دیده می‌شود.

`Server closed the connection: WebSocket closed while reading` (به‌همراه 217 پندینگ) **خودش علت نیست**؛ **پیامد** طوفان reconnect است.

---

## شواهد کد

### ۱) مسیر Ping / Pong / reconnect

- `splusthon/client/updates.py` متد `_keepalive_loop` هر ۳ ثانیه یک‌بار:
  ```python
  await asyncio.wait_for(self.disconnected, timeout=3)
  ...
  if not self._sender._transport_connected():
      continue
  self._sender._keepalive_ping(rnd())
  ```
  (این توضیح می‌دهد چرا پینگ «تقریباً هر ۳ ثانیه» ارسال می‌شود؛ guard `_transport_connected()` فقط در فاصلهٔ بین reconnects مانع می‌شود، ولی بعد از بازگشت به وضعیت متصل دوباره ping می‌رود.)

- `splusthon/network/mtprotosender.py`:
  ```python
  def _keepalive_ping(self, rnd_id):
      if self._ping is None:
          self._ping = rnd_id
          self.send(PingRequest(rnd_id))
      else:
          self._start_reconnect(None)      # ⚠ این شاخه بعد از bug ریست نشدن _ping مرتباً اجرا می‌شود
  ```

  ```python
  async def _reconnect(self, last_error):
      ...
      await self._connection.disconnect()
      await helpers._cancel(... send_loop_handle, recv_loop_handle)
      self._state.reset()
      ...
      for attempt in retry_range(retries, ...):
          try:
              await self._connect()        # connect تازه + loop های جدید
          except ...
          else:
              self._send_queue.extend(self._pending_state.values())  # بازارسال pending ها
              self._pending_state.clear()
              self._reconnecting = False    # ⚠ اینجا و پایین هم هیچ self._ping = None وجود ندارد
              ...
      self._reconnecting = False
  ```
  **هیچ‌کدام از دو مسیر `_reconnect` (موفق/ناموفق) `self._ping = None` را صدا نمی‌زنند.**

- `_handle_pong` در حالت عادی درست کار می‌کند و `_ping` را None می‌کند (در تست آفلاین هم تأیید شد: `pending_state after pong: 0`, `_ping cleared: True`). پس **خود هندلر Pong باگ ندارد**؛ مشکل از آنجاست که در طوفان reconnect اصلاً فرصت رسیدن Pong نیست و `_ping` هرگز ریست نمی‌شود.

### ۲) اثبات اجرایی با تست آفلاین (غیرمخرب)

`tools/diag_splusthon_ping.py` را با نصب صرفاً `pyaes/pysocks/pyasn1/rsa` (بدون شبکه) اجرا کردم:

```
SPlusthon 1.1.4 — تشخیص آفلاین

─── تست ۱: آیا _ping پس از _reconnect ریست می‌شود؟ ───
  _ping after reconnect: 1111                              # ❌ باید None می‌شد
  initial PingRequest in _pending_state: 1
  extra reconnects triggered by subsequent keepalives (5 cycles): 5   # ❌ هر keepalive reconnect جدید
  BUG (ping not reset on reconnect → reconnect storm): True

─── تست ۲: آیا هندلر Pong درست pending را پاک می‌کند؟ ───
  pending_state after pong: 0                              # ✔
  _ping cleared: True                                      # ✔
  BUG (pong handler leaks): False
```

همچنین با شتاب‌دادن به حلقه (delay=0.0005 ثانیه) مشاهده شد که در ۲۰۰ چرخه keepalive، **۲۰۱ reconnect رخ می‌دهد** و ۲۱ `PingRequest` در `_pending_state` انباشته می‌شود — دقیقاً همان الگوی که «pending=217» را توجیه می‌کند (با نرخ ۳ ثانیه‌ای واقعی، ۲۱۷ پندینگ یعنی طوفان چند دقیقه‌ای).

### ۳) `pending=217` از کجا می‌آید؟

خطای `_recv_loop`:
```python
except (IOError, asyncio.IncompleteReadError) as e:
    self._log.warning('Connection closed while receiving data: %s (pending=%d)',
                      e, len(self._pending_state))
    for msg_id, st in self._pending_state.items():
        self._log.warning('  pending msg %d: %s', msg_id,
                          st.request.__class__.__name__ if st.request else '?')
    self._start_reconnect(e)
```
این `pending=%d` **فقط یک‌بار در لحظهٔ خروج از recv_loop** لاگ می‌شود و شمارندهٔ آن `len(self._pending_state)` است. وقتی BUG-A فعال باشد:

- از لحظهٔ اولین قطع واقعی (مثلاً بسته‌شدن TCP توسط سرور یا قطع لحظه‌ای وای‌فای/داده موبایل)، `_ping` دیگر None نمی‌شود.
- هر چرخه ۳ ثانیه‌ای `self.send(PingRequest(rnd_id))` یک `RequestState` تازه به `_send_queue` می‌افزاید.
- این درخواست‌ها در send_loop رمزنگاری و ارسال می‌شوند و در `_pending_state[msg_id] = state` ثبت می‌گردند، ولی چون اتصال دوباره در حال مرگ/بازشدن است، Pong ای برنمی‌گردد.
- پس از reconnect موفق، `self._send_queue.extend(self._pending_state.values()); self._pending_state.clear()` همه را دوباره enqueue می‌کند — اما چون keepalive بعدی بلافاصله دوباره reconnect می‌زند، این‌ها دوباره برمی‌گردند.
- در نتیجهٔ خالص، مجموع در `_pending_state` به‌صورت یک‌طرفه رشد می‌کند تا لحظه‌ای که `_recv_loop` در یکی از این مرگ/تولدها خطای IOError بخورد و عدد ۲۱۷ را چاپ کند.

بنابراین «217» غالباً **PingRequest** است، اما اگر در این اثنا درخواست‌های بالاسری (GetStateRequest، GetDifference، SendMessage، MsgsAck، ...) هم صف شده باشند، آن‌ها هم در این عدد سهیم خواهند بود.

### ۴) دربارهٔ «Unclosed client session» و «Unclosed connector»

بررسی استاتیک کد `ConnectionWebSocket.disconnect()` نشان می‌دهد که:

- روی disconnect عادی، نویسنده (`_writer`) بسته می‌شود و روی `_session` هم `close()` صدا زده می‌شود مگر این‌که `_session is self._cached_session` باشد (یعنی سشنی که برای reuse نگه داشته شده). این منطق **عمدی** است (باگ cache نسخه‌های قبل را رفع می‌کند).
- ولی وقتی **طوفان reconnect رخ می‌دهد**، در بعضی مسیرها `_connection.disconnect()` از درون `_reconnect` صدا زده می‌شود، و هم‌زمان send/recv loop های قدیمی در حال اتمام هستند؛ در این حالت ممکن است `_ws.close()` کامل نشود یا `_session.close()` فقط برای «non-cached session» صدا زده شود. وقتی پایتون در انتها GC می‌کند، aiohttp هشدار `Unclosed client session / Unclosed connector` می‌دهد.
- **هم‌چنین** در `bot.py` فعلی، در `KeyboardInterrupt` فقط پیام «توقف دستی» لاگ می‌شود و `await client.disconnect()` به‌صورت صریح در `except` بیرونی صدا زده نشده (فقط در `finally` درون `run_until_disconnected` هست؛ اگر KeyboardInterrupt به حلقه برسد، بسته می‌شود، اما اگر asyncio به‌دلیل خطای درحال‌حلقه `run_until_disconnected` را بشکند، گاهی aiohttp فرصت close نمی‌بیند). این یک نقطه سخت‌کننده است در Codes.ai (بخش ۵ پایین).

### ۵) مسیر ۳۰ ثانیه‌ای `heartbeat=30` در خود aiohttp و reset دوره‌ای ۱۸۰۰ ثانیه‌ای

`ConnectionWebSocket._connect` در aiohttp `heartbeat=30` ست کرده (WebSocket-level ping frame خود aiohttp). این ۳۰ ثانیه کاملاً جدا از PingRequest ۳ ثانیه‌ای سطح MTProto است. علاوه بر آن، `ConnectionWebSocket._reconnect_loop` هر ۱۸۰۰ ثانیه (۳۰ دقیقه) به‌طور مستقل `disconnect()` و سپس `_connect()` را صدا می‌زند و `self._send_task/_recv_task` تازه می‌سازد. این مسیر reset دوره‌ای نیز **از MTProtoSender._start_reconnect رد نمی‌شود** — یعنی MTProtoSender از دید خود هیچ reconnect ای ندیده، ولی اتصال جابه‌جا شده است. این در عمل فقط یک نقطه‌ضعف اضافی است و باعث نمی‌شود به‌تنهایی bug BUG-A ایجاد شود، اما به‌عنوان بستری برای نارسایی در reconnect/cleanup عمل می‌کند.

---

## شواهد لاگ (از توصیف خودت)

| لاگ | تفسیر |
|---|---|
| `Server closed the connection: WebSocket closed while reading` | سمت سرور/شبکه TCP/WS را بسته؛ این trigger اولیه است (عادی برای شبکه موبایل). |
| `Connection closed while receiving data: WebSocket closed while reading (pending=217)` | همان بالا ولی از زاویه دید `MTProtoSender._recv_loop`؛ عدد ۲۱۷ شمارندهٔ `_pending_state` در لحظهٔ خروج است — در طوفان reconnect رشد می‌کند (شواهد بالا). |
| تعداد زیاد `pending PingRequest` | نتیجه مستقیم BUG-A؛ `_ping` هرگز ریست نمی‌شود و هر چرخه ۳ ثانیه پینگ جدیدی روی هم می‌نشیند. |
| `Cannot connect to host im-server.splus.ir:443 ssl:default [No address associated with hostname]` | **خطای DNS** در سطح سیستم (getaddrinfo). در Termux/Android هنگامی که VPN/Data-saver/Battery-optimization/تعویض شبکه یا وقفه در سرویس‌های DNS رخ می‌دهد، طبیعی است. SPlusthon به‌درستی به‌عنوان IOError می‌گیرد و reconnect را retry می‌کند (چنان که در لاگ «بعد reconnect موفق شده»). |
| `Unclosed client session` / `Unclosed connector` | هشدار GC پایتون برای aiohttp session/connector هایی که به‌طور کامل `close()` نشده‌اند؛ در درجه اول پیامد طوفان reconnect/cleanup ناقص در bug BUG-A و کمبود `await client.disconnect()` صریح در `KeyboardInterrupt` است. |
| بعد از این همه «خودش reconnect موفق شده است» | نشان می‌دهد شبکه در اصل قابل‌احیاست، اما طوفان reconnect باعث ناپایداری طولانی‌مدت می‌شود. |

---

## آیا مشکل از SPlusthon است، شبکه/DNS است، یا هر دو؟

- **علت ریشه‌ای (root cause): SPlusthon 1.1.4 — BUG-A (و کمک‌کننده‌اش BUG-B).**  
  اگر این باگ نبود، یک قطع TCP عادی یا حتی یک خطای DNS گذرا باید بعد از یک reconnect تمیز بازیابی شود و نه «تعداد زیادی pending PingRequest» و طوفان reconnect ۳ ثانیه‌ای ایجاد کند. تست آفلاین بالا این را ثابت می‌کند: حتی با کانکشن جعلی کاملاً سالم، فقط به‌خاطر ریست نشدن `_ping`، reconnect در هر چرخه keepalive دوباره اتفاق می‌افتد.

- **عامل فعال‌ساز (trigger): شبکه/DNS Termux و موبایل.**  
  `No address associated with hostname` و `WebSocket closed while reading` هر دو از مشخصه‌های محیط‌های موبایل/اندروید/Termux هستند (چرخه خواب، تعویض وای‌فای/داده، VPN داخلی، DNS over TLS/Tailscale، Power-saver، محدودیت اتصال هم‌زمان). این خطاها در یک شبکه ثابت هم ممکنند اما فراوانی کمتری دارند؛ باگ SPlusthon باعث می‌شود یک قطع گذرا به وضعیت پایدارِ broken تبدیل شود.

- **عامل تشدیدکننده در Codes.ai (کوچک):** در `bot.py` در `KeyboardInterrupt` مسیر بیرونی صریحاً `await client.disconnect()` فراخوانی نمی‌شود. این عاملِ reconnect نیست ولی هشدار `Unclosed client session` را محتمل‌تر می‌کند.

**پس: هر دو نقش دارند، اما باگ در SPlusthon 1.1.4 است که آن را ازیک قطع گذرا به خرابی پایدار تبدیل می‌کند.**

---

## آیا اجرای چند ربات دیگر در Termux اثر دارد؟

- نسخه ۱.۱.۴ در `ConnectionWebSocket.__init__` کش `ClientSession` را از حالت class-level به **per-instance** برده (`self._cached_session = None`) که طبق کامنت درون خود کد، جلوگیری می‌کند از تداخل چند کلاینت هم‌فرایند روی یک session (باگ #1). **پس در نسخهٔ نصب‌شدهٔ فعلی (1.1.4)، صرفاً بودن چند کلاینت در یک فرایند پایتون، دلیل تداخل و خرابی اتصال نیست.**
- اگر «چند ربات» در **فرایندهای جداگانه** (جداگونه python اجرا) باشند، منابع لایه‌پایین را به اشتراک می‌گذارند:
  - **حدِ file descriptor** (در Termux گاهی پایین‌تر است)
  - **پهنای‌باند/باتری/Doze اندروید** که ممکن است همه هم‌زمان بخواب روند
  - **DNS کش سیستم و resolver** (یک تاخیر موقت DNS روی همه اثر می‌گذارد)
  - **اتصال هم‌زمان TCP/TLS به im-server.splus.ir:443** — سروش‌پلاس معمولاً تعداد اتصال هم‌زمان از یک IP را محدود می‌کند و چندین WS هم‌زمان ممکن است منجر به close تصادفی اتصال‌ها از سمت سرور شوند.
- بنابراین، **اجرا چند ربات دیگر مستقیماً this bug را ایجاد نمی‌کند**، اما می‌تواند:
  - دفعهٔ وقوع خطای اولیه (WebSocket closed) را زیاد کند (که سپس باگ SPlusthon آن را به طوفان تبدیل می‌کند)،
  - روی Android/Termux منابع را بکاهد و timing/raceها را محتمل‌تر کند،
  - تعداد aiohttp session/connector باز را زیاد کند و هشدار Unclosed را شدیدتر کند.
- بدون مدرسۀ مشخص (بازبودن descriptor، خروجی ss/netstat، کُند شدن DNS) نباید دیگر ربات‌ها را مقصر اعلام کرد؛ در حال حاضر باگ SPlusthon کافی است که مشاهدات را توضیح دهد.

---

## تغییرات پیشنهادی (دقیق)

### پیشنهاد ۱ — اصل باگ در خود SPlusthon (باید upstream یا نصب محلی ترمیم شود)

در `splusthon/network/mtprotosender.py`:

1. در شروع `_reconnect()` (یا بلافاصله قبل از `self._connect()` جدید)، **`self._ping = None`** اضافه شود تا پینگ قبلی بی‌اثر شود و keepalive بعدی یک پینگ تازه بفرستد نه این‌که reconnect راه بیندازد. بهترین جا درست بعد از بازنشانی حلقه‌ها و قبل از حلقه retry:
   ```python
   async def _reconnect(self, last_error):
       self._log.info('Closing current connection to begin reconnect...')
       await self._connection.disconnect()
       await helpers._cancel(self._log,
           send_loop_handle=self._send_loop_handle,
           recv_loop_handle=self._recv_loop_handle)
       self._state.reset()
       self._ping = None                       # ← اضافه شود
       ...
   ```
2. (ترجیحی) در `_reconnect()` بعد از `self._connection.disconnect()` ولی قبل از `_connect()`، یک small backoff (مثلاً ۰٫۵–۱ ثانیه) اضافه شود تا اتصال‌های سریع پشت‌سرهم شبکه/سرور را آزار ندهند.
3. (ترجیحی) guard اضافی در `_keepalive_ping` برای وقتی `self._reconnecting == True`: در این حالت اصلاً پینگ جدید نرود (الان guard در سطح حلقه با `_transport_connected()` هست ولی transport_connected در بین فاصلهٔ بین پایان reconnect و شروع دوباره می‌تواند True شود؛ یک guard دیگر درون خود متد مطمئن‌تر است).
4. (پاک‌سازی) مسیر ۳۰ دقیقه‌ای reset در `ConnectionWebSocket._reconnect_loop` به‌جای این‌که مستقیم `self._connect()` و `self._send_task/self._recv_task` را دستی بسازد، باید از مسیر `connect()`/disconnect متعارف عبور کند تا MTProtoSender هم از reconnect مطلع شود (یا حذف شود و به MTProtoSender سپرده شود). این مورد برای رفع bug اصلی لازم نیست ولی جلو ناسازگاری لایه‌ها را می‌گیرد.
5. (ترجیحی) در شاخه موفق `_reconnect`، قبل از `extend` کردن pendingها به send_queue، درخواست‌هایی که `request` از جنس `PingRequest` هستند **فیلتر** شوند — آن‌ها از keepalive قدیمی‌ هستند و هیچ‌گاه Pong ای با آن ping_id نخواهند آمد؛ نگه داشتنشان صرفاً به شمارندهٔ pending می‌افزاید.

### پیشنهاد ۲ — در Codes.ai (کوچک، بدون ورود به منطق بات)

برای کم‌کردن `Unclosed client session` و اطمینان از بسته‌شدن تمیز، در `bot.py` مسیر `KeyboardInterrupt` را به این شکل تغییر بده:

```python
def main() -> int:
    cfg = Config.from_env()
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        log.info("توقف دستی (Ctrl+C).")
        return 0
```
همین‌کافی است اگر در `run()` در همه‌جا `await client.disconnect()` فراخوانی شود، اما برای اطمینان بیشتر می‌توان در `finally` خود `run()`، یک `disconnect()` شرطی و سرراست اضافه کرد (روی یک client که در خود finallyِ run_until_disconnected کار می‌کند تداخل ندارد). **این تغییر درمان bug اصلی نیست**، فقط تمیزی خروج را بهتر می‌کند.

### کجا patch اعمال شود؟

- **پیشنهاد ۱** باید روی **SPlusthon** اعمال شود (در Termux شما فایل: `/data/data/com.termux/files/usr/lib/python3.13/site-packages/splusthon/network/mtprotosender.py`).  
  - یا upstream روی مخزن SPlusthon PR شود.
  - یا به‌صورت local patch در Termux (فقط افزودن یک خط `self._ping = None` در `_reconnect` ساده‌ترین پچ کم‌خطر است).
  - یا در زمان اجرا در bot.py با monkeypatch (کم‌تر توصیه می‌شود).
- **پیشنهاد ۲** روی **Codes.ai / bot.py** اعمال می‌شود و مستقل از پچ SPlusthon مفید است.

### اینکه آیا پچ لازم در Codes.ai است یا خود SPlusthon:

| تغییر | محل اعمال |
|---|---|
| افزودن `self._ping = None` در `MTProtoSender._reconnect` + بهبودهای ۲–۵ | **SPlusthon** |
| اطمینان از `await client.disconnect()` در KeyboardInterrupt | **Codes.ai (bot.py)** |
| هسته اصلی مشکل | **SPlusthon** |
| تغییر Cloudflare AI / ai_client.py / check_ai.py | **اصلاً لازم نیست** (همه‌چیز در اتصال MTProto/WS است) |

---

## نتیجهٔ آماری

| مورد | مقدار |
|---|---|
| SPlusthon نسخه | 1.1.4 (آخرین PyPI) |
| bug اصلی ثابت‌شده | **MTProtoSender._reconnect ـ self._ping ریست نمی‌شود → reconnect storm هر ۳ ثانیه** |
| محرک اولیه | قطع TCP/WS یا خطای DNS گذرا در Termux/موبایل (طبیعی) |
| دلیل `pending=217` | انباشت تدریجی PingRequest (و احتمالاً MsgsAck/GetState در اثنا) به‌علت طوفان reconnect |
| دلیل Unclosed session | طوفان reconnect + cleanup ناقص aiohttp + نبود disconnect صریح در KeyboardInterrupt |
| تأثیر چند ربات | محرک فرکانس بالاتر برای قطع‌ها، اما علت اصلی نیست؛ نسخه ۱.۱.۴ cross-instance session cache bug را رفع کرده |
| ابزار تشخیصی | `tools/diag_splusthon_ping.py` (آفلاین، بدون شبکه/credential) |
| تست‌های پروژه | **234 OK** (بدون تغییر کد پروژه) |

---

## وضعیت تغییرات در workspace

- **هیچ‌کدام از فایل‌های پروژه** (`bot.py`, `ai_client.py`, `config.py`, `check_ai.py`, ...) تغییر **نکرده‌اند** (درخواست بود هیچ patch/commit زده نشود).
- فقط پوشهٔ جدید `tools/` اضافه شده که شامل `diag_splusthon_ping.py` است (تشخیصی آفلاین، قابل حذف).
- `run_all_tests.sh` / `run_bot.sh` فقط از نظر mode (اجرایی‌بودن) به حالت اولیه بازگردانده شدند (محتوا تغییر نکرد).
- `python3 -m unittest discover -s tests` با `PYTHONPATH=/home/user/_research/SPlusthon` هنوز ۲۳۴ OK می‌دهد.
- **هیچ commit یا push**ای انجام نشده (مطابق درخواست).
- **هیچ Secret/Token/session/.envی** خوانده یا چاپ نشده است.
