#!/usr/bin/env bash
# اجرای ربات روی حساب کاربری سروش پلاس (بدون Bot Token)
set -e
cd "$(dirname "$0")"

if [ -f .env ]; then
  # بارگذاری متغیرهای محیطی از .env (اختیاری)
  set -a; . ./.env; set +a
fi

exec python bot.py
