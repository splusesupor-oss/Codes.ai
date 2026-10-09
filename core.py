"""
منطق ربات: دستورات گروهی، مدیریت هوش مصنوعی، فعال‌سازی گروه و رفتار چت خصوصی.

  ۱) «ai cod»  → اولین کاربر مالک سراسری (Global Owner) می‌شود؛ ارسال‌های بعدی تاگل روشن/خاموش.
  ۲) «Tery ai» (با Reply) → ثبت مالک ربات (Registered Bot Owner) توسط مالک سراسری.
  ۳) «N پیام» / «N عضو» / «N day» → تنظیمات سهمیه، سقف اعضا و انقضا فقط توسط مالک سراسری.
  ۴) «ai x cod» → فعال‌سازی ربات در گروه + ارسال پیام اعلان با مشخصات گروه و مدیران.
  ۵) «ai online» / «ai of» → روشن/خاموش کردن هوش مصنوعی گروه (مالک سراسری یا مالک ثبت‌شده).
  ۶) «ai list» / «ai list x» / «ai L» → مدیریت کاربران مجاز هوش مصنوعی در هر گروه.
  ۷) «راهنما» → راهنمای فرمت‌دار هوش مصنوعی.
  ۸) «ai plun» → نمایش سهمیه باقیمانده روزانه گروه.
  ۹) «ai» → پاسخ «جانم 👾».
  ۱۰) پیام خصوصی (PV) → ثبت آمار، دستورات مدیریتی مالک، معرفی یک‌بار و منوی ۶ گزینه‌ای.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import unicodedata
from typing import Optional

import brand
from ai_service import GroupAI
from brand import chunk_lines
from config import Config
from models import DEFAULT_MODEL_PROFILE_ID, MODEL_PROFILES, get_model_profile
from sender import BrandSender, SendReport
from splusthon import types
from storage import OwnerStore

log = logging.getLogger("acod.core")

try:
    import zoneinfo
    TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")
except Exception:
    # فال‌بک برای محیط‌هایی مثل Termux / Android / ویندوز که پکیج tzdata ندارند.
    # ساعت رسمی ایران (تهران) به صورت ثابت UTC+03:30 است (بدون DST).
    TEHRAN_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30), name="Asia/Tehran")
_ZWNJ = "\u200c"
_ARABIC_PAIRS = str.maketrans({"ي": "ی", "ك": "ک", "ۀ": "ه", "أ": "ا", "إ": "ا"})
PERSIAN_DIGITS_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _base_clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ARABIC_PAIRS)
    return text.strip().lower()


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", _base_clean(text).replace(_ZWNJ, "")).strip()


def _normalization_variants(text: str) -> set[str]:
    base = _base_clean(text)
    return {
        re.sub(r"\s+", " ", base.replace(_ZWNJ, "")).strip(),
        re.sub(r"\s+", " ", base.replace(_ZWNJ, " ")).strip(),
    }


def parse_persian_int(text: str) -> Optional[int]:
    """تبدیل اعداد انگلیسی/فارسی به عدد صحیح معتبر (> 0 و <= 1,000,000)."""
    cleaned = text.strip().translate(PERSIAN_DIGITS_TABLE)
    if cleaned.isdigit():
        val = int(cleaned)
        if 0 < val <= 1_000_000:
            return val
    return None


def match_global_config_command(text: str) -> Optional[tuple[str, int]]:
    """تشخیص دستورات تنظیم سهمیه، سقف اعضا و انقضا:

    * «N پیام» / «N پیام»  → ('quota', N)
    * «N عضو»  / «N عضو»   → ('max_users', N)
    * «N day» / «N روز»    → ('expire_days', N)
    """
    if not text or not text.strip():
        return None
    raw = normalize_text(text)

    m_quota = re.match(r"^(\d+|[۰-۹]+)\s*پیام$", raw)
    if m_quota:
        val = parse_persian_int(m_quota.group(1))
        if val is not None:
            return ("quota", val)

    m_users = re.match(r"^(\d+|[۰-۹]+)\s*عضو$", raw)
    if m_users:
        val = parse_persian_int(m_users.group(1))
        if val is not None:
            return ("max_users", val)

    m_day = re.match(r"^(\d+|[۰-۹]+)\s*(?:day|روز)$", raw)
    if m_day:
        val = parse_persian_int(m_day.group(1))
        if val is not None:
            return ("expire_days", val)

    return None


def is_tery_ai_command(text: str) -> bool:
    if not text:
        return False
    return normalize_text(text) == "tery ai"


def is_ai_xcod_command(text: str) -> bool:
    if not text:
        return False
    return normalize_text(text) == "ai x cod"


def is_help_command(text: str) -> bool:
    if not text:
        return False
    return normalize_text(text) == "راهنما"


def is_plun_command(text: str) -> bool:
    if not text:
        return False
    return normalize_text(text) == "ai plun"


def is_expiration_list_command(text: str) -> bool:
    if not text:
        return False
    norm = normalize_text(text)
    return norm in ("لیست انقضا", "لیست انقضاء", "list engheza", "لیست انقضاها")


def is_ai_single_command(text: str) -> bool:
    if not text:
        return False
    return normalize_text(text) == "ai"


def owner_command_variants(cfg: Config) -> set:
    commands = (cfg.owner_command,) + tuple(cfg.owner_command_aliases)
    variants = set()
    for command in commands:
        variants |= _normalization_variants(command)
    return variants


def match_command(text: str, cfg: Config) -> Optional[str]:
    if not text or not text.strip():
        return None
    given = _normalization_variants(text)
    if given & owner_command_variants(cfg):
        return "owner"
    if given & _normalization_variants(cfg.kodrez_command):
        return "kodrez"
    return None


def match_pv_admin_command(text: str, cfg: Config) -> Optional[str]:
    if not text or not text.strip():
        return None
    given = _normalization_variants(text)
    if given & _normalization_variants(cfg.pv_count_command):
        return "count"
    if given & _normalization_variants(cfg.pv_list_command):
        return "list"
    return None


def match_ai_command(text: str, cfg: Config) -> Optional[str]:
    if not text or not text.strip():
        return None
    norm = normalize_text(text)
    if norm == "ai list x":
        return "listx"
    if norm == "ai list":
        return "list"
    if norm == "ai online":
        return "online"
    if norm == "ai of":
        return "of"
    if norm == "ai l":
        return "l"
    if norm == "ai model":
        return "model"
    if norm.startswith("ai model "):
        return norm[3:]  # e.g. "model 1", "model 2", "model 3"
    return None


def match_menu_option(text: str) -> Optional[str]:
    if not text or not text.strip():
        return None
    given = _normalization_variants(text)
    for option in brand.MENU_OPTIONS:
        if given & _normalization_variants(option):
            return option
    return None


def pv_user_label(user, unknown_name: str = "کاربر بدون نام") -> str:
    username = (getattr(user, "username", None) or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    display_name = (getattr(user, "display_name", None) or "").strip()
    return display_name or unknown_name


def format_pv_user_list(users, unknown_name: str = "کاربر بدون نام") -> list[str]:
    return [
        f"{index} : {pv_user_label(user, unknown_name)}"
        for index, user in enumerate(users, start=1)
    ]


def _format_user_display(user) -> str:
    username = (getattr(user, "username", None) or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    u_id = getattr(user, "id", None)
    return f"کاربر {u_id}" if u_id else "کاربر بدون نام"


class BotCore:
    """هسته‌ی ربات."""

    def __init__(
        self,
        cfg: Config,
        store: OwnerStore,
        sender: Optional[BrandSender] = None,
        ai: Optional[GroupAI] = None,
        *,
        now_provider=None,
    ):
        self.cfg = cfg
        self.store = store
        self.sender = sender or BrandSender(cfg)
        self.ai = ai or GroupAI(cfg, store, self.sender)
        self._now_provider = now_provider

    def _now(self, tz=None):
        if self._now_provider is not None:
            return self._now_provider(tz) if tz else self._now_provider()
        if hasattr(self.ai, "_now") and callable(self.ai._now):
            return self.ai._now(tz) if tz else self.ai._now()
        return _dt.datetime.now(tz) if tz else _dt.datetime.now()

    # ------------------------------------------------------------------ entry
    async def on_new_message(self, client, event) -> None:
        if getattr(event, "out", False):
            return

        if getattr(event, "is_private", None) is True:
            await self._handle_private(client, event)
            return

        text = getattr(event, "raw_text", None) or ""
        is_group = bool(getattr(event, "is_group", False))
        if not text.strip():
            return

        sender_id = int(getattr(event, "sender_id", 0) or 0)
        norm_text = normalize_text(text)
        current_time = self._now()

        # -------------------------------------------------------------
        # الف) بررسی انقضای سرویس ربات
        # -------------------------------------------------------------
        if self.store.is_expired(current_time):
            if self.store.is_owner(sender_id):
                config_cmd = match_global_config_command(text)
                if config_cmd and config_cmd[0] == "expire_days":
                    await self._handle_expire_config(client, event, config_cmd[1])
                    return
                if match_command(text, self.cfg) == "owner":
                    await self._handle_owner_command(client, event)
                    return
            if is_ai_xcod_command(norm_text) or getattr(event, "is_reply", False):
                await self.sender.send_styled(
                    client,
                    await self._peer_of(event),
                    brand.BOT_EXPIRED_TEXT,
                    reply_to_msg_id=getattr(event, "id", None),
                    quote=False,
                )
            return

        # -------------------------------------------------------------
        # ب) بررسی فعال بودن سراسری ربات
        # -------------------------------------------------------------
        if not self.store.is_active(current_time):
            command = match_command(text, self.cfg)
            if command == "owner":
                await self._handle_owner_command(client, event)
            else:
                log.debug("ربات خاموش است؛ پیام گروهی «%s» نادیده گرفته شد.", text[:40])
            return

        # -------------------------------------------------------------
        # ج) دستورات پیکربندی مالک سراسری: «N پیام» / «N عضو» / «N day»
        # -------------------------------------------------------------
        config_cmd = match_global_config_command(text)
        if config_cmd:
            if self.store.is_owner(sender_id):
                cmd_type, val = config_cmd
                if cmd_type == "quota":
                    await self._handle_quota_config(client, event, val)
                    return
                if cmd_type == "max_users":
                    await self._handle_max_users_config(client, event, val)
                    return
                if cmd_type == "expire_days":
                    await self._handle_expire_config(client, event, val)
                    return
            else:
                log.info("کاربر عادی %s تلاش کرد مقادیر پیکربندی را تغییر دهد؛ نادیده گرفته شد.", sender_id)

        # -------------------------------------------------------------
        # د) دستور «Tery ai» — ثبت مالک ربات (فقط مالک سراسری)
        # -------------------------------------------------------------
        if is_tery_ai_command(norm_text):
            if self.store.is_owner(sender_id):
                await self._handle_tery_ai(client, event)
            else:
                log.info("دستور Tery ai از غیرمالک سراسری %s رد شد", sender_id)
            return

        # -------------------------------------------------------------
        # هـ) دستور «ai x cod» — فعال‌سازی گروه (فقط مالک سراسری)
        # -------------------------------------------------------------
        if is_ai_xcod_command(norm_text):
            if self.store.is_owner(sender_id):
                await self._handle_ai_xcod(client, event)
            else:
                log.info("دستور ai x cod از غیرمالک سراسری %s رد شد", sender_id)
            return

        # -------------------------------------------------------------
        # هـ/۱) دستور «لیست انقضا» — نمایش فهرست انقضای گروه‌ها (فقط مالک سراسری)
        # -------------------------------------------------------------
        if is_expiration_list_command(norm_text):
            if self.store.is_owner(sender_id):
                await self._handle_expiration_list(client, event)
            else:
                log.info("دستور لیست انقضا از غیرمالک سراسری %s رد شد", sender_id)
            return

        # -------------------------------------------------------------
        # و) دستور «راهنما» — پیام راهنمای فارسی
        # -------------------------------------------------------------
        if is_help_command(norm_text):
            help_text, entities = brand.build_help_message()
            await self.sender.send_entities(
                client,
                await self._peer_of(event),
                help_text,
                entities,
                reply_to_msg_id=getattr(event, "id", None),
            )
            return

        # -------------------------------------------------------------
        # ز) دستور «ai plun» — باقیمانده سهمیه روزانه گروه
        # -------------------------------------------------------------
        if is_plun_command(norm_text):
            await self._handle_plun(client, event)
            return

        # -------------------------------------------------------------
        # ح) دستور «ai» — صدا زدن ربات
        # -------------------------------------------------------------
        if is_ai_single_command(norm_text):
            await self.sender.send_text(
                client,
                await self._peer_of(event),
                brand.AI_CALL_RESPONSE,
                reply_to_msg_id=getattr(event, "id", None),
            )
            return

        # -------------------------------------------------------------
        # ط) دستورهای مدیریت هوش مصنوعی: ai online / ai of / ai list / ai list x / ai L
        # -------------------------------------------------------------
        ai_command = match_ai_command(text, self.cfg)
        if ai_command is not None:
            if is_group:
                await self.ai.handle_admin_command(client, event, ai_command)
            return

        # -------------------------------------------------------------
        # ی) دستورات سنتی ربات: ai cod و کدرز
        # -------------------------------------------------------------
        command = match_command(text, self.cfg)
        if command is not None:
            if self.cfg.groups_only and not is_group:
                return
            if command == "owner":
                await self._handle_owner_command(client, event)
            elif command == "kodrez":
                await self._handle_kodrez(client, event)
            return

        # -------------------------------------------------------------
        # ک) گفت‌وگو با هوش مصنوعی (فقط Reply در گروه فعال)
        # -------------------------------------------------------------
        if is_group and getattr(event, "is_reply", False):
            await self.ai.handle_chat(client, event)

    # ------------------------------------------------------- دستورات پیکربندی مالک
    async def _handle_quota_config(self, client, event, quota: int) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        is_group = bool(getattr(event, "is_group", False) or (chat_id < 0))
        if is_group:
            self.store.set_group_daily_quota(chat_id, quota)
            max_users = self.store.get_max_allowed_users(self.cfg.ai_max_allowed_users, chat_id=chat_id)
            msg = (
                f"{brand.format_quota_ceiling(quota, max_users)}\n\n"
                f"سهمیه روزانه هوش مصنوعی این گروه به {quota} پیام تنظیم شد."
            )
            log.info("سهمیه روزانه گروه %s به %s پیام تغییر یافت", chat_id, quota)
        else:
            self.store.set_daily_quota(quota)
            max_users = self.store.get_max_allowed_users(self.cfg.ai_max_allowed_users)
            msg = (
                f"{brand.format_quota_ceiling(quota, max_users)}\n\n"
                f"سهمیه روزانه هوش مصنوعی به {quota} پیام تنظیم شد."
            )
            log.info("سهمیه روزانه سراسری به %s پیام تغییر یافت", quota)

        await self.sender.send_styled(
            client, await self._peer_of(event), msg, quote=False,
            reply_to_msg_id=getattr(event, "id", None),
        )

    async def _handle_max_users_config(self, client, event, max_users: int) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        is_group = bool(getattr(event, "is_group", False) or (chat_id < 0))
        if is_group:
            self.store.set_group_max_allowed_users(chat_id, max_users)
            quota = self.store.get_daily_quota(self.cfg.ai_daily_quota, chat_id=chat_id)
            msg = (
                f"{brand.format_quota_ceiling(quota, max_users)}\n\n"
                f"سقف اعضای مجاز هوش مصنوعی این گروه به {max_users} عضو تنظیم شد."
            )
            log.info("سقف اعضای مجاز گروه %s به %s عضو تغییر یافت", chat_id, max_users)
        else:
            self.store.set_max_allowed_users(max_users)
            quota = self.store.get_daily_quota(self.cfg.ai_daily_quota)
            msg = (
                f"{brand.format_quota_ceiling(quota, max_users)}\n\n"
                f"سقف اعضای مجاز هوش مصنوعی به {max_users} عضو تنظیم شد."
            )
            log.info("سقف اعضای مجاز سراسری به %s عضو تغییر یافت", max_users)

        await self.sender.send_styled(
            client, await self._peer_of(event), msg, quote=False,
            reply_to_msg_id=getattr(event, "id", None),
        )

    async def _handle_expire_config(self, client, event, days: int) -> None:
        expires_at = self._now(TEHRAN_TZ) + _dt.timedelta(days=days)
        self.store.set_expiration(expires_at)
        msg = f"اعتبار ربات برای {days} روز تنظیم شد."
        log.info("انقضای ربات به %s روز دیگر (%s) تنظیم شد", days, expires_at.isoformat())
        await self.sender.send_styled(
            client, await self._peer_of(event), msg, quote=False,
            reply_to_msg_id=getattr(event, "id", None),
        )

    # ------------------------------------------------------- ثبت مالک ربات (Tery ai)
    async def _handle_tery_ai(self, client, event) -> None:
        reply_message = await self.ai._get_reply_message(event)
        target_id = int(getattr(reply_message, "sender_id", 0) or 0) if reply_message else 0

        if not target_id:
            await self.sender.send_styled(
                client,
                await self._peer_of(event),
                brand.AI_NEED_REPLY_REG_OWNER_TEXT,
                quote=False,
                reply_to_msg_id=getattr(event, "id", None),
            )
            return

        label = self.ai._user_label(reply_message, target_id, brand.AI_USER_FALLBACK_PREFIX)
        target_username = getattr(getattr(reply_message, "sender", None), "username", None)
        sender_id = int(getattr(event, "sender_id", 0) or 0)

        self.store.set_registered_bot_owner(
            target_id,
            username=target_username,
            display_name=label,
            registered_by=sender_id,
        )
        log.info("مالک ثبت‌شده ثبت شد: user_id=%s توسط %s", target_id, sender_id)
        await self.sender.send_styled(
            client,
            await self._peer_of(event),
            brand.AI_REGISTERED_OWNER_TEXT.format(user=label),
            quote=False,
            reply_to_msg_id=getattr(event, "id", None),
        )

    # ------------------------------------------------------- فعال‌سازی گروه (ai x cod)
    async def _handle_ai_xcod(self, client, event) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        sender_id = int(getattr(event, "sender_id", 0) or 0)

        group_name, owner_label, admins = await self._fetch_group_metadata(client, event)
        self.store.activate_group(chat_id, sender_id, group_name=group_name)
        self.store.ai_set_enabled(chat_id, True)

        now_dt = self._now(TEHRAN_TZ) if hasattr(self, "_now") else _dt.datetime.now(TEHRAN_TZ)
        act_date_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        exp_dt = self.store.get_group_expiration(chat_id)
        if exp_dt:
            exp_date_str = exp_dt.astimezone(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        else:
            exp_date_str = "تنظیم نشده (نامحدود)"

        text, entities = brand.build_announcement_message(
            group_name,
            owner_label,
            admins,
            activation_date=act_date_str,
            expiration_date=exp_date_str,
        )

        log.info("گروه %s فعال شد (نام: %s، مالک: %s، مدیران: %s)", chat_id, group_name, owner_label, len(admins))
        report = await self.sender.send_entities(
            client,
            await self._peer_of(event),
            text,
            entities,
            reply_to_msg_id=getattr(event, "id", None),
        )
        self._log_report(report)

    async def _fetch_group_metadata(self, client, event) -> tuple[str, str, list[str]]:
        group_name = "گروه"
        owner_label = "مالک یافت نشد"
        admin_labels: list[str] = []
        seen_admin_ids = set()

        try:
            chat = getattr(event, "chat", None)
            if not chat and hasattr(event, "get_chat"):
                chat = await event.get_chat()
            if chat and getattr(chat, "title", None):
                group_name = chat.title
        except Exception as e:
            log.debug("خطا در دریافت نام گروه: %s", e)

        chat_id = getattr(event, "chat_id", None)
        try:
            participants = None
            if hasattr(client, "get_participants"):
                try:
                    participants = await client.get_participants(
                        chat_id, filter=types.ChannelParticipantsAdmins()
                    )
                except Exception:
                    participants = await client.get_participants(chat_id)
            elif hasattr(client, "iter_participants"):
                participants = [p async for p in client.iter_participants(chat_id)]

            if participants:
                creator_found = False
                for u in participants:
                    u_id = getattr(u, "id", None)
                    p_info = getattr(u, "participant", None)
                    is_creator = isinstance(
                        p_info, (types.ChannelParticipantCreator, types.ChatParticipantCreator)
                    ) or getattr(p_info, "is_creator", False)

                    u_label = _format_user_display(u)

                    if is_creator and not creator_found:
                        owner_label = u_label
                        creator_found = True
                        if u_id:
                            seen_admin_ids.add(u_id)
                    else:
                        is_admin = isinstance(
                            p_info, (types.ChannelParticipantAdmin, types.ChatParticipantAdmin)
                        ) or getattr(p_info, "is_admin", False)
                        if is_admin and (u_id is None or u_id not in seen_admin_ids):
                            admin_labels.append(u_label)
                            if u_id:
                                seen_admin_ids.add(u_id)
        except Exception as e:
            log.debug("خطا در دریافت لیست مدیران گروه: %s", e)

        return group_name, owner_label, admin_labels

    # ------------------------------------------------------- سهمیه گروه (ai plun)
    async def _handle_plun(self, client, event) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        day = self.ai._day()
        limit = self.store.get_daily_quota(self.cfg.ai_daily_quota, chat_id=chat_id)
        used = self.store.ai_used_quota(chat_id, day)
        remaining = max(0, limit - used)

        msg = brand.format_remaining_quota(remaining, limit, used)
        await self.sender.send_styled(
            client,
            await self._peer_of(event),
            msg,
            quote=False,
            reply_to_msg_id=getattr(event, "id", None),
        )

    # ------------------------------------------------------- لیست انقضای گروه‌ها
    async def _handle_expiration_list(self, client, event) -> None:
        records = self.store.list_group_records()
        if not records:
            await self.sender.send_styled(
                client,
                await self._peer_of(event),
                "📋 هیچ گروهی تاکنون در رجیستری ربات ثبت نشده است.",
                reply_to_msg_id=getattr(event, "id", None),
                quote=False,
            )
            return

        now_dt = self._now(TEHRAN_TZ) if hasattr(self, "_now") else _dt.datetime.now(TEHRAN_TZ)
        day_str = self.ai._day()

        lines = ["📋 𝗟𝗜𝗦𝗧 𝗘𝗫𝗣𝗜𝗥𝗔𝗧𝗜𝗢𝗡 | لیست گروه‌های ثبت‌شده:", ""]
        for idx, rec in enumerate(records, start=1):
            exp_dt = self.store.get_group_expiration(rec.chat_id)
            if exp_dt:
                exp_local = exp_dt.astimezone(TEHRAN_TZ)
                exp_str = exp_local.strftime("%Y-%m-%d %H:%M:%S")
                if now_dt >= exp_dt:
                    status_text = "❌ منقضی شده"
                    remaining_str = "منقضی شده"
                else:
                    diff = exp_dt - now_dt
                    days = diff.days
                    hours = diff.seconds // 3600
                    status_text = "✅ فعال" if rec.is_active else "⏸ غیرفعال"
                    remaining_str = f"{days} روز و {hours} ساعت"
            else:
                exp_str = "تنظیم نشده (بدون انقضا)"
                remaining_str = "نامحدود"
                status_text = "✅ فعال" if rec.is_active else "⏸ غیرفعال"

            prof = get_model_profile(rec.model_profile) or MODEL_PROFILES[DEFAULT_MODEL_PROFILE_ID]
            quota_limit = self.store.get_daily_quota(self.cfg.ai_daily_quota, chat_id=rec.chat_id)
            used = self.store.ai_used_quota(rec.chat_id, day_str)
            rem_quota = max(0, quota_limit - used)

            lines.append(f"☲ ردیف {idx}: {rec.group_name or 'بدون نام'}")
            lines.append(f"• شناسه چت: {rec.chat_id}")
            lines.append(f"• کد رجیستری: {rec.record_id}")
            lines.append(f"• وضعیت: {status_text}")
            lines.append(f"• مدل: مدل {prof.id} — {prof.name}")
            lines.append(f"• تاریخ فعال‌سازی: {rec.activated_at}")
            lines.append(f"• تاریخ انقضا: {exp_str}")
            lines.append(f"• زمان باقیمانده: {remaining_str}")
            lines.append(f"• سهمیه روزانه: {quota_limit} (مصرف: {used} | باقیمانده: {rem_quota})")
            lines.append("")

        full_text = "\n".join(lines).strip()
        for chunk in brand.chunk_lines(full_text.split("\n"), self.cfg.max_message_chars):
            await self.sender.send_styled(
                client,
                await self._peer_of(event),
                chunk,
                reply_to_msg_id=getattr(event, "id", None),
                quote=False,
            )

    # ------------------------------------------------------- پیام خصوصی (PV)
    async def _handle_private(self, client, event) -> None:
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        text = getattr(event, "raw_text", None) or ""
        norm_text = normalize_text(text)
        current_time = self._now()

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

        if self.store.has_owner() and self.store.is_owner(sender_id):
            if match_command(text, self.cfg) == "owner":
                new_state = not self.store.is_global_suspended()
                self.store.set_global_suspended(new_state)
                text_resp = brand.BOT_OFF_TEXT if new_state else brand.BOT_ON_TEXT
                log.info("وضعیت ربات از PV تغییر کرد: suspended=%s (مالک %s)",
                         new_state, sender_id)
                self._log_report(await self.sender.send_text(
                    client, await self._peer_of(event), text_resp
                ))
                return

            config_cmd = match_global_config_command(text)
            if config_cmd:
                cmd_type, val = config_cmd
                if cmd_type == "quota":
                    await self._handle_quota_config(client, event, val)
                    return
                if cmd_type == "max_users":
                    await self._handle_max_users_config(client, event, val)
                    return
                if cmd_type == "expire_days":
                    await self._handle_expire_config(client, event, val)
                    return

            if is_expiration_list_command(norm_text):
                await self._handle_expiration_list(client, event)
                return

            if self.store.is_active(current_time):
                command = match_pv_admin_command(text, self.cfg)
                if command == "count":
                    await self._send_pv_count(client, event)
                    return
                if command == "list":
                    await self._send_pv_list(client, event)
                    return

        if not self.cfg.private_auto_reply:
            return

        if not self.store.is_active(current_time):
            log.debug("ربات خاموش است؛ پیام PV از %s نادیده گرفته شد.", sender_id)
            return

        if is_help_command(norm_text):
            help_text, entities = brand.build_help_message()
            await self.sender.send_entities(
                client, await self._peer_of(event), help_text, entities,
                reply_to_msg_id=getattr(event, "id", None)
            )
            return

        if is_first_message:
            await self._send_intro_and_menu(client, event)
            return

        option = match_menu_option(text)
        if option:
            await self._send_pv_answer(client, event, option)
            return

    async def _send_intro_and_menu(self, client, event) -> None:
        log.info("اولین پیام کاربر %s در PV → معرفی + منو",
                 getattr(event, "sender_id", None))
        self._log_report(await self._send_brand(client, event))
        self._log_report(
            await self.sender.send_styled(client, await self._peer_of(event), brand.MENU_TEXT)
        )

    async def _send_pv_answer(self, client, event, option: str) -> None:
        text = brand.PV_REPLIES[option]
        log.info("گزینه «%s» از کاربر %s → ارسال پاسخ",
                 option, getattr(event, "sender_id", None))
        self._log_report(
            await self.sender.send_styled(client, await self._peer_of(event), text)
        )

    async def _send_pv_count(self, client, event) -> None:
        users = self.store.list_pv_users()
        count = len(users)
        header = f"{self.cfg.pv_count_command} : {count}"
        log.info("دستور «%s» توسط مالک → %s کاربر", self.cfg.pv_count_command, count)

        if not users:
            report = await self.sender.send_text(
                client, await self._peer_of(event), header
            )
            self._log_report(report)
            return

        lines = format_pv_user_list(users, self.cfg.pv_unknown_name)
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
            self.store.set_global_suspended(False)
            log.info("🏆 مالک سراسری ثبت شد: user_id=%s (chat_id=%s)", user_id, chat_id)
            report = await self._send_brand(client, event)
            self._log_report(report)
            return

        if result.reason == "already_same_owner":
            new_state = not self.store.is_global_suspended()
            self.store.set_global_suspended(new_state)
            text = brand.BOT_OFF_TEXT if new_state else brand.BOT_ON_TEXT
            log.info("وضعیت ربات تغییر کرد: suspended=%s (توسط مالک %s)",
                     new_state, user_id)
            self._log_report(await self.sender.send_text(
                client, await self._peer_of(event), text
            ))
            return

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
            except Exception:
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
