"""
سرویس هوش مصنوعی گروه‌ها (فقط GROUP — هیچ ارتباطی با PV ندارد).

قواعدی که این ماژول پیاده می‌کند:
  * «ai online» / «ai of»  → روشن/خاموش کردن AI برای «همان گروه» (مالک سراسری یا مالک ثبت‌شده).
  * «ai list» (با Reply روی پیام کاربر)  → مجاز کردن آن کاربر با رعایت سقف مجاز اعضا.
  * «ai list x» (با Reply روی پیام کاربر) → حذف مجوز همان کاربر در همان گروه.
  * «ai L» → نمایش فهرست کاربران مجاز همان گروه.
  * صحبت با AI فقط با «Reply + متن» روی پیام ربات در گروهی که AI روشن است انجام می‌شود.
  * مالک ثبت‌شده (Registered Bot Owner) بدون نیاز به مجوز صحبت می‌کند و سهمیه عضویت مصرف نمی‌کند،
    اما درخواست‌هایش از سهمیه روزانه گروه کسر می‌شود.
  * کنترل مصرف: سهمیه‌ی روزانه به تفکیک گروه با ریست در 00:00 Asia/Tehran.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from collections import OrderedDict, deque
from typing import Optional

import brand
from ai_client import AIConfigError, AIError, AIQuotaExceeded, CloudflareAI
from config import Config
from sender import BrandSender, resolve_peer
from splusthon import functions, types
from storage import OwnerStore

log = logging.getLogger("acod.ai")

try:
    import zoneinfo
    TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")
except Exception:
    # فال‌بک برای محیط‌هایی مثل Termux / Android / ویندوز که پکیج tzdata ندارند.
    # ساعت رسمی ایران (تهران) به صورت ثابت UTC+03:30 است (بدون DST).
    TEHRAN_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30), name="Asia/Tehran")
_TYPING_INTERVAL = 4.5


class _TypingIndicator:
    """مدیریت وضعیت «در حال نوشتن…» در یک چت مشخص، فقط برای طول عمر یک درخواست AI."""

    __slots__ = ("_send", "_peer", "_task", "_stopped", "_log")

    def __init__(self, send, peer, logger):
        self._send = send
        self._peer = peer
        self._task: Optional[asyncio.Task] = None
        self._stopped = False
        self._log = logger

    def start(self):
        if self._stopped or self._peer is None:
            return
        if self._task is not None:
            return
        self._task = asyncio.ensure_future(self._run())

    async def stop(self):
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._peer is None:
            return
        try:
            await self._send(
                functions.messages.SetTypingRequest(
                    peer=self._peer,
                    action=types.SendMessageCancelAction(),
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._log.debug("لغو typing ناموفق بود (نادیده گرفته می‌شود): %s", exc)

    async def _run(self):
        while not self._stopped:
            try:
                await self._send(
                    functions.messages.SetTypingRequest(
                        peer=self._peer,
                        action=types.SendMessageTypingAction(),
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log.debug("ارسال typing ناموفق بود (نادیده گرفته می‌شود): %s", exc)
            try:
                await asyncio.sleep(_TYPING_INTERVAL)
            except asyncio.CancelledError:
                raise


def tehran_day(now: Optional[_dt.datetime] = None) -> str:
    """روز جاری بر اساس ساعت ایران (Asia/Tehran) به شکل YYYY-MM-DD."""
    if now is None:
        now = _dt.datetime.now(TEHRAN_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc).astimezone(TEHRAN_TZ)
    else:
        now = now.astimezone(TEHRAN_TZ)
    return now.strftime("%Y-%m-%d")


def utc_day(now: Optional[_dt.datetime] = None) -> str:
    """روز جاری بر اساس UTC به شکل YYYY-MM-DD (برای سازگاری با تست‌های قدیمی)."""
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
        self._retry_max = int(getattr(cfg, "ai_retry_max", 3))
        self._retry_base_delay = float(getattr(cfg, "ai_retry_base_delay", 1.2))
        self._now = now_provider or _dt.datetime.now
        self._history: "OrderedDict[int, deque]" = OrderedDict()

    # ------------------------------------------------------------------ کمکی‌ها
    def _day(self) -> str:
        tz_name = getattr(self.cfg, "ai_quota_timezone", "Asia/Tehran")
        if tz_name == "UTC":
            return utc_day(self._now(_dt.timezone.utc))
        return tehran_day(self._now(TEHRAN_TZ))

    def _is_owner(self, user_id: int) -> bool:
        return bool(user_id) and self.store.is_owner(user_id)

    def _is_registered_bot_owner(self, user_id: int) -> bool:
        return bool(user_id) and self.store.is_registered_bot_owner(user_id)

    def _is_admin(self, user_id: int) -> bool:
        """مالک سراسری یا مالک ثبت‌شده (دارای دسترسی‌های عادی AI)."""
        return self._is_owner(user_id) or self._is_registered_bot_owner(user_id)

    @staticmethod
    def _user_label(message, user_id: int, fallback_prefix: str) -> str:
        sender = getattr(message, "sender", None)
        username = (getattr(sender, "username", None) or "").strip().lstrip("@")
        if username:
            return f"@{username}"
        first = (getattr(sender, "first_name", "") or "").strip()
        last = (getattr(sender, "last_name", "") or "").strip()
        name = f"{first} {last}".strip()
        return name or f"{fallback_prefix} {user_id}"

    def _push_history(self, chat_id: int, role: str, content: str) -> None:
        if self.cfg.ai_history_pairs <= 0:
            return
        history = self._history.get(chat_id)
        if history is None:
            history = deque(maxlen=self.cfg.ai_history_pairs * 2)
            self._history[chat_id] = history
            if len(self._history) > 500:
                self._history.popitem(last=False)
        history.append({"role": role, "content": content[: self.cfg.ai_max_input_chars]})

    def _build_messages(self, chat_id: int, user_text: str) -> list[dict]:
        messages = [{"role": "system", "content": brand.AI_SYSTEM_PROMPT}]
        history = self._history.get(chat_id)
        if history and self.cfg.ai_history_pairs > 0:
            messages.extend(list(history)[-self.cfg.ai_history_pairs * 2:])
        messages.append(
            {"role": "user", "content": user_text[: self.cfg.ai_max_input_chars]}
        )
        return messages

    # ------------------------------------------------- دستورهای مدیریتی (مالک / مالک ثبت‌شده)
    async def handle_admin_command(self, client, event, command: str) -> bool:
        """اجرای دستورهای ai online / ai of / ai list / ai list x / ai L."""
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        chat_id = int(getattr(event, "chat_id", 0) or 0)

        # امنیت: فقط مالک سراسری یا مالک ثبت‌شده
        if not self._is_admin(sender_id):
            log.info("دستور AI «%s» از کاربر غیرمجاز %s نادیده گرفته شد (chat=%s)",
                     command, sender_id, chat_id)
            return True

        # بررسی انقضا
        if self.store.is_expired(self._now()):
            await self._reply(client, event, brand.BOT_EXPIRED_TEXT)
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

        if command == "l":
            users = self.store.ai_allowed_users(chat_id)
            if not users:
                await self._reply(client, event, "هنوز کاربری برای هوش مصنوعی در این گروه مجاز نشده است.")
                return True
            lines = ["☰ لیست کاربران مجاز هوش مصنوعی:"]
            for i, u in enumerate(users, 1):
                label = u.username and f"@{u.username.lstrip('@')}" or u.display_name or f"کاربر {u.user_id}"
                lines.append(f"{i} : {label}")
            await self._reply(client, event, "\n".join(lines))
            return True

        # ai list / ai list x → نیازمند Reply روی پیام همان کاربر
        reply_message = await self._get_reply_message(event)
        target_id = int(getattr(reply_message, "sender_id", 0) or 0) if reply_message else 0

        if not target_id:
            log.info("دستور «%s» بدون Reply معتبر از کاربر %s", command, sender_id)
            await self._reply(client, event, brand.AI_NEED_REPLY_TEXT)
            return True

        label = self._user_label(reply_message, target_id, brand.AI_USER_FALLBACK_PREFIX)
        target_username = getattr(getattr(reply_message, "sender", None), "username", None)

        if command == "list":
            # بررسی سقف کاربران مجاز برای این گروه
            limit = self.store.get_max_allowed_users(self.cfg.ai_max_allowed_users)
            current_users = self.store.ai_allowed_users(chat_id)
            is_already_allowed = any(u.user_id == target_id for u in current_users)

            if not is_already_allowed and len(current_users) >= limit:
                log.info("سقف اعضای مجاز گروه %s تکمیل است (%s عضو)", chat_id, limit)
                await self._reply(client, event, brand.AI_MAX_USERS_REACHED_TEXT.format(limit=limit))
                return True

            self.store.ai_allow_user(
                chat_id, target_id, username=target_username, display_name=label
            )
            log.info("کاربر %s برای AI گروه %s مجاز شد", target_id, chat_id)
            await self._reply(client, event, brand.AI_ALLOWED_TEXT.format(user=label))
            return True

        if command == "listx":
            removed = self.store.ai_revoke_user(chat_id, target_id)
            log.info("حذف مجوز کاربر %s در گروه %s → %s", target_id, chat_id, removed)
            await self._reply(client, event, brand.AI_REVOKED_TEXT.format(user=label))
            return True

        return True

    # ------------------------------------------------------ گفت‌وگو با AI (گروه)
    async def handle_chat(self, client, event) -> None:
        """مسیر گفت‌وگو: فقط Reply روی پیام‌های خود ربات، فقط در گروهی که AI روشن است."""
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        text = (getattr(event, "raw_text", None) or "").strip()

        # بررسی انقضا
        if self.store.is_expired(self._now()):
            await self._reply(client, event, brand.BOT_EXPIRED_TEXT)
            return

        if not self.store.ai_is_enabled(chat_id):
            return

        if not text:
            log.debug("پیام بدون متن در گروه %s برای AI نادیده گرفته شد", chat_id)
            return

        reply_msg = await self._get_reply_message(event)
        if reply_msg is None:
            return

        # بررسی اینکه ریپلای روی پیام خودِ ربات است
        my_id = None
        get_me = getattr(client, "get_me", None)
        if callable(get_me):
            try:
                me = await get_me()
                my_id = int(getattr(me, "id", 0) or 0)
            except Exception:  # noqa: BLE001
                my_id = None

        reply_is_mine = bool(getattr(reply_msg, "out", False))
        reply_sender_id = int(getattr(reply_msg, "sender_id", 0) or 0)
        if my_id and reply_sender_id and reply_sender_id != my_id and not reply_is_mine:
            log.debug("ریپلای روی پیام کاربری دیگر (sender=%s) در گروه %s نادیده گرفته شد.",
                      reply_sender_id, chat_id)
            return
        if (not reply_is_mine) and (not my_id):
            if reply_sender_id and reply_sender_id != sender_id and not reply_is_mine:
                return

        # مجوز: کاربر مجاز، مالک سراسری، یا مالک ثبت‌شده
        allowed = (
            self.store.ai_is_allowed(chat_id, sender_id)
            or (self.cfg.ai_owner_always_allowed and self._is_owner(sender_id))
            or self._is_registered_bot_owner(sender_id)
        )
        if not allowed:
            log.info("کاربر غیرمجاز %s در گروه %s → پیام عدم دسترسی", sender_id, chat_id)
            await self._reply(client, event, brand.AI_DENIED_TEXT)
            return

        # سهمیه‌ی روزانه (per-group و بر اساس روز Asia/Tehran)
        day = self._day()
        limit = self.store.get_daily_quota(self.cfg.ai_daily_quota)
        if self.store.ai_used_quota(chat_id, day) >= limit:
            log.warning("سهمیه روزانه AI گروه %s تمام شده است (%s)", chat_id, day)
            await self._reply(client, event, brand.AI_QUOTA_TEXT)
            return

        try:
            peer = await resolve_peer(event)
        except Exception as exc:  # noqa: BLE001
            log.error("تعیین peer چت برای typing ممکن نشد: %s", exc)
            peer = None

        typing_indicator = _TypingIndicator(client, peer, log)
        typing_indicator.start()
        await asyncio.sleep(0)
        try:
            messages = self._build_messages(chat_id, text)
            try:
                response = await self.ai.chat(
                    messages,
                    max_retries=self._retry_max,
                    base_delay=self._retry_base_delay,
                )
            except AIQuotaExceeded as exc:
                log.warning("Cloudflare سهمیه را تمام‌شده اعلام کرد: %s", exc)
                self.store.ai_exhaust_quota(chat_id, day, limit)
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

            # فقط در صورت پاسخ موفقیت‌آمیز، سهمیه مصرف می‌شود (درخواست‌های ناموفق بالادستی سهمیه مصرف نمی‌کنند)
            self.store.ai_consume_quota(chat_id, day, limit)

            self._push_history(chat_id, "user", text)
            self._push_history(chat_id, "assistant", response.text)

            for chunk in brand.chunk_lines(response.text.split("\n"), self.cfg.max_message_chars):
                report = await self.sender.send_text(
                    client,
                    peer,
                    chunk,
                    reply_to_msg_id=getattr(event, "id", None),
                )
                if not report.ok:
                    log.error("ارسال پاسخ AI ناموفق بود: %s | %s",
                              report.error, report.attempts)
        finally:
            await typing_indicator.stop()

    # ------------------------------------------------------------------ کمکی‌ها
    async def _reply(self, client, event, text: str) -> None:
        report = await self.sender.send_styled(
            client,
            await resolve_peer(event),
            text,
            reply_to_msg_id=getattr(event, "id", None),
            quote=False,
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
        except Exception as exc:  # noqa: BLE001
            log.debug("get_reply_message ناموفق بود: %s", exc)
            return None
