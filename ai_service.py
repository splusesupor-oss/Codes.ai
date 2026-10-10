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
import re
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Optional

AI_SESSION_TIMEOUT_SECONDS = 50 * 60  # ۵۰ دقیقه مهلت حافظه گفت‌وگو و لاگ پیام‌های گروه


def _to_timestamp(dt_val) -> float:
    if isinstance(dt_val, (int, float)):
        return float(dt_val)
    if hasattr(dt_val, "timestamp"):
        try:
            return dt_val.timestamp()
        except Exception:
            pass
    return time.time()

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
    target_user_info: Optional[dict] = None


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
        # زمان آخرین فعالیت برای تایم‌اوت ۵۰ دقیقه‌ای حافظه: key = (chat_id, user_id) -> timestamp
        self._history_last_activity: dict[tuple[int, int], float] = {}
        # بافر رویدادها و پیام‌های ۵۰ دقیقه اخیر در گروه برای درک زمینه، تعاملات و تحلیل کلی
        self._recent_group_messages: dict[int, deque] = {}
        # مشخصات گروه (نام، مالک، ادمین‌ها و اعضا)
        self._group_metadata: dict[int, dict] = {}
        # فهرست اعضا و کاربران شناخته‌شده گروه جهت جستجو و شناسایی
        self._group_members: dict[int, OrderedDict[int, dict]] = {}
        # صف مستقل به ازای هر گروه
        self.queue_manager = GroupQueueManager(self._process_single_request)

    # ------------------------------------------------------------------ کمکی‌ها
    def get_history(self, chat_id: int, user_id: int) -> list[dict]:
        """دریافت تاریخچه گفت‌وگوی کاربر با رعایت سقف ۵۰ دقیقه."""
        key = (int(chat_id), int(user_id))
        now_ts = _to_timestamp(self._now())
        last_act = self._history_last_activity.get(key)
        if last_act and (now_ts - last_act) > AI_SESSION_TIMEOUT_SECONDS:
            self._history.pop(key, None)
            self._history_last_activity.pop(key, None)
            return []
        hist = self._history.get(key)
        return list(hist) if hist else []

    def record_group_message(
        self,
        chat_id: int,
        sender_label: str,
        text: str,
        user_id: int = 0,
        username: Optional[str] = None,
        reply_to_info: Optional[dict] = None,
        is_ad: bool = False,
        ad_word: Optional[str] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        """ثبت پیام‌ها و رویدادهای ۵۰ دقیقه اخیر گروه با جزئیات کامل فرستنده، مخاطب، کلمات تبلیغاتی و محتوا."""
        if not text or not chat_id:
            return
        chat_id = int(chat_id)
        if chat_id not in self._recent_group_messages:
            self._recent_group_messages[chat_id] = deque(maxlen=100)

        clean_text = text.strip()
        if len(clean_text) > 400:
            clean_text = clean_text[:400] + "..."

        now_val = self._now(TEHRAN_TZ) if hasattr(self, "_now") else _dt.datetime.now(TEHRAN_TZ)
        now_ts = float(timestamp) if timestamp is not None else _to_timestamp(self._now())
        time_str = now_val.strftime("%H:%M") if hasattr(now_val, "strftime") else ""

        self._recent_group_messages[chat_id].append({
            "sender": sender_label,
            "username": (username or "").strip().lstrip("@") if username else None,
            "user_id": int(user_id or 0),
            "text": clean_text,
            "reply_to": reply_to_info,
            "is_ad": is_ad,
            "ad_word": ad_word,
            "time": time_str,
            "timestamp": now_ts,
        })

    def set_group_metadata(
        self,
        chat_id: int,
        group_name: Optional[str] = None,
        owner_label: Optional[str] = None,
        admin_labels: Optional[list[str]] = None,
        members_count: Optional[int] = None,
    ) -> None:
        """به‌روزرسانی اطلاعات مالکان، ادمین‌ها و هویت گروه."""
        chat_id = int(chat_id)
        current = self._group_metadata.get(chat_id, {}).copy()
        if group_name:
            current["group_name"] = group_name
        if owner_label:
            current["owner_label"] = owner_label
        if admin_labels is not None:
            current["admin_labels"] = admin_labels
        if members_count is not None:
            current["members_count"] = members_count
        self._group_metadata[chat_id] = current

    def get_group_metadata(self, chat_id: int) -> dict:
        chat_id = int(chat_id)
        meta = self._group_metadata.get(chat_id, {}).copy()
        if not meta.get("group_name"):
            rec = self.store.get_group_record(chat_id)
            if rec and rec.group_name:
                meta["group_name"] = rec.group_name
        return meta

    def get_recent_group_messages(self, chat_id: int) -> list[dict]:
        """بازگردانی رویدادها و پیام‌های ۵۰ دقیقه اخیر گروه."""
        chat_id = int(chat_id)
        msgs = self._recent_group_messages.get(chat_id)
        if not msgs:
            return []
        now_ts = _to_timestamp(self._now())
        # نگهداری و بازگردانی پیام‌های ۵۰ دقیقه اخیر (۳۰۰۰ ثانیه)
        valid = [
            m for m in msgs
            if (now_ts - m.get("timestamp", now_ts)) <= AI_SESSION_TIMEOUT_SECONDS
        ]
        return valid

    def record_group_member(
        self, chat_id: int, user_id: int, name: str, username: Optional[str] = None
    ) -> None:
        """ثبت کاربر شناخته‌شده در گروه برای امکان جستجو و یافتن کاربران توسط هوش مصنوعی."""
        if not chat_id or not user_id:
            return
        chat_id = int(chat_id)
        if chat_id not in self._group_members:
            self._group_members[chat_id] = OrderedDict()
        members = self._group_members[chat_id]
        clean_name = (name or "").strip()
        clean_user = (username or "").strip().lstrip("@") if username else None
        members[int(user_id)] = {
            "name": clean_name or f"کاربر {user_id}",
            "username": clean_user,
        }
        if len(members) > 500:
            members.popitem(last=False)

    def get_group_members(self, chat_id: int) -> dict[int, dict]:
        """فهرست اعضا و کاربران شناخته‌شده گروه."""
        return dict(self._group_members.get(int(chat_id), {}))

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
        now_ts = _to_timestamp(self._now())
        history = self._history.get(key)
        if history is None:
            history = deque(maxlen=self.cfg.ai_history_pairs * 2)
            self._history[key] = history
            if len(self._history) > 1000:
                self._history.popitem(last=False)
        history.append({"role": role, "content": content[: self.cfg.ai_max_input_chars]})
        self._history_last_activity[key] = now_ts

    def _build_messages(
        self,
        chat_id: int,
        user_id: int | str = 0,
        user_text: str = "",
        target_user_info: Optional[dict] = None,
    ) -> list[dict]:
        if isinstance(user_id, str) and not user_text:
            user_text = user_id
            uid = 0
        else:
            uid = int(user_id or 0)

        # اطلاعات گروه و آخرین پیام‌های تبادل‌شده (در صورت وجود)
        group_info_lines = []
        meta = self.get_group_metadata(chat_id)
        has_real_meta = bool(
            (meta.get("owner_label") and meta.get("owner_label") != "مالک یافت نشد")
            or meta.get("admin_labels")
        )
        if meta and has_real_meta:
            g_name = meta.get("group_name") or "گروه"
            owner = meta.get("owner_label")
            admins = meta.get("admin_labels")
            count = meta.get("members_count")

            info = ["اطلاعات گروه فعلی:"]
            info.append(f"• نام گروه: {g_name}")
            if owner and owner != "مالک یافت نشد":
                info.append(f"• مالک (سازنده) اصلی گروه: {owner}")
            if admins:
                info.append(f"• مدیران (ادمین‌های) گروه: {', '.join(admins)}")
            if count:
                info.append(f"• تعداد اعضای شناخته‌شده: {count}")
            group_info_lines.append("\n".join(info))

            members = self.get_group_members(chat_id)
            if members:
                mem_lines = ["فهرست اعضا و کاربران شناخته‌شده در گروه (در صورت پرسش درباره حضور یا مشخصات یک کاربر در گروه، از این اطلاعات با دقت استفاده کن):"]
                for m_id, m_info in list(members.items())[:60]:
                    u_disp = m_info.get("name") or "بدون نام"
                    u_handle = f"@{m_info['username'].lstrip('@')}" if m_info.get("username") else "ندارد"
                    mem_lines.append(f"• نام: {u_disp} | نام کاربری: {u_handle} | شناسه: {m_id}")
                group_info_lines.append("\n".join(mem_lines))

        recent = self.get_recent_group_messages(chat_id)
        if recent:
            rec_lines = ["آخرین پیام‌های ردوبدل‌شده اعضای گروه و رویدادهای ۵۰ دقیقه اخیر (شامل اینکه چه کسی به چه کسی پیام داده، چه گفته شده و چه کلمات تبلیغاتی ارسال شده است):"]
            for m in recent[-15:]:
                t_str = f"[{m['time']}] " if m.get("time") else ""
                u_str = f" (@{m['username']})" if m.get("username") else ""
                sender_part = f"{m['sender']}{u_str}"

                if m.get("is_ad"):
                    ad_w = f" حاوی کلمه تبلیغاتی «{m['ad_word']}»" if m.get("ad_word") else ""
                    rec_lines.append(f"- {t_str}[⚠️ ارسال تبلیغات] کاربر {sender_part}{ad_w} این پیام را ارسال کرد که حذف و هشدار داده شد: «{m['text']}»")
                elif m.get("reply_to"):
                    r = m["reply_to"]
                    r_target = r.get("name") or "کاربر"
                    r_uname = f" (@{r['username'].lstrip('@')})" if r.get("username") else ""
                    r_ref = f" در پاسخ به «{r['text']}»" if r.get("text") else ""
                    rec_lines.append(f"- {t_str}{sender_part} خطاب به {r_target}{r_uname}{r_ref}: «{m['text']}»")
                else:
                    rec_lines.append(f"- {t_str}{sender_part}: «{m['text']}»")
            group_info_lines.append("\n".join(rec_lines))

        if target_user_info:
            target_lines = ["اطلاعات کاربری که پیام روی او ریپلای شده است (در صورت سوال درباره این کاربر، از این اطلاعات برای پاسخ دقیق و تحلیل استفاده کن):"]
            t_name = target_user_info.get("display_name") or "کاربر"
            t_user = target_user_info.get("username")
            t_id = target_user_info.get("user_id")
            t_msg = target_user_info.get("replied_message")
            t_recent = target_user_info.get("recent_messages")

            target_lines.append(f"• نام نمایشی کاربر (Display Name): {t_name}")
            u_handle = f"@{t_user.lstrip('@')}" if t_user else "ندارد"
            target_lines.append(f"• نام کاربری (Username): {u_handle}")
            if t_id:
                target_lines.append(f"• شناسه عددی (User ID): {t_id}")
            if t_msg:
                target_lines.append(f"• متن پیامی که روی آن ریپلای شده: «{t_msg}»")
            if t_recent:
                target_lines.append("• آخرین پیام‌های این کاربر در گروه:")
                for rm in t_recent[-8:]:
                    target_lines.append(f"  - {rm}")
            group_info_lines.append("\n".join(target_lines))

        system_content = brand.AI_SYSTEM_PROMPT
        if group_info_lines:
            system_content = f"{brand.AI_SYSTEM_PROMPT}\n\n" + "\n\n".join(group_info_lines)

        messages = [{"role": "system", "content": system_content}]

        key = (chat_id, uid)
        now_ts = _to_timestamp(self._now())
        last_act = self._history_last_activity.get(key)
        if last_act and (now_ts - last_act) > AI_SESSION_TIMEOUT_SECONDS:
            self._history.pop(key, None)
            self._history_last_activity.pop(key, None)

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

        # بررسی انقضای ربات یا گروه
        if self.store.is_expired(self._now()) or self.store.is_group_expired(chat_id, self._now(TEHRAN_TZ)):
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
            limit = self.store.get_max_allowed_users(self.cfg.ai_max_allowed_users, chat_id=chat_id)
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

        if self.store.is_expired(self._now()) or self.store.is_group_expired(chat_id, self._now(TEHRAN_TZ)):
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
        is_reply_to_bot = bool(
            reply_is_mine
            or (my_id and reply_sender_id == my_id)
            or (reply_sender_id and reply_sender_id == sender_id)
        )
        target_user_info = None

        if not is_reply_to_bot:
            # اگر ریپلای روی پیام سایر اعضا باشد، فقط در صورتی پردازش شود که:
            # ۱) فرستنده پیام، مالک گروه یا کاربر مجاز باشد
            allowed = (
                self.store.ai_is_allowed(chat_id, sender_id)
                or (self.cfg.ai_owner_always_allowed and self._is_owner(sender_id))
                or self._is_registered_bot_owner(sender_id)
            )
            if not allowed:
                return

            # ۲) پیام ارسالی صراحتاً با پیشوند هوش مصنوعی آغاز شده باشد (مثال: «ai تحلیل رفتار کاربر» یا «ai این کاربر چرا ساکته»)
            ai_call_match = re.match(
                r"^(?:ai|هوش\s*مصنوعی|ربات)(?:[\s:،,–\-]+(.*)|$)",
                text.strip(),
                re.IGNORECASE | re.DOTALL,
            )
            if not ai_call_match:
                # اگر کاربر مجاز بدون صدا زدن هوش مصنوعی در حال گفت‌وگوی عادی با اعضاست، ربات ساکت بماند
                return

            clean_query = (ai_call_match.group(1) or "").strip()
            if clean_query:
                text = clean_query

            target_id = reply_sender_id
            target_sender = getattr(reply_msg, "sender", None)
            target_username = getattr(target_sender, "username", None) if target_sender else None

            # استخراج نام نمایشی دقیق
            first_name = (getattr(target_sender, "first_name", "") or "").strip()
            last_name = (getattr(target_sender, "last_name", "") or "").strip()
            full_display = f"{first_name} {last_name}".strip()
            target_label = full_display or self._user_label(reply_msg, target_id, "کاربر")

            target_text = (getattr(reply_msg, "raw_text", "") or "").strip()

            recent_msgs = self.get_recent_group_messages(chat_id)
            user_recent = [
                m["text"]
                for m in recent_msgs
                if (m.get("user_id") and m.get("user_id") == target_id)
                or (m.get("sender") == target_label)
            ]

            target_user_info = {
                "user_id": target_id,
                "username": target_username,
                "display_name": target_label,
                "replied_message": target_text,
                "recent_messages": user_recent[-10:],
            }
        else:
            # بررسی مجوز کاربر هنگام ریپلای مستقیم به پیام ربات
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
        limit = self.store.get_daily_quota(self.cfg.ai_daily_quota, chat_id=chat_id)
        if self.store.ai_used_quota(chat_id, day) >= limit:
            await self._reply(client, event, brand.AI_QUOTA_TEXT)
            return

        # به‌روزرسانی لیست اعضای گروه از کلاینت در صورت پشتیبانی
        if callable(getattr(client, "get_participants", None)):
            try:
                all_p = await client.get_participants(chat_id)
                if all_p:
                    for p in all_p:
                        p_id = getattr(p, "id", None) or getattr(p, "user_id", None)
                        if p_id:
                            p_fn = (getattr(p, "first_name", "") or "").strip()
                            p_ln = (getattr(p, "last_name", "") or "").strip()
                            p_dn = f"{p_fn} {p_ln}".strip()
                            p_un = getattr(p, "username", None)
                            self.record_group_member(chat_id, int(p_id), p_dn or f"کاربر {p_id}", p_un)
            except Exception as exc:
                log.debug("خطا در واکشی شرکت‌کنندگان در handle_chat: %s", exc)

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
            target_user_info=target_user_info,
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
            messages = self._build_messages(
                req.chat_id, req.sender_id, req.text, target_user_info=req.target_user_info
            )
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

            # تصفیه تگ‌های تفکر مدل‌های استدلالی مانند DeepSeek-R1 (<think>...</think>)
            cleaned_text = brand.clean_model_output(response.text)
            reply_text = cleaned_text if cleaned_text else response.text

            # مصرف سهمیه گروه فقط پس از دریافت پاسخ موفق
            day = self._day()
            limit = self.store.get_daily_quota(self.cfg.ai_daily_quota, chat_id=req.chat_id)
            self.store.ai_consume_quota(req.chat_id, day, limit)

            # ثبت تاریخچه گفت‌وگو منحصراً برای همین کاربر در همین گروه (با متن تمیز)
            self._push_history(req.chat_id, req.sender_id, "user", req.text)
            self._push_history(req.chat_id, req.sender_id, "assistant", reply_text)

            for chunk in brand.chunk_lines(reply_text.split("\n"), self.cfg.max_message_chars):
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
