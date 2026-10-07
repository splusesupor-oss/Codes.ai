"""
ورودی اجرای ربات «ai cod» — روی حساب کاربری سروش پلاس، بدون Bot Token.

مسیر اتصال (کاملاً واقعی و مبتنی بر کد کتابخانه‌ی SPlusthon):
    ۱) ساخت کلاینت MTProto مخصوص سروش پلاس (SoroushClient)
    ۲) اتصال WebSocket به wss://im-server.splus.ir:443/apiws
    ۳) هندشیک MTProto با کلیدهای RSA سروش (داخل کتابخانه)
    ۴) لاگین با شماره تلفن + کد پیامک/داخل‌برنامه‌ای (+ رمز دو مرحله‌ای در صورت وجود)
    ۵) ذخیره‌ی سشن در فایل SQLite (‎*.session‎) تا اجرای بعدی، لاگین نخواهد

اجرا:  python bot.py
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import sys

from splusthon import SoroushClient, events

from config import Config, ensure_data_dir
from core import BotCore
from sender import BrandSender
from storage import OwnerStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("acod.bot")


def build_client(cfg: Config) -> SoroushClient:
    """ساخت کلاینت سروش پلاس.

    توجه: api_id/api_hash اینجا «توکن ربات» نیستند؛ مقادیر عمومی کلاینت وب سروش
    پلاس هستند که برای هندشیک MTProto لازم است و به‌صورت پیش‌فرض داخل خود
    SPlusthon هم وجود دارند (telegrambaseclient.py).
    """
    ensure_data_dir(cfg)         # پوشه‌ی سشن/دیتابیس باید قبل از ساخت کلاینت وجود داشته باشد
    client = SoroushClient(
        str(cfg.session_path),   # → فایل SQLiteSession ساخته/بازاستفاده می‌شود
        cfg.api_id,
        cfg.api_hash,
        app_version="3.9.2 A",   # همان نسخه‌ای که کتابخانه به‌عنوان پیش‌فرض استفاده می‌کند
        lang_code="fa",
        system_lang_code="fa",
        auto_reconnect=True,
    )
    return client


async def run(cfg: Config) -> None:
    ensure_data_dir(cfg)
    store = OwnerStore(cfg.db_path)
    core = BotCore(cfg, store, BrandSender(cfg))

    owner = store.get_owner()
    if owner:
        log.info("مالک سراسری فعلی (از حافظه دائمی): user_id=%s | %s | ثبت‌شده در %s",
                 owner.user_id, owner.display_name or "-", owner.claimed_at)
    else:
        log.info("هنوز مالک سراسری ثبت نشده است؛ اولین «%s» در هر گروه مالک را تعیین می‌کند.",
                 cfg.owner_command)

    client = build_client(cfg)

    phone = cfg.phone or (lambda: input("شماره حساب سروش پلاس (مثل 09123456789): ").strip())
    password = cfg.twofa_password or (
        lambda: getpass.getpass("رمز دو مرحله‌ای (اگر فعال نیست فقط Enter بزنید): ")
    )

    await client.start(
        phone=phone,
        password=password,
        code_callback=lambda: input("کد ورود ارسال‌شده را وارد کنید: ").strip(),
    )

    me = await client.get_me()
    log.info("اتصال برقرار شد: %s (id=%s)", getattr(me, "first_name", "?"), getattr(me, "id", "?"))

    async def handler(event):
        try:
            await core.on_new_message(client, event)
        except Exception:  # noqa: BLE001 — یک خطا نباید کل ربات را بخواباند
            log.exception("خطا در پردازش پیام")

    client.add_event_handler(handler, events.NewMessage(incoming=True))
    log.info("ربات فعال است. دستورات: «%s» برای مالک سراسری و «%s» برای معرفی ربات.",
             cfg.owner_command, cfg.kodrez_command)

    try:
        await client.run_until_disconnected()
    finally:
        store.close()
        log.info("اتصال بسته شد.")


def main() -> int:
    cfg = Config.from_env()
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        log.info("توقف دستی (Ctrl+C).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
