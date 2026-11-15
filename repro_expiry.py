"""بازتولید باگ: ربات در گروه با هیچ دستوری روشن نمی‌شود و همیشه می‌گوید «اعتبار ... تمام شده»."""
from __future__ import annotations

import asyncio
import datetime as _dt
import tempfile
from pathlib import Path

from ai_service import GroupAI
from config import Config
from core import BotCore
from sender import BrandSender
from storage import OwnerStore
from tests.fakes import FakeAI, FakeClient, FakeEvent, FakeReplyMessage

TEHRAN = _dt.timezone(_dt.timedelta(hours=3, minutes=30))
GROUP = -100111
OWNER = 1000
USER = 3001

NOW = _dt.datetime.now(TEHRAN)


def run(c):
    return asyncio.run(c)


async def main():
    tmp = tempfile.TemporaryDirectory()
    store = OwnerStore(Path(tmp.name) / "t.sqlite3")
    cfg = Config()
    client = FakeClient()
    sender = BrandSender(cfg)

    def now_provider(tz=None):
        return NOW.astimezone(tz) if tz else NOW

    ai = GroupAI(cfg, store, sender, FakeAI(reply="x"), now_provider=now_provider)
    core = BotCore(cfg, store, sender, ai=ai)

    store.claim(OWNER, chat_id=GROUP, message_id=1, username="me", display_name="مالک")
    store.set_global_suspended(False)

    async def send(text, user=OWNER):
        before = len(client.requests)
        await core.on_new_message(client, FakeEvent(text, user_id=user, chat_id=GROUP, is_group=True))
        return client.requests[-1].message if len(client.requests) > before else None

    # گروه ۴۵ روز پیش فعال شده و «30 day» گرفته بود ⇒ اعتبارش ۱۵ روز پیش تمام شده
    store.activate_group(GROUP, OWNER, group_name="گروه من")
    store.ai_set_enabled(GROUP, True)
    old = (NOW - _dt.timedelta(days=45)).isoformat(timespec="seconds")
    store._conn.execute("UPDATE group_activations SET activated_at = ? WHERE chat_id = ?", (old, GROUP))
    store._conn.execute("UPDATE group_registry SET activated_at = ? WHERE chat_id = ?", (old, GROUP))
    store._conn.commit()
    store.set_group_expiration(GROUP, NOW - _dt.timedelta(days=15))
    store.set_expiration(NOW - _dt.timedelta(days=15))

    print("وضعیت امروز:", NOW.isoformat(timespec="seconds"))
    print("activated_at گروه :", old)
    print("انقضای گروه/ربات :", store.get_group_expiration(GROUP))
    print("is_expired:", store.is_expired(NOW), "| is_group_expired:", store.is_group_expired(GROUP, NOW))

    print("\n--- چیزی که کاربر می‌بیند ---")
    for cmd in ("ai x cod", "ai online", "ai of"):
        msg = await send(cmd)
        print(f"«{cmd}» → {msg!r}")

    reply = FakeReplyMessage(sender_id=client.me_id, display_name="acod", msg_id=99, text="x", out=True)
    before = len(client.requests)
    await core.on_new_message(client, FakeEvent("سلام", user_id=USER, chat_id=GROUP,
                                               is_group=True, reply_to=reply))
    print("سؤال کاربر با ریپلای →", repr(client.requests[-1].message))

    print("\n--- مالک تلاش می‌کند با «30 day» تمدید کند ---")
    msg = await send("30 day")
    print("پاسخ:", (msg or "").replace("\n", " ")[:70], "...")
    exp = store.get_group_expiration(GROUP)
    print("expires_at جدید گروه =", exp)
    print("⇒ باز هم منقضی؟ is_group_expired =", store.is_group_expired(GROUP, NOW),
          "| is_expired =", store.is_expired(NOW))
    print("\n(۳۰ روز از «تاریخ فعال‌سازی ۴۵ روز پیش» حساب شد، نه از امروز)")

    print("\n--- تلاش دوباره بعد از تمدید ---")
    for cmd in ("ai x cod", "ai online"):
        msg = await send(cmd)
        print(f"«{cmd}» → {msg!r}")

    store.close()
    tmp.cleanup()


run(main())
