"""
سرویس هوش مصنوعی گروه‌ها (فقط GROUP — هیچ ارتباطی با PV ندارد).

قواعدی که این ماژول پیاده می‌کند:

  * «ai online» / «ai of»  → روشن/خاموش کردن AI برای «همان گروه» (فقط مالک سراسری).
  * «ai list» (با Reply روی پیام کاربر)  → مجاز کردن آن کاربر برای همان گروه.
  * «ai list x» (با Reply روی پیام کاربر) → حذف مجوز همان کاربر در همان گروه.
  * صحبت با AI فقط با «Reply + متن» در گروهی که AI روشن است انجام می‌شود.
  * همه‌ی وضعیت‌ها per-group هستند: enabled، کاربران مجاز و مصرف روزانه.
  * کنترل مصرف: سهمیه‌ی روزانه به تفکیک گروه و بر اساس روز UTC + محدودیت طول ورودی
    و حداکثر توکن خروجی + تاریخچه‌ی کوتاه و محدود.
  * تشخیص مالک از همان سیستم واقعی پروژه (OwnerStore.is_owner) و بر اساس user_id؛
    به username هیچ اعتمادی نمی‌شود.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections import OrderedDict, deque
from typing import Optional

import brand
from ai_client import AIConfigError, AIError, AIQuotaExceeded, CloudflareAI
from config import Config
from sender import BrandSender, resolve_peer
from storage import OwnerStore

log = logging.getLogger("acod.ai")


def utc_day(now: Optional[_dt.datetime] = None) -> str:
    """روز جاری بر اساس UTC (هماهنگ با ریست سهمیه‌ی Cloudflare) به شکل YYYY-MM-DD."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d")


