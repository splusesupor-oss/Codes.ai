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


def match_command(text: str, cfg: Config) -> Optional[str]:
    """تشخیص دستور: 'owner' برای «ai cod» و 'kodrez' برای «کدرز»."""
    if not text or not text.strip():
        return None
    given = _normalization_variants(text)
    if given & _normalization_variants(cfg.owner_command):
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


def chunk_lines(lines: list[str], max_chars: int) -> list[str]:
    """تکه‌تکه‌کردن خطوط به پیام‌هایی با طول مجاز (برای جلوگیری از خطای طول پیام).

    یک خط بلندتر از سقف، تنها در پیام خودش می‌آید (وسط خط بریده نمی‌شود).
    """
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        extra = len(line) + (1 if current else 0)   # +1 برای \n
        if current and current_len + extra > max_chars:
            chunks.append("\n".join(current))
            current, current_len = [], 0
            extra = len(line)
        current.append(line)
        current_len += extra

    if current:
        chunks.append("\n".join(current))
    return chunks


class BotCore:
    """هسته‌ی ربات (مستقل از شبکه و کاملاً تست‌پذیر)."""

    def __init__(self, cfg: Config, store: OwnerStore, sender: Optional[BrandSender] = None):
        self.cfg = cfg
        self.store = store
        self.sender = sender or BrandSender(cfg)

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

        # --- مسیر ۲: گروه‌ها → دستورها (بدون هیچ تغییری نسبت به قبل) ---
        text = getattr(event, "raw_text", None) or ""
        if not text.strip():
            return

        command = match_command(text, self.cfg)
        if command is None:
            return

        if self.cfg.groups_only and not getattr(event, "is_group", False):
            log.debug("دستور «%s» در چت غیرگروهی نادیده گرفته شد (chat_id=%s)",
                      text, getattr(event, "chat_id", None))
            return

        if command == "owner":
            await self._handle_owner_command(client, event)
        elif command == "kodrez":
            await self._handle_kodrez(client, event)

    # ------------------------------------------------------- پیام خصوصی (PV)
    async def _handle_private(self, client, event) -> None:
        """مسیر پیام خصوصی:
        ۱) ثبت یک‌بارِ کاربر PV در storage دائمی
        ۲) اگر فرستنده مالک سراسری باشد و دستور مدیریتی بفرستد → آمار اعضا
        ۳) در غیر این صورت → همان پاسخ خودکار پیام معرفی (رفتار قبلی، دست‌نخورده)
        """
        sender_id = int(getattr(event, "sender_id", 0) or 0)

        # (۱) ثبت کاربر PV — هر کاربر فقط یک‌بار؛ پیام‌های بعدی تعداد را زیاد نمی‌کند.
        #     کاربران گروهی اینجا ثبت نمی‌شوند (این تابع فقط در مسیر PV صدا زده می‌شود).
        if sender_id:
            is_new = self.store.register_pv_user(
                sender_id,
                username=self._username(event),
                display_name=self._display_name(event),
            )
            if is_new:
                log.info("کاربر جدید PV ثبت شد: user_id=%s (تعداد: %s)",
                         sender_id, self.store.count_pv_users())

        # (۲) دستورهای مدیریتی — فقط مالک سراسری و فقط در PV
        if sender_id and self.store.is_owner(sender_id):
            command = match_pv_admin_command(
                getattr(event, "raw_text", None) or "", self.cfg
            )
            if command == "count":
                await self._send_pv_count(client, event)
                return
            if command == "list":
                await self._send_pv_list(client, event)
                return

        # (۳) پاسخ خودکار PV (بدون تغییر نسبت به قبل)
        if not self.cfg.private_auto_reply:
            return

        log.info("پیام خصوصی از کاربر %s → ارسال پیام معرفی", sender_id)
        self._log_report(await self._send_brand(client, event))

    # ------------------------------------------- آمار کاربران PV (فقط مالک)
    async def _send_pv_count(self, client, event) -> None:
        count = self.store.count_pv_users()
        text = f"{self.cfg.pv_count_command} : {count}"
        log.info("دستور «%s» توسط مالک → %s", self.cfg.pv_count_command, text)
        report = await self.sender.send_text(client, await self._peer_of(event), text)
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
            log.info("🏆 مالک سراسری ثبت شد: user_id=%s (chat_id=%s)", user_id, chat_id)
            report = await self._send_brand(client, event)
            self._log_report(report)
            return

        if result.reason == "already_same_owner":
            # مالک قبلی دوباره «ai cod» زده است → مالک جدیدی ساخته نمی‌شود.
            log.info("مالک سراسری (%s) دوباره «ai cod» زد؛ مالک جدید ساخته نشد.", user_id)
            if self.cfg.announce_on_owner_repeat:
                self._log_report(await self._send_brand(client, event))
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
