#!/usr/bin/env bash
# اجرای همه‌ی تست‌های آفلاین پروژه (بدون نیاز به حساب واقعی)
set -e
cd "$(dirname "$0")"

echo "=== تست‌های ۳۸گانه ==="
python -m unittest discover -s tests -t . -v