class GroupAI:
    """مدیریت وضعیت و اجرای درخواست‌های AI برای گروه‌ها."""

    def __init__(
        self,
        cfg: Config,
        store: OwnerStore,
        sender: BrandSender,
        ai_client: Optional[CloudflareAI] = None,
        *,
        now_provider=None,
    ):
        self.cfg = cfg
        self.store = store
        self.sender = sender
        self.ai = ai_client or CloudflareAI(
            cfg.cloudflare_account_id,
            cfg.cloudflare_api_token,
            cfg.ai_model,
            timeout=cfg.ai_timeout,
            max_output_tokens=cfg.ai_max_output_tokens,
        )
        self._now = now_provider or _dt.datetime.now
        # تاریخچه‌ی کوتاه هر گروه (فقط در حافظه؛ با ری‌استارت پاک می‌شود)
        self._history: "OrderedDict[int, deque]" = OrderedDict()

    # ------------------------------------------------------------------ کمکی‌ها
    def _day(self) -> str:
        return utc_day(self._now(_dt.timezone.utc))

    def _is_owner(self, user_id: int) -> bool:
        return bool(user_id) and self.store.is_owner(user_id)

    @staticmethod
    def _user_label(message, fallback: str) -> str:
        sender = getattr(message, "sender", None)
        username = (getattr(sender, "username", None) or "").strip().lstrip("@")
        if username:
            return f"@{username}"
        first = (getattr(sender, "first_name", "") or "").strip()
        last = (getattr(sender, "last_name", "") or "").strip()
        name = f"{first} {last}".strip()
        return name or fallback

    def _push_history(self, chat_id: int, role: str, content: str) -> None:
        if self.cfg.ai_history_pairs <= 0:
            return
        history = self._history.get(chat_id)
        if history is None:
            history = deque(maxlen=self.cfg.ai_history_pairs * 2)
            self._history[chat_id] = history
            if len(self._history) > 500:          # جلوگیری از رشد نامحدود حافظه
                self._history.popitem(last=False)
        history.append({"role": role, "content": content[: self.cfg.ai_max_input_chars]})

    def _build_messages(self, chat_id: int, user_text: str) -> list[dict]:
        """system + تاریخچه‌ی محدود + پیام جدید (با طول محدود)."""
        messages = [{"role": "system", "content": brand.AI_SYSTEM_PROMPT}]
        history = self._history.get(chat_id)
        if history and self.cfg.ai_history_pairs > 0:
            messages.extend(list(history)[-self.cfg.ai_history_pairs * 2:])
        messages.append(
            {"role": "user", "content": user_text[: self.cfg.ai_max_input_chars]}
        )
        return messages

    # ------------------------------------------------- دستورهای مدیریتی (مالک)
    async def handle_admin_command(self, client, event, command: str) -> bool:
        """اجرای دستورهای ai online / ai of / ai list / ai list x (فقط مالک سراسری)."""
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        chat_id = int(getattr(event, "chat_id", 0) or 0)

        # امنیت: فقط مالک سراسری — بر اساس user_id واقعی، نه username
        if not self._is_owner(sender_id):
            log.info("دستور AI «%s» از کاربر غیرمالک %s نادیده گرفته شد (chat=%s)",
                     command, sender_id, chat_id)
            return True

        if command == "online":
            self.store.ai_set_enabled(chat_id, True)
            log.info("AI برای گروه %s روشن شد", chat_id)
            await self._reply(client, event, brand.AI_ENABLED_TEXT)
            return True

        if command == "of":
            self.store.ai_set_enabled(chat_id, False)
            log.info("AI برای گروه %s خاموش شد", chat_id)
            await self._reply(client, event, brand.AI_DISABLED_TEXT)
            return True

        # ai list / ai list x → نیازمند Reply روی پیام همان کاربر
        reply_message = await self._get_reply_message(event)
        target_id = int(getattr(reply_message, "sender_id", 0) or 0) if reply_message else 0

        if not target_id:
            log.info("دستور «%s» بدون Reply معتبر از مالک %s", command, sender_id)
            await self._reply(client, event, brand.AI_NEED_REPLY_TEXT)
            return True

        label = self._user_label(reply_message, self.cfg.pv_unknown_name)
        target_username = getattr(getattr(reply_message, "sender", None), "username", None)

        if command == "list":
            self.store.ai_allow_user(
                chat_id, target_id, username=target_username, display_name=label
            )
            log.info("کاربر %s برای AI گروه %s مجاز شد", target_id, chat_id)
            await self._reply(client, event, brand.AI_ALLOWED_TEXT.format(user=label))
            return True

        if command == "listx":
            removed = self.store.ai_revoke_user(chat_id, target_id)
            log.info("حذف مجوز کاربر %s در گروه %s → %s", target_id, chat_id, removed)
            template = brand.AI_REVOKED_TEXT if removed else brand.AI_NOT_ALLOWED_TEXT
            await self._reply(client, event, template.format(user=label))
            return True

        return True

    # ------------------------------------------------------ گفت‌وگو با AI (گروه)
    async def handle_chat(self, client, event) -> None:
        """مسیر گفت‌وگو: فقط Reply + متن، فقط در گروهی که AI روشن است."""
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        text = (getattr(event, "raw_text", None) or "").strip()

        if not self.store.ai_is_enabled(chat_id):
            return

        # پیام بدون متن (مدیا/استیکر/…) بدون حدس زدن نادیده گرفته می‌شود
        if not text:
            log.debug("پیام بدون متن در گروه %s برای AI نادیده گرفته شد", chat_id)
            return

        # مجوز: کاربر مجازِ همان گروه یا خود مالک سراسری
        allowed = self.store.ai_is_allowed(chat_id, sender_id) or (
            self.cfg.ai_owner_always_allowed and self._is_owner(sender_id)
        )
        if not allowed:
            log.info("کاربر غیرمجاز %s در گروه %s → پیام عدم دسترسی", sender_id, chat_id)
            await self._reply(client, event, brand.AI_DENIED_TEXT)
            return

        # سهمیه‌ی روزانه (per-group و بر اساس روز UTC) — قبل از هر درخواست شبکه‌ای
        day = self._day()
        if not self.store.ai_consume_quota(chat_id, day, self.cfg.ai_daily_quota):
            log.warning("سهمیه روزانه AI گروه %s تمام شده است (%s)", chat_id, day)
            await self._reply(client, event, brand.AI_QUOTA_TEXT)
            return

        messages = self._build_messages(chat_id, text)
        try:
            response = await self.ai.chat(messages)
        except AIQuotaExceeded as exc:
            log.warning("Cloudflare سهمیه را تمام‌شده اعلام کرد: %s", exc)
            self.store.ai_exhaust_quota(chat_id, day, self.cfg.ai_daily_quota)
            await self._reply(client, event, brand.AI_QUOTA_TEXT)
            return
        except AIConfigError as exc:
            log.error("تنظیمات AI کامل نیست: %s", exc)
            await self._reply(client, event, brand.AI_CONFIG_ERROR_TEXT)
            return
        except AIError as exc:
            log.error("خطای AI: %s", exc)
            await self._reply(client, event, brand.AI_ERROR_TEXT)
            return

        self._push_history(chat_id, "user", text)
        self._push_history(chat_id, "assistant", response.text)

        # پاسخ مدل، در همان گروه و در پاسخ به همان پیام کاربر
        for chunk in brand.chunk_lines(response.text.split("\n"), self.cfg.max_message_chars):
            report = await self.sender.send_text(
                client,
                await resolve_peer(event),
                chunk,
                reply_to_msg_id=getattr(event, "id", None),
            )
            if not report.ok:
                log.error("ارسال پاسخ AI ناموفق بود: %s | %s",
                          report.error, report.attempts)

    # ------------------------------------------------------------------ کمکی‌ها
    async def _reply(self, client, event, text: str) -> None:
        """ارسال پیام سیستمی AI در پاسخ به همان پیام کاربر."""
        report = await self.sender.send_text(
            client,
            await resolve_peer(event),
            text,
            reply_to_msg_id=getattr(event, "id", None),
        )
        if not report.ok:
            log.error("ارسال پیام AI ناموفق بود: %s | %s", report.error, report.attempts)

    @staticmethod
    async def _get_reply_message(event):
        getter = getattr(event, "get_reply_message", None)
        if not callable(getter):
            return None
        try:
            return await getter()
        except Exception as exc:  # noqa: BLE001 — اگر پیام مرجع در دسترس نبود
            log.debug("get_reply_message ناموفق بود: %s", exc)
            return None
