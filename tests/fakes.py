"""
ابزارهای جعلی (Fake) برای تست کامل منطق ربات بدون نیاز به لاگین واقعی.

این‌ها فقط «شبیه‌ساز» ورودی/خروجی هستند؛ هیچ درخواست شبکه‌ای انجام نمی‌شود و
هیچ endpoint ساختگی‌ای هم به پروژه اضافه نمی‌کنند. کلاینتی که در تست‌ها استفاده
می‌شود، همان امضای SoroushClient را دارد (get_input_entity + __call__) و درخواست‌های
واقعی messages.SendMessageRequest را ثبت می‌کند.
"""

from __future__ import annotations

from typing import Optional

from splusthon import types


class FakeClient:
    """کلاینت جعلی: درخواست‌های واقعی MTProto را فقط ثبت می‌کند."""

    def __init__(self, *, reject_blockquote: bool = False, reject_all: bool = False):
        self.requests: list = []
        self.reject_blockquote = reject_blockquote
        self.reject_all = reject_all
        self._next_id = 100

    async def get_input_entity(self, chat):
        # مثل خود SPlusthon: شناسه‌ی مثبت = کاربر (PV)، منفی = گروه
        chat_id = int(chat)
        if chat_id > 0:
            return types.InputPeerUser(user_id=chat_id, access_hash=0)
        return types.InputPeerChat(chat_id=abs(chat_id))

    async def __call__(self, request, ordered=False):
        if self.reject_all:
            raise RuntimeError("RPCError: InternalServerError (fake)")
        entities = getattr(request, "entities", None) or []
        if self.reject_blockquote and any(
            isinstance(e, types.MessageEntityBlockquote) for e in entities
        ):
            raise RuntimeError("RPCError: 400 ENTITIES_INVALID (fake)")
        self.requests.append(request)
        self._next_id += 1
        msg = types.Message(
            id=self._next_id,
            peer_id=types.PeerChat(chat_id=1),
            date=None,
            message=getattr(request, "message", "") or "",
        )
        return types.Updates(
            updates=[types.UpdateNewMessage(message=msg, pts=1, pts_count=1)],
            users=[],
            chats=[],
            date=None,
            seq=0,
        )

    # ------------------------------------------------------------ ابزار تحلیل
    def last_request(self):
        return self.requests[-1] if self.requests else None

    def sent_messages(self) -> list[dict]:
        out = []
        for req in self.requests:
            out.append(
                {
                    "message": req.message,
                    "entities": getattr(req, "entities", None) or [],
                    "reply_to": getattr(req, "reply_to", None),
                    "peer": getattr(req, "peer", None),
                }
            )
        return out


class FakeSender:
    """کاربر سروش (فقط اطلاعات نمایشی برای لاگ/حافظه)."""

    def __init__(self, user_id: int, first_name: str = "User", last_name: str = "",
                 username: Optional[str] = None):
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username


class FakeEvent:
    """شبیه‌ساز events.NewMessage با همان attributeهایی که core استفاده می‌کند."""

    def __init__(
        self,
        text: str,
        *,
        user_id: int,
        chat_id: int,
        msg_id: int = 1,
        is_group: bool = True,
        is_private: Optional[bool] = None,
        out: bool = False,
        username: Optional[str] = None,
        display_name: Optional[str] = None,
    ):
        self.raw_text = text
        self.sender_id = user_id
        self.chat_id = chat_id
        self.id = msg_id
        self.is_group = is_group
        # مطابق SPlusthon: is_private ↔ PeerUser بودن چت (در PV، chat_id همان شناسه‌ی کاربر است)
        self.is_private = (not is_group) if is_private is None else is_private
        self.out = out
        self.sender = FakeSender(
            user_id,
            # None → نام پیش‌فرض؛ رشته‌ی خالی → کاربر بدون نام (برای تست fallback)
            first_name=(display_name if display_name is not None else "User"),
            username=username,
        )

    async def get_input_chat(self):
        if self.is_private:
            return types.InputPeerUser(user_id=int(self.chat_id), access_hash=0)
        return types.InputPeerChat(chat_id=abs(int(self.chat_id)))
