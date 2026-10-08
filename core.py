"""
منطق ربات: دو دستور گروهی + یک رفتار خودکار در پیام خصوصی.

  ۱) «ai cod»  → اولین کاربری که این دستور را در هر گروهی بفرستد، برای همیشه
                 Global Owner می‌شود؛ هیچ کاربر دیگری در هیچ گروهی نمی‌تواند مالک شود.
                 بعد از ثبت موفق، پیام معرفی (فعال‌سازی) ارسال می‌شود.
                 (فقط در گروه — در پیام خصوصی هرگز مالک ثبت نمی‌شود)

  ۲) «کدرز»    → هر کاربری در هر گروهی این را بفرستد، پیام معرفی با همان قالب
                 (نقل‌قول شیشه‌ای + Bold + لینک دست‌نخورده) ارسال می‌شود.

  ۳) پیام خصوصی (PV) → هر پیام ورودی در چت خصوصی — بدون نیاز به هیچ دستوری و
                 فارغ از متن پیام — پاسخِ همان پیام معرفی را برای همان کاربر می‌گیرد.
                 تشخیص PV با API واقعی کتابخانه انجام می‌شود: `event.is_private`
                 که در `splusthon/tl/custom/chatgetter.py` معادل
                 `isinstance(_chat_peer, types.PeerUser)` است.

نکته‌ی مهم: `groups_only` فقط روی «دستورها» اثر دارد؛ مسیر پیام خصوصی از آن مستقل است.

این ماژول هیچ چیزی از شبکه را مستقیم صدا نمی‌زند؛ فقط از `client` و `event`
استفاده می‌کند، بنابراین با یک کلاینت/ایونت جعلی هم کاملاً قابل تست است.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

import brand
from ai_service import GroupAI
from brand import chunk_lines          # ابزار مشترک تکه‌تکه‌کردن متن (در brand)
from config import Config
from sender import BrandSender, SendReport
from storage import OwnerStore

log = logging.getLogger("acod.core")

_ZWNJ = "\u200c"          # نیم‌فاصله
_ARABIC_PAIRS = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "أ": "ا", "إ": "ا"})


def _base_clean(text: str) -> str:
    """یکسان‌سازی حروف و فاصله‌ها (بدون تصمیم‌گیری درباره‌ی نیم‌فاصله)."""
    text = unicodedata.normalize("NFKC", text).translate(_ARABIC_PAIRS)
    return text.strip().lower()


def normalize_text(text: str) -> str:
    """نرمال‌سازی متن برای مقایسه‌ی منصفانه‌ی دستور.

    * نیم‌فاصله حذف می‌شود («کدرز» == «کدرز»)
    * فاصله‌های تکراری یکی می‌شوند و ابتدا/انتهای متن trim می‌شود
    * حروف عربی/فارسی هم‌شکل یکسان‌سازی می‌شوند («كدرز» == «کدرز»)
    * حروف لاتین کوچک می‌شوند («AI COD» == «ai cod»)
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", _base_clean(text).replace(_ZWNJ, "")).strip()


def _normalization_variants(text: str) -> set[str]:
    """نیم‌فاصله هم «حذف» و هم «فاصله» در نظر گرفته می‌شود تا هر دو حالت بگیرد."""
    base = _base_clean(text)
    return {
        re.sub(r"\s+", " ", base.replace(_ZWNJ, "")).strip(),
        re.sub(r"\s+", " ", base.replace(_ZWNJ, " ")).strip(),
    }


def owner_command_variants(cfg: Config) -> set:
    """همه‌ی املاهای پذیرفته‌شده‌ی دستور مالک: «ai cod» و «ai code» (قابل تغییر با env)."""
    commands = (cfg.owner_command,) + tuple(cfg.owner_command_aliases)
    variants = set()
    for command in commands:
        variants |= _normalization_variants(command)
    return variants


def match_command(text: str, cfg: Config) -> Optional[str]:
    """تشخیص دستور: 'owner' برای «ai cod»/«ai code» و 'kodrez' برای «کدرز»."""
    if not text or not text.strip():
        return None
    given = _normalization_variants(text)
    if given & owner_command_variants(cfg):
        return "owner"
    if given & _normalization_variants(cfg.kodrez_command):
        return "kodrez"
    return None


