"""
سرویس هوش مصنوعی گروه‌ها (فقط GROUP — هیچ ارتباطی با PV ندارد).

قابلیت‌های پیشرفته:
  * صف پردازش مستقل به ازای هر گروه (Independent Per-Group Queue).
  * ایزولاسیون کامل گفت‌وگو به ازای هر کاربر (Per-User Conversation Isolation).
  * سه مدل انتخابی Workers AI (مدل ۱ سریع، مدل ۲ استدلالی، مدل ۳ کدنویسی).
  * کنترل همروندی و جلوگیری از تداخل گروه‌ها در شرایط کندی شبکه.
  * سهمیه‌بندی دقیق روزانه و سقف اعضا با ریست در 00:00 Asia/Tehran.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Optional

import brand
from ai_client import AIConfigError, AIError, AIQuotaExceeded, CloudflareAI
from config import Config
from models import (
    DEFAULT_MODEL_PROFILE_ID,
    MODEL_PROFILES,
    ModelProfile,
    format_model_status,
    get_model_profile,
)
from sender import BrandSender, resolve_peer
from splusthon import functions, types
from storage import OwnerStore

log = logging.getLogger("acod.ai")

try:
    import zoneinfo
    TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30), name="Asia/Tehran")

_TYPING_INTERVAL = 4.5


class QueueFullError(AIError):
    """صف درخواست‌های گروه پر است."""


@dataclass
class QueuedRequest:
    """بسته متادیتا و بستر درخواست در صف پردازش."""

    request_id: str
    chat_id: int
    sender_id: int
    message_id: int
    text: str
    model_profile: ModelProfile
    created_at: _dt.datetime
    client: Any
    event: Any
    peer: Any
    future: asyncio.Future


class GroupQueueManager:
    """مدیریت صف‌های مستقل برای هر گروه با کنترل همروندی و عدم مسدودسازی سایر گروه‌ها."""

    MAX_QUEUE_SIZE = 15

    def __init__(self, processor_callable):
        self._processor = processor_callable
        self._queues: dict[int, asyncio.Queue] = {}
        self._workers: dict[int, asyncio.Task] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _ensure_same_loop(self) -> None:
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None
        if self._loop != curr_loop:
            self._loop = curr_loop
            self._queues.clear()
            self._workers.clear()

    def get_queue(self, chat_id: int) -> asyncio.Queue:
        self._ensure_same_loop()
        if chat_id not in self._queues:
            self._queues[chat_id] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        return self._queues[chat_id]

    def enqueue(self, req: QueuedRequest) -> None:
        self._ensure_same_loop()
        queue = self.get_queue(req.chat_id)
        if queue.full():
            raise QueueFullError("صف درخواست‌های این گروه تکمیل است.")
        queue.put_nowait(req)
        self._ensure_worker(req.chat_id)

    def _ensure_worker(self, chat_id: int) -> None:
        self._ensure_same_loop()
        task = self._workers.get(chat_id)
        if task is None or task.done():
            self._workers[chat_id] = asyncio.create_task(
                self._worker_loop(chat_id), name=f"ai-worker-{chat_id}"
            )

    async def _worker_loop(self, chat_id: int) -> None:
        queue = self.get_queue(chat_id)
        while True:
            try:
                # مهلت انتظار ۶۰ ثانیه قبل از خاموش شدن تسک بی‌کار
                req: QueuedRequest = await asyncio.wait_for(queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                if queue.empty():
                    self._workers.pop(chat_id, None)
                    break
                continue
            except asyncio.CancelledError:
                break

            try:
                timeout_val = req.model_profile.timeout if req.model_profile else 30.0
                exec_timeout = timeout_val + 15.0
                await asyncio.wait_for(self._processor(req), timeout=exec_timeout)
                if not req.future.done():
                    req.future.set_result(True)
            except asyncio.TimeoutError:
                if not req.future.done():
                    req.future.set_exception(
                        AIError("مهلت زمانی اجرای درخواست هوش مصنوعی به پایان رسید.", retryable=True)
                    )
            except Exception as exc:
                if not req.future.done():
                    req.future.set_exception(exc)
            finally:
                queue.task_done()

    async def shutdown(self) -> None:
        for task in list(self._workers.values()):
            if not task.done():
                task.cancel()
        self._workers.clear()
        self._queues.clear()


class _TypingIndicator:
    """مدیریت وضعیت «در حال نوشتن…» در یک چت مشخص برای طول عمر یک درخواست."""

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
            self._log.debug("لغو typing ناموفق بود: %s", exc)

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
                self._log.debug("ارسال typing ناموفق بود: %s", exc)
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
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d")


class GroupAI:
    """مدیریت وضعیت، صف‌های ایزوله و اجرای درخواست‌های AI برای گروه‌ها."""

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
        # تاریخچه مجزا به ازای هر کاربر در هر گروه: key = (chat_id, user_id)
        self._history: "OrderedDict[tuple[int, int], deque]" = OrderedDict()
        # صف مستقل به ازای هر گروه
        self.queue_manager = GroupQueueManager(self._process_single_request)

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

    def _push_history(self, chat_id: int, user_id: int, role: str, content: str) -> None:
        if self.cfg.ai_history_pairs <= 0:
            return
        key = (chat_id, int(user_id))
        history = self._history.get(key)
        if history is None:
            history = deque(maxlen=self.cfg.ai_history_pairs * 2)
            self._history[key] = history
            if len(self._history) > 1000:
                self._history.popitem(last=False)
        history.append({"role": role, "content": content[: self.cfg.ai_max_input_chars]})

    def _build_messages(
        self, chat_id: int, user_id: int | str = 0, user_text: str = ""
    ) -> list[dict]:
        if isinstance(user_id, str) and not user_text:
            user_text = user_id
            uid = 0
        else:
            uid = int(user_id or 0)

        messages = [{"role": "system", "content": brand.AI_SYSTEM_PROMPT}]
        key = (chat_id, uid)
        history = self._history.get(key)
        if history and self.cfg.ai_history_pairs > 0:
            messages.extend(list(history)[-self.cfg.ai_history_pairs * 2:])
        messages.append(
            {"role": "user", "content": user_text[: self.cfg.ai_max_input_chars]}
        )
        return messages

    def get_group_model_profile(self, chat_id: int) -> Optional[ModelProfile]:
        if self.store.has_group_model(chat_id):
            pid = self.store.get_group_model(chat_id)
            return get_model_profile(pid)
        return None

    # ------------------------------------------------- دستورهای مدیریتی
    async def handle_admin_command(self, client, event, command: str) -> bool:
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        chat_id = int(getattr(event, "chat_id", 0) or 0)

        # بررسی انقضا
        if self.store.is_expired(self._now()):
            await self._reply(client, event, brand.BOT_EXPIRED_TEXT)
            return True

        # دستور انتخاب مدل: ai model [1|2|3] یا ai model
        if command.startswith("model"):
            if command == "model":
                profile_id = self.store.get_group_model(chat_id)
                await self._reply(client, event, format_model_status(profile_id))
                return True

            # تغییر مدل منحصراً برای مالک سراسری است
            if not self._is_owner(sender_id):
                log.info("تغییر مدل توسط کاربر غیرمالک سراسری %s رد شد", sender_id)
                await self._reply(
                    client, event, "⚠️ تغییر مدل هوش مصنوعی فقط توسط مالک سراسری امکان‌پذیر است."
                )
                return True

            parts = command.split()
            if len(parts) >= 2:
                try:
                    target_pid = int(parts[1])
                except ValueError:
                    target_pid = 0
                profile = get_model_profile(target_pid)
                if not profile:
                    await self._reply(
                        client, event, "⚠️ شماره مدل نامعتبر است. مدل‌های مجاز: 1، 2، 3"
                    )
                    return True

                self.store.set_group_model(chat_id, profile.id)
                log.info("مدل هوش مصنوعی گروه %s به %s (%s) تغییر یافت", chat_id, profile.id, profile.name)
                msg = (
                    f"✅ مدل هوش مصنوعی این گروه تغییر یافت:\n"
                    f"مدل {profile.id} — {profile.name} ({profile.english_name})\n"
                    f"📝 {profile.description}"
                )
                await self._reply(client, event, msg)
                return True
            return True

        # سایر دستورات نیازمند دسترسی مدیریت (مالک سراسری یا مالک ثبت‌شده) هستند
        if not self._is_admin(sender_id):
            log.info("دستور AI «%s» از کاربر غیرمجاز %s نادیده گرفته شد (chat=%s)",
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

        if command == "l":
            users = self.store.ai_allowed_users(chat_id)
            labels = []
            for u in users:
                lbl = u.username and f"@{u.username.lstrip('@')}" or u.display_name or f"کاربر {u.user_id}"
                labels.append(lbl)
            msg = brand.format_ai_l_list(labels)
            await self._reply(client, event, msg)
            return True

        reply_message = await self._get_reply_message(event)
        target_id = int(getattr(reply_message, "sender_id", 0) or 0) if reply_message else 0

        if not target_id:
            log.info("دستور «%s» بدون Reply معتبر از کاربر %s", command, sender_id)
            await self._reply(client, event, brand.AI_NEED_REPLY_TEXT)
            return True

        label = self._user_label(reply_message, target_id, brand.AI_USER_FALLBACK_PREFIX)
        target_username = getattr(getattr(reply_message, "sender", None), "username", None)

        if command == "list":
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

    # ------------------------------------------------------ گفت‌وگو با AI (ورود به صف اختصاصی گروه)
    async def handle_chat(self, client, event) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        text = (getattr(event, "raw_text", None) or "").strip()

        if self.store.is_expired(self._now()):
            await self._reply(client, event, brand.BOT_EXPIRED_TEXT)
            return

        if not self.store.ai_is_enabled(chat_id):
            return

        if not text:
            return

        reply_msg = await self._get_reply_message(event)
        if reply_msg is None:
            return

        my_id = None
        get_me = getattr(client, "get_me", None)
        if callable(get_me):
            try:
                me = await get_me()
                my_id = int(getattr(me, "id", 0) or 0)
            except Exception:
                my_id = None

        reply_is_mine = bool(getattr(reply_msg, "out", False))
        reply_sender_id = int(getattr(reply_msg, "sender_id", 0) or 0)
        if my_id and reply_sender_id and reply_sender_id != my_id and not reply_is_mine:
            return
        if (not reply_is_mine) and (not my_id):
            if reply_sender_id and reply_sender_id != sender_id and not reply_is_mine:
                return

        # بررسی مجوز کاربر
        allowed = (
            self.store.ai_is_allowed(chat_id, sender_id)
            or (self.cfg.ai_owner_always_allowed and self._is_owner(sender_id))
            or self._is_registered_bot_owner(sender_id)
        )
        if not allowed:
            await self._reply(client, event, brand.AI_DENIED_TEXT)
            return

        # بررسی سهمیه روزانه گروه
        day = self._day()
        limit = self.store.get_daily_quota(self.cfg.ai_daily_quota)
        if self.store.ai_used_quota(chat_id, day) >= limit:
            await self._reply(client, event, brand.AI_QUOTA_TEXT)
            return

        profile = self.get_group_model_profile(chat_id)

        try:
            peer = await resolve_peer(event)
        except Exception:
            peer = None

        req_id = f"req-{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        req = QueuedRequest(
            request_id=req_id,
            chat_id=chat_id,
            sender_id=sender_id,
            message_id=getattr(event, "id", None),
            text=text,
            model_profile=profile,
            created_at=self._now(),
            client=client,
            event=event,
            peer=peer,
            future=future,
        )

        try:
            self.queue_manager.enqueue(req)
        except QueueFullError:
            await self._reply(client, event, brand.AI_QUEUE_BUSY_TEXT)
            return

        try:
            await future
        except AIQuotaExceeded as exc:
            log.warning("Cloudflare سهمیه را تمام‌شده اعلام کرد: %s", exc)
            self.store.ai_exhaust_quota(chat_id, day, limit)
            await self._reply(client, event, brand.AI_QUOTA_TEXT)
        except AIConfigError as exc:
            log.error("تنظیمات AI کامل نیست: %s", exc)
            await self._reply(client, event, brand.AI_CONFIG_ERROR_TEXT)
        except AIError as exc:
            log.error("خطای AI: %s", exc)
            await self._reply(client, event, brand.AI_ERROR_TEXT)
        except Exception as exc:
            log.error("استثنا در پردازش AI: %s", exc)
            await self._reply(client, event, brand.AI_ERROR_TEXT)

    # ------------------------------------------------------ اجرای پردازش درخواست در صف
    async def _process_single_request(self, req: QueuedRequest) -> None:
        typing_indicator = _TypingIndicator(req.client, req.peer, log)
        typing_indicator.start()
        await asyncio.sleep(0)
        try:
            messages = self._build_messages(req.chat_id, req.sender_id, req.text)
            model_override = req.model_profile.model_id if req.model_profile else None
            max_tokens_override = (
                req.model_profile.max_output_tokens if req.model_profile else None
            )
            response = await self.ai.chat(
                messages,
                model=model_override,
                max_tokens=max_tokens_override,
                max_retries=self._retry_max,
                base_delay=self._retry_base_delay,
            )

            # مصرف سهمیه گروه فقط پس از دریافت پاسخ موفق
            day = self._day()
            limit = self.store.get_daily_quota(self.cfg.ai_daily_quota)
            self.store.ai_consume_quota(req.chat_id, day, limit)

            # ثبت تاریخچه گفت‌وگو منحصراً برای همین کاربر در همین گروه
            self._push_history(req.chat_id, req.sender_id, "user", req.text)
            self._push_history(req.chat_id, req.sender_id, "assistant", response.text)

            for chunk in brand.chunk_lines(response.text.split("\n"), self.cfg.max_message_chars):
                report = await self.sender.send_text(
                    req.client,
                    req.peer,
                    chunk,
                    reply_to_msg_id=req.message_id,
                )
                if not report.ok:
                    log.error("ارسال پاسخ AI ناموفق بود: %s", report.error)
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
