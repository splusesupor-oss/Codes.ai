"""
ابزار خط فرمان برای مدیریت حافظه‌ی مالک سراسری (بدون نیاز به اتصال به شبکه).

    python manage.py owner            # نمایش مالک فعلی
    python manage.py attempts         # تاریخچه‌ی همه‌ی «ai cod»ها
    python manage.py export           # خروجی JSON از مالک
    python manage.py reset-owner --yes  # حذف مالک (برای تست/شروع دوباره)
"""

from __future__ import annotations

import argparse
import sys

from config import Config, ensure_data_dir
from storage import OwnerStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="مدیریت حافظه‌ی مالک سراسری ربات ai cod")
    parser.add_argument(
        "action", choices=["owner", "attempts", "export", "reset-owner"]
    )
    parser.add_argument("--yes", action="store_true", help="تأیید برای reset-owner")
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    ensure_data_dir(cfg)
    store = OwnerStore(cfg.db_path)
    try:
        if args.action == "owner":
            owner = store.get_owner()
            if owner is None:
                print("هنوز مالکی ثبت نشده است.")
            else:
                print(f"user_id        : {owner.user_id}")
                print(f"نام            : {owner.display_name or '-'}")
                print(f"username       : {owner.username or '-'}")
                print(f"گروه ثبت       : {owner.claimed_chat_id}")
                print(f"پیام           : {owner.claimed_message_id}")
                print(f"زمان (UTC)     : {owner.claimed_at}")

        elif args.action == "attempts":
            rows = store.attempts()
            if not rows:
                print("تلاشی ثبت نشده است.")
            for r in rows:
                print(f"[{r['at']}] user={r['user_id']} chat={r['chat_id']} → {r['result']}")

        elif args.action == "export":
            print(store.export_json())

        elif args.action == "reset-owner":
            if not args.yes:
                print("برای حذف مالک باید --yes بدهید (این کار فقط برای تست است).")
                return 2
            owner = store.get_owner()
            if owner is None:
                print("مالکی وجود ندارد.")
                return 0
            store._conn.execute("DELETE FROM global_owner")   # noqa: SLF001 — ابزار داخلی
            store._conn.commit()                              # noqa: SLF001
            print(f"مالک قبلی (user_id={owner.user_id}) حذف شد.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
