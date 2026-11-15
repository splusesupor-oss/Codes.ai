#!/usr/bin/env python3
"""
ابزار تعمیر و بازرسی «اعتبار/انقضای» ربات — برای وضعیتی که ربات می‌گوید
«اعتبار استفاده از ربات به پایان رسیده است» و در گروه روشن نمی‌شود.

نمونه‌ها:

    # ۱) فقط وضعیت را نشان بده (بدون هیچ تغییری)
    python fix_expiry.py

    # ۲) انقضای ربات و همه‌ی گروه‌های منقضی را ۳۰ روز از «همین حالا» تمدید کن
    python fix_expiry.py --extend 30 --all-groups

    # ۳) انقضای یک گروه خاص را تمدید کن
    python fix_expiry.py --extend 30 --group -1001234567890

    # ۴) محدودیت زمانی را کامل بردار (ربات دیگر خودکار خاموش نمی‌شود)
    python fix_expiry.py --clear --all-groups

    # مسیر دیتابیس: پیش‌فرض همان ACOD_DB یا data/owner.sqlite3 است
    python fix_expiry.py --db /path/to/owner.sqlite3 ...

قبل از هر تغییر، یک نسخه‌ی پشتیبان کنار همان فایل ساخته می‌شود:
    owner.sqlite3.bak-20261115-235959
"""

from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
from pathlib import Path

try:
    import zoneinfo

    TEHRAN_TZ = zoneinfo.ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30), name="Asia/Tehran")

from storage import OwnerStore  # noqa: E402  (نیازمند اجرا از ریشه‌ی پروژه)


def _aware(dt):
    if dt is None:
        return None
    return dt.replace(tzinfo=TEHRAN_TZ) if dt.tzinfo is None else dt.astimezone(TEHRAN_TZ)


def _jalali(dt) -> str:
    try:
        import brand

        return brand.parse_and_format_jalali(dt)
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M")


def _line(title: str, value: str) -> None:
    print(f"  {title:<26} {value}")


def show_status(store: OwnerStore, now: _dt.datetime) -> None:
    print("\n=== وضعیت فعلی ===")
    owner = store.get_owner()
    _line("مالک سراسری:", f"{owner.user_id} ({owner.display_name})" if owner else "ثبت نشده")
    reg = store.get_registered_bot_owner()
    _line("مالک ثبت‌شده:", f"{reg.user_id}" if reg else "—")
    _line("ربات معلق (ai cod):", "بله" if store.is_global_suspended() else "خیر")

    exp = _aware(store.get_expiration())
    expired = store.is_expired(now)
    _line("انقضای سراسری ربات:",
          f"{_jalali(exp)}  [{exp.isoformat(timespec='seconds')}]" if exp else "تنظیم نشده (نامحدود)")
    _line("ربات منقضی است؟", "بله ❌" if expired else "خیر ✅")

    print("\n--- گروه‌ها ---")
    rows = store._conn.execute(
        "SELECT chat_id, group_name, is_active, expires_at FROM group_registry ORDER BY chat_id"
    ).fetchall()
    if not rows:
        print("  هیچ گروهی در دیتابیس نیست.")
        return
    for row in rows:
        chat_id = row["chat_id"]
        g_exp = _aware(store.get_group_expiration(chat_id))
        if g_exp is None:
            state = "نامحدود ✅"
            when = "—"
        else:
            is_exp = store.is_group_expired(chat_id, now)
            state = "منقضی ❌" if is_exp else "فعال ✅"
            when = _jalali(g_exp)
        print(f"  {chat_id:<18} {str(row['group_name'] or '—'):<24} {state:<10} {when}")


def main() -> int:
    cfg_db = None
    try:
        from config import Config

        cfg_db = Config().db_path
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="بازرسی/تعمیر انقضای ربات acod")
    ap.add_argument("--db", default=str(cfg_db or Path("data/owner.sqlite3")),
                    help="مسیر فایل SQLite (پیش‌فرض: ACOD_DB یا data/owner.sqlite3)")
    ap.add_argument("--show", action="store_true", help="فقط نمایش وضعیت (پیش‌فرض)")
    ap.add_argument("--extend", type=int, metavar="DAYS",
                    help="تمدید انقضا به تعداد روز، از همین حالا")
    ap.add_argument("--clear", action="store_true", help="حذف کامل محدودیت زمانی")
    ap.add_argument("--group", type=int, action="append", default=None,
                    help="شناسه‌ی گروه (قابل تکرار)؛ بدون آن فقط انقضای سراسری تغییر می‌کند")
    ap.add_argument("--all-groups", action="store_true",
                    help="اعمال تغییر روی همه‌ی گروه‌های ثبت‌شده")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ فایل دیتابیس پیدا نشد: {db_path}\n"
              f"   مسیر را با --db مشخص کنید (متغیر محیطی ACOD_DB هم پذیرفته می‌شود).",
              file=sys.stderr)
        return 2

    store = OwnerStore(db_path)
    now = _dt.datetime.now(TEHRAN_TZ)
    print(f"دیتابیس: {db_path.resolve()}")
    print(f"اکنون (تهران): {_jalali(now)}")
    show_status(store, now)

    if not args.extend and not args.clear:
        print("\n(حالت فقط-نمایش؛ برای تغییر از --extend N یا --clear استفاده کنید)")
        return 0

    backup = db_path.with_name(db_path.name + ".bak-" + now.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(db_path, backup)
    print(f"\n💾 نسخه‌ی پشتیبان ساخته شد: {backup.name}")

    target_groups: list[int] = []
    if args.all_groups:
        target_groups = [r["chat_id"] for r in store._conn.execute(
            "SELECT chat_id FROM group_registry").fetchall()]
    elif args.group:
        target_groups = list(args.group)

    if args.clear:
        store.clear_expiration()
        for chat_id in target_groups:
            store.clear_group_expiration(chat_id)
        print("✅ انقضای سراسری ربات حذف شد" +
              (f" و {len(target_groups)} گروه." if target_groups else "."))
    else:
        new_exp = now + _dt.timedelta(days=args.extend)
        store.set_expiration(new_exp)
        print(f"✅ انقضای سراسری ربات → {_jalali(new_exp)} ({args.extend} روز)")
        for chat_id in target_groups:
            current = _aware(store.get_group_expiration(chat_id))
            base = current if (current and current > now) else now
            g_exp = base + _dt.timedelta(days=args.extend)
            store.set_group_expiration(chat_id, g_exp)
            print(f"✅ گروه {chat_id} → {_jalali(g_exp)}")

    if not args.all_groups and not args.group:
        print("ℹ️  یادآوری: انقضای گروه‌ها دست‌نخورده ماند. اگر ربات داخل یک گروه خاص "
              "قفل است، با --all-groups یا --group <chat_id> اجرا کنید.")

    print("\n=== وضعیت بعد از تغییر ===")
    show_status(store, now)
    store.close()
    print("\nربات را یک‌بار ری‌استارت کنید (یا از دستور «ai x cod» در گروه استفاده کنید).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
