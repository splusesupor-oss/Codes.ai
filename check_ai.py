#!/usr/bin/env python3
"""
ابزار تشخیصی امن برای اتصال Cloudflare Workers AI.

این ابزار هیچ Secretی را چاپ نمی‌کند:
  * Account ID و API Token فقط به‌صورت «تنظیم‌شده/نشده»، طول و اثر انگشت (SHA-256) دیده می‌شوند.
  * endpoint با حساب پوشیده (`<ACCOUNT_ID>`) چاپ می‌شود.
  * هیچ هدر Authorization یا مقدار خامی چاپ نمی‌شود؛ هر متنی هم که چاپ شود،
    از فیلتر حذف مقادیر حساس عبور می‌کند.

اجرا:

    python3 check_ai.py               # بررسی تنظیمات + یک درخواست واقعی کوتاه
    python3 check_ai.py --no-call     # فقط بررسی تنظیمات و ساخت endpoint (بدون درخواست)
    python3 check_ai.py --max-tokens 1024
    python3 check_ai.py --json        # خروجی ماشین‌خوان (همان داده‌های غیرحساس)

کد خروج: 0 اگر تماس موفق بود، 1 در غیر این صورت (برای استفاده در اسکریپت).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_client import (  # noqa: E402
    AIConfigError,
    AIError,
    AIQuotaExceeded,
    CloudflareAI,
    hint_for,
)
from config import BASE_DIR, Config, load_env_file  # noqa: E402

LIVE_PROMPT = "در یک جمله کوتاه فارسی بگو: سلام."
ACCOUNT_ID_HEX = re.compile(r"^[0-9a-f]{32}$")


def _fingerprint(value: str) -> str:
    """اثر انگشت کوتاه (۸ رقم اول SHA-256) — برای تشخیص «همان مقدار» بدون افشای مقدار."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:8]


def _scrub(text: str, *secrets: str) -> str:
    out = str(text)
    for secret in secrets:
        if secret and len(secret) >= 6:
            out = out.replace(secret, "***")
    return out


def config_report(cfg: Config, env_path: Path | None = None) -> dict:
    """گزارش غیرحساس از تنظیمات فعلی (بدون هیچ مقدار Secret)."""
    account = (cfg.cloudflare_account_id or "").strip()
    token = (cfg.cloudflare_api_token or "").strip()
    return {
        "env_file": str(env_path) if env_path and env_path.is_file() else None,
        "env_file_found": bool(env_path and env_path.is_file()),
        "account_set": bool(account),
        "account_length": len(account),
        "account_looks_like_id": bool(ACCOUNT_ID_HEX.match(account.lower())),
        "account_fingerprint": _fingerprint(account) if account else None,
        "token_set": bool(token),
        "token_length": len(token),
        "token_fingerprint": _fingerprint(token) if token else None,
        "model": cfg.ai_model,
        "max_completion_tokens": cfg.ai_max_output_tokens,
        "timeout": cfg.ai_timeout,
        "daily_quota_per_group": cfg.ai_daily_quota,
    }


def endpoint_preview(cfg: Config) -> str:
    """endpoint نهایی با حساب پوشیده."""
    client = CloudflareAI(cfg.cloudflare_account_id, cfg.cloudflare_api_token, cfg.ai_model)
    return client.url_masked()


async def run_live_check(cfg: Config, *, max_tokens: int | None = None, session=None) -> dict:
    """یک درخواست واقعی و کوتاه؛ خروجی فقط شامل داده‌های غیرحساس است.

    ``session`` فقط برای تست‌های آفلاین تزریق می‌شود (aiohttp.ClientSession جعلی).
    """
    client = CloudflareAI(
        cfg.cloudflare_account_id,
        cfg.cloudflare_api_token,
        cfg.ai_model,
        timeout=cfg.ai_timeout,
        max_output_tokens=max_tokens or cfg.ai_max_output_tokens,
        session=session,
    )
    result = {
        "call": True,
        "ok": False,
        "status": None,
        "codes": [],
        "messages": [],
        "hint": "",
        "elapsed": None,
        "finish_reason": None,
        "usage": None,
        "answer": None,
        "error_type": None,
        "error": None,
    }
    try:
        response = await client.chat([{"role": "user", "content": LIVE_PROMPT}])
        info = client.last_status
        result.update(
            ok=True,
            status=info.status if info else 200,
            codes=(info.codes if info else []) or [],
            messages=(info.messages if info else []) or [],
            elapsed=(info.elapsed if info else None),
            finish_reason=response.finish_reason,
            usage=response.usage,
            answer=response.text,
        )
    except (AIError, AIConfigError, AIQuotaExceeded) as exc:
        info = client.last_status
        result.update(
            ok=False,
            status=info.status if info else None,
            codes=(info.codes if info else []) or [],
            messages=(info.messages if info else []) or [],
            elapsed=(info.elapsed if info else None),
            error_type=type(exc).__name__,
            error=_scrub(str(exc), client.api_token, client.account_id),
            hint=hint_for((info.codes if info else []), info.status if info else None,
                          " ".join((info.messages if info else []) or [])),
        )
    finally:
        await client.close()

    for key in ("messages", "error"):
        value = result.get(key)
        if isinstance(value, list):
            result[key] = [_scrub(v, cfg.cloudflare_api_token, cfg.cloudflare_account_id)
                           for v in value]
        elif value:
            result[key] = _scrub(value, cfg.cloudflare_api_token, cfg.cloudflare_account_id)
    return result