def match_pv_admin_command(text: str, cfg: Config) -> Optional[str]:
    """تشخیص دستورهای مدیریتی PV: 'count' برای «تعداد اعضا» و 'list' برای «لیست اعضا»."""
    if not text or not text.strip():
        return None
    given = _normalization_variants(text)
    if given & _normalization_variants(cfg.pv_count_command):
        return "count"
    if given & _normalization_variants(cfg.pv_list_command):
        return "list"
    return None


def match_ai_command(text: str, cfg: Config) -> Optional[str]:
    """تشخیص دستورهای AI گروه: online | of | list | listx.

    «ai list x» قبل از «ai list» بررسی می‌شود تا اشتباه match نشود.
    """
    if not text or not text.strip():
        return None
    given = _normalization_variants(text)
    if given & _normalization_variants(cfg.ai_listx_command):
        return "listx"
    if given & _normalization_variants(cfg.ai_list_command):
        return "list"
    if given & _normalization_variants(cfg.ai_online_command):
        return "online"
    if given & _normalization_variants(cfg.ai_of_command):
        return "of"
    return None


def match_menu_option(text: str) -> Optional[str]:
    """تشخیص یکی از ۶ گزینه‌ی منوی PV.

    متن گزینه‌ها در `brand.MENU_OPTIONS` است و مقایسه با همان نرمال‌سازی
    دستورهای دیگر انجام می‌شود (فاصله‌های اضافی، نیم‌فاصله، حروف عربی/فارسی).
    """
    if not text or not text.strip():
        return None
    given = _normalization_variants(text)
    for option in brand.MENU_OPTIONS:
        if given & _normalization_variants(option):
            return option
    return None


