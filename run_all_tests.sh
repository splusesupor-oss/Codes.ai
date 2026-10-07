#!/usr/bin/env bash
# اجرای همه‌ی تست‌های آفلاین پروژه (بدون نیاز به حساب واقعی)
set -e
cd "$(dirname "$0")"

echo "=== اجرای همه‌ی تست‌های پروژه (رگرسیون کامل) ==="
python -m unittest discover -s tests -t . -v