def print_report(cfg_report: dict, cfg: Config, live: dict | None, env_path: Path) -> None:
    ok = "✅"
    bad = "❌"
    print("== ۱) تنظیمات ==")
    print(f"فایل .env               : "
          f"{'پیدا شد (' + str(env_path) + ')' if cfg_report['env_file_found'] else 'پیدا نشد'}")
    print(f"CLOUDFLARE_ACCOUNT_ID   : "
          f"{'تنظیم‌شده ' + ok if cfg_report['account_set'] else 'تنظیم‌نشده ' + bad} | "
          f"طول={cfg_report['account_length']} | "
          f"قالب 32-کاراکتر hex={'✅' if cfg_report['account_looks_like_id'] else '❌'} | "
          f"اثرانگشت={cfg_report['account_fingerprint'] or '—'}")
    print(f"CLOUDFLARE_API_TOKEN    : "
          f"{'تنظیم‌شده ' + ok if cfg_report['token_set'] else 'تنظیم‌نشده ' + bad} | "
          f"طول={cfg_report['token_length']} | "
          f"اثرانگشت={cfg_report['token_fingerprint'] or '—'}")
    print(f"مدل                     : {cfg_report['model']}")
    print(f"endpoint                : {endpoint_preview(cfg)}")
    print(f"payload                 : "
          f'{{"messages": [{{"role": "user", "content": "…"}}], '
          f'"max_completion_tokens": {cfg_report["max_completion_tokens"]}}}')
    print(f"سهمیه‌ی روزانه هر گروه  : {cfg_report['daily_quota_per_group']} درخواست")

    if live is None:
        print("\n(بدون تماس واقعی — حالت --no-call)")
        return

    print("\n== ۲) درخواست واقعی ==")
    print(f"HTTP status : {live['status'] if live['status'] is not None else '—'}")
    print(f"CF codes    : {live['codes'] or '—'}")
    print(f"CF message  : {(live['messages'][0][:300] if live['messages'] else '—')}")
    if live["elapsed"] is not None:
        print(f"زمان        : {live['elapsed']:.2f} ثانیه")
    if live["ok"]:
        print(f"finish      : {live['finish_reason'] or '—'}")
        usage = live["usage"] or {}
        print(f"usage       : completion={usage.get('completion_tokens', '—')} | "
              f"prompt={usage.get('prompt_tokens', '—')} | "
              f"reasoning={(usage.get('completion_tokens_details') or {}).get('reasoning_tokens', '—')}")
        print(f"پاسخ مدل    : {live['answer']}")
        print(f"\nنتیجه       : {ok} موفق — اتصال Cloudflare Workers AI درست کار می‌کند.")
    else:
        print(f"نوع خطا     : {live['error_type']}")
        print(f"پیام خطا    : {live['error']}")
        if live["hint"]:
            print(f"راهنما       : {live['hint']}")
        print(f"\nنتیجه       : {bad} ناموفق — علت و راهنمای بالا را ببینید.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="بررسی امن اتصال Cloudflare Workers AI")
    parser.add_argument("--no-call", action="store_true", help="فقط بررسی تنظیمات، بدون درخواست")
    parser.add_argument("--max-tokens", type=int, default=None, help="سقف توکن خروجی این تست")
    parser.add_argument("--json", action="store_true", help="خروجی JSON (غیرحساس)")
    parser.add_argument("--env", default=None, help="مسیر فایل .env (پیش‌فرض: کنار پروژه)")
    args = parser.parse_args(argv)

    env_path = Path(args.env) if args.env else BASE_DIR / ".env"
    if args.env:
        # کاربر صراحتاً مسیر دیگری داده — فقط از همان فایل باید اعتبارها خوانده شود.
        # ابتدا هر مقدار CLOUDFLARE_* که به‌خاطر side-effect ایمپورتِ config (از .env
        # پیش‌فرض) یا متغیرهای پوسته در os.environ نشسته را پاک می‌کنیم تا نشتی به
        # Config نرسد؛ سپس با override=True فقط از مسیر داده‌شده می‌خوانیم. اگر مسیر
        # وجود نداشته باشد، load_env_file هیچ متغیری ست نمی‌کند و نتیجه «تنظیم‌نشده»
        # خواهد بود (و شاخه‌ی بدون‌شبکه فعال می‌شود).
        for _var in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
            os.environ.pop(_var, None)
        load_env_file(env_path, override=True)
    else:
        # مسیر پیش‌فرض — همان رفتار قبلی: .env کنار پروژه + متغیرهای پوسته.
        load_env_file(env_path)
    cfg = Config.from_env()

    report = config_report(cfg, env_path)
    live = None
    if not args.no_call:
        if not (cfg.cloudflare_account_id and cfg.cloudflare_api_token):
            live = {
                "call": True, "ok": False, "status": None, "codes": [], "messages": [],
                "hint": "CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN در .env پر نشده‌اند.",
                "elapsed": None, "finish_reason": None, "usage": None, "answer": None,
                "error_type": "AIConfigError",
                "error": "تنظیمات ناقص است؛ تماس واقعی انجام نشد.",
            }
        else:
            live = asyncio.run(run_live_check(cfg, max_tokens=args.max_tokens))

    if args.json:
        print(json.dumps({"config": report, "live": live}, ensure_ascii=False, indent=2))
    else:
        print_report(report, cfg, live, env_path)

    return 0 if (live is None or live.get("ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
