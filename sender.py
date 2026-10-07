"""
ارسال «پیام معرفی» با نقل‌قول شیشه‌ای (Quote) + Bold — از طریق API واقعی سروش پلاس.

اینجا هیچ endpoint ساختگی‌ای وجود ندارد؛ همه‌چیز روی همان درخواست واقعی MTProto
ساخته می‌شود که خودِ کلاینت رسمی سروش پلاس هم استفاده می‌کند:

    messages.sendMessage#280d096f flags:# ... peer:InputPeer
        reply_to:flags.0?InputReplyTo message:string random_id:long
        entities:flags.3?Vector<MessageEntity> ... = Updates;

زنجیره‌ی تلاش (Fallback) — چون رفتار سرور سروش ممکن است در نسخه‌های مختلف فرق کند:

    ۱) blockquote + bold   (نقل‌قول شیشه‌ای واقعی + متن‌های بولد)   ← پیش‌فرض
    ۲) bold                (اگر سرور entity نقل‌قول را نپذیرفت)
    ۳) متن ساده            (اگر entityها به‌هر دلیل رد شدند)

و در حالت اختیاری "reply": اول «ریپلای + quote_text» (همان قابلیتِ
«انتخاب متن → سه‌نقطه → نقل قول» در سروش پلاس) امتحان می‌شود و اگر سرور
آن را نپذیرفت، به حالت blockquote برمی‌گردد.

نتیجه‌ی هر تلاش لاگ می‌شود تا در عمل مشخص باشد کدام حالت واقعاً کار می‌کند.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from splusthon import functions, types

import brand
from config import Config

log = logging.getLogger("acod.sender")


async def resolve_peer(event):
    """استخراج InputPeer مقصد از یک ایونت پیام (مشترک بین core و سرویس AI)."""
    getter = getattr(event, "get_input_chat", None)
    if callable(getter):
        try:
            peer = await getter()
            if peer is not None:
                return peer
        except Exception:  # noqa: BLE001 — در بدترین حالت به chat_id برمی‌گردیم
            log.debug("get_input_chat ناموفق بود؛ از chat_id استفاده می‌شود.")
    return getattr(event, "chat_id", None)


@dataclass
class SendReport:
    ok: bool
    mode: Optional[str] = None
    message_id: Optional[int] = None
    attempts: list[dict] = field(default_factory=list)
    error: Optional[str] = None


def _extract_message_id(result: Any) -> Optional[int]:
    """استخراج شناسه‌ی پیام ارسال‌شده از پاسخ سرور (به‌صورت best-effort)."""
    if result is None:
        return None
    if isinstance(result, types.UpdateShortSentMessage):
        return result.id
    updates = getattr(result, "updates", None) or []
    for upd in updates:
        for attr in ("message",):
            msg = getattr(upd, attr, None)
            if isinstance(msg, types.Message):
                return msg.id
        inner = getattr(upd, "update", None)
        if isinstance(inner, types.UpdateNewMessage) and isinstance(
            getattr(inner, "message", None), types.Message
        ):
            return inner.message.id
        if isinstance(inner, types.UpdateNewChannelMessage) and isinstance(
            getattr(inner, "message", None), types.Message
        ):
            return inner.message.id
    return None


class BrandSender:
    """ارسال پیام معرفی با نقل‌قول شیشه‌ای و Bold."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def send(
        self,
        client,
        chat,
        *,
        reply_to_msg_id: Optional[int] = None,
        quote_from_msg_id: Optional[int] = None,
    ) -> SendReport:
        """ارسال پیام معرفی به چت مشخص.

        Args:
            client: نمونه‌ی SoroushClient (یا هر شیئی با get_input_entity و __call__).
            chat: شناسه/موجودیت چت مقصد (گروه).
            reply_to_msg_id: اگر مقدار داشته باشد، پیام به‌صورت ریپلای معمولی ارسال می‌شود.
            quote_from_msg_id: شناسه‌ی پیام مرجع برای حالت نقل‌قول واقعی (quote_text).
        """
        peer = await self._resolve_peer(client, chat)
        attempts = self._build_plan(
            quote_from_msg_id=quote_from_msg_id, reply_to_msg_id=reply_to_msg_id
        )
        report = SendReport(ok=False, attempts=[])

        for name, text, entities, reply_to in attempts:
            try:
                request = functions.messages.SendMessageRequest(
                    peer=peer,
                    message=text,
                    entities=entities or None,
                    reply_to=reply_to,
                    no_webpage=not self.cfg.link_preview,
                )
                result = await client(request)
                report.ok = True
                report.mode = name
                report.message_id = _extract_message_id(result)
                report.attempts.append({"mode": name, "ok": True})
                log.info("پیام معرفی ارسال شد (حالت: %s)", name)
                return report
            except Exception as exc:  # noqa: BLE001 — هر خطای سرور باید fallback را فعال کند
                err = f"{type(exc).__name__}: {exc}"
                report.attempts.append({"mode": name, "ok": False, "error": err})
                report.error = err
                log.warning("تلاش «%s» ناموفق بود → %s", name, err)

        log.error("ارسال پیام معرفی در همه‌ی حالت‌ها ناموفق بود: %s", report.error)
        return report

    # ------------------------------------------------------------------ helpers
    async def send_text(
        self, client, chat, text: str, *, reply_to_msg_id: Optional[int] = None
    ) -> SendReport:
        """ارسال یک پیام متنی ساده (بدون entity).

        برای پیام‌های سیستمی (مثل «تعداد اعضا» / «لیست اعضا» و پاسخ‌های هوش مصنوعی)
        که قالب خاصی لازم ندارند. از همان درخواست واقعی `messages.sendMessage`
        استفاده می‌کند و در صورت نیاز، پیام را به‌صورت Reply می‌فرستد.
        """
        peer = await self._resolve_peer(client, chat)
        report = SendReport(ok=False, attempts=[])
        try:
            result = await client(
                functions.messages.SendMessageRequest(
                    peer=peer,
                    message=text,
                    reply_to=self._plain_reply(reply_to_msg_id),
                    no_webpage=not self.cfg.link_preview,
                )
            )
            report.ok = True
            report.mode = "plain-text"
            report.message_id = _extract_message_id(result)
            report.attempts.append({"mode": "plain-text", "ok": True})
            return report
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            report.error = err
            report.attempts.append({"mode": "plain-text", "ok": False, "error": err})
            log.warning("ارسال پیام متنی ناموفق بود → %s", err)
            return report

    async def send_styled(
        self,
        client,
        chat,
        text: str,
        *,
        reply_to_msg_id: Optional[int] = None,
        quote: bool = True,
    ) -> SendReport:
        """ارسال یک متن دلخواه با «قالب استاندارد پروژه»: Bold (و در حالت پیش‌فرض نقل‌قول شیشه‌ای).

        زنجیره‌ی fallback:
            * ``quote=True``  → blockquote+bold → bold-only → متن ساده
            * ``quote=False`` → bold-only → متن ساده   (بدون هیچ Blockquote)

        بنابراین اگر سرور سروش entity را نپذیرد، ارسال شکست نمی‌خورد.
        با ``reply_to_msg_id`` پیام به‌صورت Reply روی همان پیام فرستاده می‌شود.
        """
        peer = await self._resolve_peer(client, chat)
        bold_only = ("bold-only", brand.quote_bold_entities(text, quote=False, bold=True))
        plan = (
            [
                ("blockquote+bold", brand.quote_bold_entities(text, quote=True, bold=True)),
                bold_only,
                ("plain", []),
            ]
            if quote
            else [bold_only, ("plain", [])]
        )
        report = SendReport(ok=False, attempts=[])

        for name, entities in plan:
            try:
                result = await client(
                    functions.messages.SendMessageRequest(
                        peer=peer,
                        message=text,
                        entities=entities or None,
                        reply_to=self._plain_reply(reply_to_msg_id),
                        no_webpage=not self.cfg.link_preview,
                    )
                )
                report.ok = True
                report.mode = name
                report.message_id = _extract_message_id(result)
                report.attempts.append({"mode": name, "ok": True})
                log.info("پیام قالب‌دار ارسال شد (حالت: %s)", name)
                return report
            except Exception as exc:  # noqa: BLE001 — هر خطای سرور باید fallback را فعال کند
                err = f"{type(exc).__name__}: {exc}"
                report.attempts.append({"mode": name, "ok": False, "error": err})
                report.error = err
                log.warning("تلاش «%s» ناموفق بود → %s", name, err)

        log.error("ارسال پیام قالب‌دار در همه‌ی حالت‌ها ناموفق بود: %s", report.error)
        return report

    async def send_text_chunked(
        self, client, chat, chunks: list[str]
    ) -> list[SendReport]:
        """ارسال چند پیام پشت‌سرهم (برای لیست‌های بلندتر از سقف طول پیام)."""
        reports = []
        for chunk in chunks:
            reports.append(await self.send_text(client, chat, chunk))
        return reports

    @staticmethod
    async def _resolve_peer(client, chat):
        if isinstance(chat, types.TypeInputPeer):
            return chat
        return await client.get_input_entity(chat)

    def _build_plan(self, *, quote_from_msg_id: Optional[int], reply_to_msg_id: Optional[int]):
        """ساخت فهرست تلاش‌ها بر اساس تنظیمات."""
        plan: list[tuple[str, str, list, Any]] = []
        full = brand.FULL_TEXT
        body_only = brand.build_text(include_quote_line=False)

        quote_mode = (self.cfg.quote_mode or "entity").lower()

        # --- حالت اختیاری: نقل‌قول واقعی با ریپلای + quote_text ---
        if quote_mode == "reply" and quote_from_msg_id:
            reply_to = types.InputReplyToMessage(
                reply_to_msg_id=quote_from_msg_id,
                quote_text=brand.QUOTE_LINE,
                quote_entities=brand.build_quote_entities(),
            )
            plan.append(
                (
                    "reply_quote+bold",
                    body_only,
                    brand.build_entities(body_only, quote_line=False, bold_body=True),
                    reply_to,
                )
            )
            if not self.cfg.quote_reply_fallback_to_entity:
                return plan

        # --- نقل‌قول شیشه‌ای به‌صورت entity + متن‌های Bold ---
        if quote_mode in ("entity", "reply"):
            plan.append(
                (
                    "blockquote+bold",
                    full,
                    brand.build_entities(full, quote_line=True, bold_body=True),
                    self._plain_reply(reply_to_msg_id),
                )
            )

        # --- فقط Bold ---
        plan.append(
            (
                "bold-only",
                full,
                brand.build_entities(full, quote_line=False, bold_body=True),
                self._plain_reply(reply_to_msg_id),
            )
        )

        # --- متن ساده ---
        plan.append(("plain", full, [], self._plain_reply(reply_to_msg_id)))
        return plan

    @staticmethod
    def _plain_reply(reply_to_msg_id: Optional[int]):
        if not reply_to_msg_id:
            return None
        return types.InputReplyToMessage(reply_to_msg_id=int(reply_to_msg_id))
