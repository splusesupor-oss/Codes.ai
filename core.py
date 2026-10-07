"""
منطق ربات: دو دستور، طبق دقیق نیازمندی‌ها.

  ۱) «ai cod»  → اولین کاربری که این دستور را در هر گروهی بفرستد، برای همیشه
                 Global Owner می‌شود؛ هیچ کاربر دیگری در هیچ گروهی نمی‌تواند مالک شود.
                 بعد از ثبت موفق، پیام معرفی (فعال‌سازی) ارسال می‌شود.

  ۲) «کدرز»    → هر کاربری در هر گروهی این را بفرستد، پیام معرفی با همان قالب
                 (نقل‌قول شیشه‌ای + Bold + لینک دست‌نخورده) ارسال می‌شود.

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
        if getattr(event, "out", False):
            return

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