def pv_user_label(user, unknown_name: str = "کاربر بدون نام") -> str:
    """نمایش کاربر PV: اگر username دارد «@username» وگرنه نام نمایشی.

    اگر هیچ‌کدام نبود، نام امن «کاربر بدون نام» برگردانده می‌شود.
    """
    username = (getattr(user, "username", None) or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    display_name = (getattr(user, "display_name", None) or "").strip()
    return display_name or unknown_name


def format_pv_user_list(users, unknown_name: str = "کاربر بدون نام") -> list[str]:
    """ساخت خطوط فهرست: «1 : @osine» به ترتیب ثبت."""
    return [
        f"{index} : {pv_user_label(user, unknown_name)}"
        for index, user in enumerate(users, start=1)
    ]


class BotCore:
    """هسته‌ی ربات (مستقل از شبکه و کاملاً تست‌پذیر)."""

    def __init__(
        self,
        cfg: Config,
        store: OwnerStore,
        sender: Optional[BrandSender] = None,
        ai: Optional[GroupAI] = None,
    ):
        self.cfg = cfg
        self.store = store
        self.sender = sender or BrandSender(cfg)
        # سرویس هوش مصنوعی (فقط گروه‌ها) — در تست‌ها با یک کلاینت جعلی تزریق می‌شود
        self.ai = ai or GroupAI(cfg, store, self.sender)

    # ------------------------------------------------------------------ entry
    async def on_new_message(self, client, event) -> None:
        """هندلر اصلی — به events.NewMessage وصل می‌شود."""
        # پیام‌های خودمان (خروجی) نباید پردازش شوند؛ وگرنه حلقه‌ی بی‌پایان می‌شود.
        # این بررسی، پاسخ خودکار PV ربات را هم از پردازش مجدد محافظت می‌کند.
        if getattr(event, "out", False):
            return

        # --- مسیر ۱: پیام خصوصی (PV) → ثبت کاربر + پاسخ خودکار معرفی/آمار ---
        # تشخیص با API واقعی SPlusthon: `is_private` (↔ PeerUser بودن چت).
        if getattr(event, "is_private", None) is True:
            await self._handle_private(client, event)
            return

        # --- مسیر ۲: گروه‌ها → دستورها و هوش مصنوعی ---
        text = getattr(event, "raw_text", None) or ""
        is_group = bool(getattr(event, "is_group", False))
        if not text.strip():
            return

        # (قفلِ سراسری) دو حالت ربات را «قفل» می‌کند:
        #   ۱) هنوز هیچ‌کس با «ai cod» مالک نشده است (پیش‌فرض خاموش)؛
        #   ۲) مالک با «ai cod» دوباره ربات را خاموش کرده (global_suspended=True).
        # در هر دو حالت فقط خود دستور «ai cod» عبور می‌کند تا مالک بتواند:
        #   - اولین‌بار claim کند (حالت ۱)، یا
        #   - دوباره ربات را روشن کند (حالت ۲).
        # همه‌ی پیام‌های دیگر بی‌درنگ نادیده گرفته می‌شوند و هیچ پاسخی فرستاده
        # نمی‌شود (ربات کاملاً بی‌صدا است).
        if not self.store.is_active():
            command = match_command(text, self.cfg)
            if command == "owner":
                await self._handle_owner_command(client, event)
            else:
                log.debug("ربات خاموش است؛ پیام گروهی «%s» نادیده گرفته شد.",
                          text[:40])
            return

        # (۲-الف) دستورهای AI گروه: ai online / ai of / ai list / ai list x
        #         (فقط گروه و فقط مالک سراسری — منطق و بررسی مالک در GroupAI)
        ai_command = match_ai_command(text, self.cfg)
        if ai_command is not None:
            if is_group:
                await self.ai.handle_admin_command(client, event, ai_command)
            return

        # (۲-ب) دستورهای فعلی ربات (بدون تغییر)
        command = match_command(text, self.cfg)
        if command is not None:
            if self.cfg.groups_only and not is_group:
                log.debug("دستور «%s» در چت غیرگروهی نادیده گرفته شد (chat_id=%s)",
                          text, getattr(event, "chat_id", None))
                return
            if command == "owner":
                await self._handle_owner_command(client, event)
            elif command == "kodrez":
                await self._handle_kodrez(client, event)
            return

        # (۲-ج) گفت‌وگو با هوش مصنوعی: فقط Reply در گروهی که AI آن روشن است
        if is_group and getattr(event, "is_reply", False):
            await self.ai.handle_chat(client, event)

    # ------------------------------------------------------- پیام خصوصی (PV)
    async def _handle_private(self, client, event) -> None:
        """مسیر پیام خصوصی:

        ۱) ثبت یک‌بارِ کاربر PV در storage دائمی (کلید = user_id واقعی)
        ۲) «ai cod» از مالک در PV هم ربات را خاموش/روشن می‌کند (اما هرگز claim نمی‌کند)
        ۳) دستورهای مدیریتی مالک سراسری («تعداد اعضا» / «لیست اعضا») — فقط وقتی روشن
        ۴) «اولین پیام» همان کاربر → معرفی (یک‌بار) + منوی انتخاب (یک‌بار)
        ۵) پیام‌های بعدی: اگر یکی از ۶ گزینه‌ی منو بود → فقط پاسخ همان گزینه
        ۶) متن دیگر → پاسخ جدیدی ساخته نمی‌شود و معرفی/منو هم تکرار نمی‌شوند
        """
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        text = getattr(event, "raw_text", None) or ""

        # (۱) ثبت کاربر PV — هر کاربر فقط یک‌بار (کلید: user_id؛ تغییر username
        #     کاربر را «جدید» نمی‌کند). مقدار بازگشتی «اولین پیام» بودن را می‌گوید و
        #     چون در SQLite دائمی ذخیره می‌شود، بعد از restart هم از بین نمی‌رود.
        is_first_message = False
        if sender_id:
            is_first_message = self.store.register_pv_user(
                sender_id,
                username=self._username(event),
                display_name=self._display_name(event),
            )
            if is_first_message:
                log.info("کاربر جدید PV ثبت شد: user_id=%s (تعداد: %s)",
                         sender_id, self.store.count_pv_users())

        # (۲) «ai cod» از مالک در PV — تاگل خاموش/روشن (ولی هرگز claim نمی‌کند)
        if self.store.has_owner() and self.store.is_owner(sender_id) \
                and match_command(text, self.cfg) == "owner":
            new_state = not self.store.is_global_suspended()
            self.store.set_global_suspended(new_state)
            text_resp = brand.BOT_OFF_TEXT if new_state else brand.BOT_ON_TEXT
            log.info("وضعیت ربات از PV تغییر کرد: suspended=%s (مالک %s)",
                     new_state, sender_id)
            self._log_report(await self.sender.send_text(
                client, await self._peer_of(event), text_resp
            ))
            return

        # (۳) دستورهای مدیریتی — فقط مالک سراسری و فقط وقتی ربات روشن
        if sender_id and self.store.is_owner(sender_id):
            if not self.store.is_active():
                log.debug("ربات خاموش است؛ دستور مدیریتی PV از %s نادیده گرفته شد.",
                          sender_id)
                return
            command = match_pv_admin_command(text, self.cfg)
            if command == "count":
                await self._send_pv_count(client, event)
                return
            if command == "list":
                await self._send_pv_list(client, event)
                return

        if not self.cfg.private_auto_reply:
            return

        # در حالت قفل (قبل از claim یا خاموشی کلی)، PV هیچ پیام خودکاری
        # (معرفی/منو/گزینه‌ها) نمی‌فرستد؛ فقط ثبت کاربر انجام می‌شود تا بعداً
        # که مالک ربات را روشن کرد آمار کاربران PV در دسترس باشد.
        if not self.store.is_active():
            log.debug("ربات خاموش است؛ پیام PV از %s نادیده گرفته شد.", sender_id)
            return

        # (۳) اولین پیام این کاربر → معرفی + منو (هرکدام فقط یک‌بار)
        if is_first_message:
            await self._send_intro_and_menu(client, event)
            return

        # (۴) پیام‌های بعدی: یکی از گزینه‌های منو؟ → فقط پاسخ همان گزینه
        option = match_menu_option(text)
        if option:
            await self._send_pv_answer(client, event, option)
            return

        # (۵) متن دیگر: پاسخ جدیدی اختراع نمی‌شود؛ معرفی و منو هم تکرار نمی‌شوند.
        log.debug("پیام PV بدون گزینه‌ی منو از کاربر %s نادیده گرفته شد", sender_id)

    async def _send_intro_and_menu(self, client, event) -> None:
        """اولین پیام کاربر PV: پیام معرفی (فقط یک‌بار) سپس منوی انتخاب (فقط یک‌بار)."""
        log.info("اولین پیام کاربر %s در PV → معرفی + منو",
                 getattr(event, "sender_id", None))
        self._log_report(await self._send_brand(client, event))
        self._log_report(
            await self.sender.send_styled(client, await self._peer_of(event), brand.MENU_TEXT)
        )

    async def _send_pv_answer(self, client, event, option: str) -> None:
        """پاسخ یک گزینه‌ی منو — با همان قالب (نقل‌قول شیشه‌ای + Bold)."""
        text = brand.PV_REPLIES[option]
        log.info("گزینه «%s» از کاربر %s → ارسال پاسخ",
                 option, getattr(event, "sender_id", None))
        self._log_report(
            await self.sender.send_styled(client, await self._peer_of(event), text)
        )

    # ------------------------------------------- آمار کاربران PV (فقط مالک)
    async def _send_pv_count(self, client, event) -> None:
        """«تعداد اعضا» → تعداد کل + فهرست کامل کاربران PV، به این شکل:

            تعداد اعضا : 3

            1 : @osine
            2 : ali
            3 : @elism

        شماره‌گذاری از ۱ و به ترتیب «اولین ثبت» است و اگر لیست بلند شد،
        به چند پیام متوالی تقسیم می‌شود و شماره‌ها ادامه‌دار می‌مانند
        (شماره‌ها قبل از تکه‌تکه‌کردن حساب می‌شوند).
        """
        users = self.store.list_pv_users()
        count = len(users)
        header = f"{self.cfg.pv_count_command} : {count}"
        log.info("دستور «%s» توسط مالک → %s کاربر",
                 self.cfg.pv_count_command, count)

        if not users:
            report = await self.sender.send_text(
                client, await self._peer_of(event), header
            )
            self._log_report(report)
            return

        lines = format_pv_user_list(users, self.cfg.pv_unknown_name)
        # سرتیتر + یک خط خالی + فهرست (طبق فرمت خواسته‌شده)
        chunks = chunk_lines([header, ""] + lines, self.cfg.max_message_chars)
        for report in await self.sender.send_text_chunked(
            client, await self._peer_of(event), chunks
        ):
            self._log_report(report)

    async def _send_pv_list(self, client, event) -> None:
        users = self.store.list_pv_users()
        if not users:
            report = await self.sender.send_text(
                client, await self._peer_of(event), "هنوز کاربری ثبت نشده است."
            )
            self._log_report(report)
            return

        lines = format_pv_user_list(users, self.cfg.pv_unknown_name)
        chunks = chunk_lines(lines, self.cfg.max_message_chars)
        log.info("دستور «%s» → %s کاربر در %s پیام",
                 self.cfg.pv_list_command, len(users), len(chunks))
        for report in await self.sender.send_text_chunked(
            client, await self._peer_of(event), chunks
        ):
            self._log_report(report)

    # ------------------------------------------------------------ دستور ai cod
    async def _handle_owner_command(self, client, event) -> None:
        user_id = int(getattr(event, "sender_id", 0) or 0)
        chat_id = int(getattr(event, "chat_id", 0) or 0)

        result = self.store.claim(
            user_id,
            chat_id=chat_id,
            message_id=getattr(event, "id", None),
            username=self._username(event),
            display_name=self._display_name(event),
        )

        if result.claimed:
            # اولین «ai cod» → مالک می‌شود و ربات روشن می‌ماند (پیام خوش‌آمد/برند)
            self.store.set_global_suspended(False)
            log.info("🏆 مالک سراسری ثبت شد: user_id=%s (chat_id=%s)", user_id, chat_id)
            report = await self._send_brand(client, event)
            self._log_report(report)
            return

        if result.reason == "already_same_owner":
            # مالک دوباره «ai cod» زده است → تاگل خاموش/روشن
            new_state = not self.store.is_global_suspended()
            self.store.set_global_suspended(new_state)
            text = brand.BOT_OFF_TEXT if new_state else brand.BOT_ON_TEXT
            log.info("وضعیت ربات تغییر کرد: suspended=%s (توسط مالک %s)",
                     new_state, user_id)
            self._log_report(await self.sender.send_text(
                client, await self._peer_of(event), text
            ))
            return

        # کاربر دیگری «ai cod» زده است → کاملاً نادیده گرفته می‌شود.
        log.info(
            "کاربر %s تلاش کرد مالک شود؛ نادیده گرفته شد (مالک فعلی: %s).",
            user_id, result.owner.user_id if result.owner else None,
        )

    # ------------------------------------------------------------- دستور کدرز
    async def _handle_kodrez(self, client, event) -> None:
        log.info("دستور «کدرز» از کاربر %s در چت %s",
                 getattr(event, "sender_id", None), getattr(event, "chat_id", None))
        self._log_report(await self._send_brand(client, event))

    # ------------------------------------------------------------------ utils
    async def _send_brand(self, client, event) -> SendReport:
        return await self.sender.send(client, await self._peer_of(event))

    @staticmethod
    async def _peer_of(event):
        getter = getattr(event, "get_input_chat", None)
        if callable(getter):
            try:
                peer = await getter()
                if peer is not None:
                    return peer
            except Exception:  # noqa: BLE001 — در بدترین حالت به chat_id برمی‌گردیم
                log.debug("get_input_chat ناموفق بود؛ از chat_id استفاده می‌شود.")
        return getattr(event, "chat_id", None)

    @staticmethod
    def _username(event) -> Optional[str]:
        sender = getattr(event, "sender", None)
        return getattr(sender, "username", None) if sender is not None else None

    @staticmethod
    def _display_name(event) -> Optional[str]:
        sender = getattr(event, "sender", None)
        if sender is None:
            return None
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        name = f"{first} {last}".strip()
        return name or None

    @staticmethod
    def _log_report(report: SendReport) -> None:
        if not report.ok:
            log.error("ارسال پیام معرفی ناموفق بود: %s | تلاش‌ها: %s",
                      report.error, report.attempts)
