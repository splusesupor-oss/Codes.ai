#!/usr/bin/env python3
"""
ابزار تشخیصی امن برای اتصال Cloudflare Workers AI (با پشتیبانی کامل از چند حساب).

این ابزار همه‌ی حساب‌های پیکربندی‌شده در `.env` را یک‌به‌یک بررسی و نمایش می‌دهد و
هیچ Secretی را چاپ نمی‌کند:
  * برای هر حساب: شماره، `masked_id` (`0123...cdef`)، `status`، `request_count`، کد خطا،
    `HTTP status` و زمان پاسخ (`elapsed`) گزارش می‌شود.
  * API Token هرگز (حتی بخشی از کاراکترهایش) چاپ نمی‌شود؛ فقط طول و اثر انگشت کوتاه SHA-256 درج می‌شود.
  * endpoint با حساب پوشیده (`<ACCOUNT_ID>`) چاپ می‌شود.

اجرا:

    python3 check_ai.py               # بررسی تنظیمات + تست واقعی همه‌ی حساب‌ها
    python3 check_ai.py --no-call     # فقط بررسی تنظیمات و وضعیت همه‌ی حساب‌ها (بدون درخواست شبکه)
    python3 check_ai.py --max-tokens 1024
    python3 check_ai.py --json        # خروجی ماشین‌خوان (همان داده‌های غیرحساس)
    python3 check_ai.py --env path/to/.env

کد خروج: 0 اگر حداقل یک حساب سالم و فعال باشد (یا در حالت `--no-call`)، 1 در غیر این صورت.
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
from config import (  # noqa: E402
    BASE_DIR,
    CloudflareAccountCreds,
    Config,
    load_env_file,
    mask_account_id,
)
from storage import CfAccountRecord, OwnerStore  # noqa: E402

LIVE_PROMPT = "در یک جمله کوتاه فارسی بگو: سلام."
ACCOUNT_ID_HEX = re.compile(r"^[0-9a-f]{32}$")


def _fingerprint(value: str) -> str:
    """اثر انگشت کوتاه (۸ رقم اول SHA-256) — برای تشخیص «همان مقدار» بدون افشای مقدار."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:8]


def _scrub(text: str, *secrets: str) -> str:
    out = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, "***")
    return out


def _read_db_accounts(cfg: Config) -> dict[str, CfAccountRecord]:
    """خواندن آمار ذخیره‌شده‌ی حساب‌ها از SQLite (فقط در صورت وجود فایل دیتابیس)."""
    db_path = getattr(cfg, "db_path", None)
    if not db_path or not Path(db_path).is_file():
        return {}
    store = OwnerStore(db_path)
    try:
        return {row.account_id: row for row in store.list_cf_accounts()}
    except Exception:
        return {}
    finally:
        store.close()


def _collect_accounts(cfg: Config) -> list[CloudflareAccountCreds]:
    """استخراج همه‌ی حساب‌های قابل‌بررسی از `Config`."""
    accounts = list(getattr(cfg, "cloudflare_accounts", ()) or ())
    if accounts:
        return [
            item
            if isinstance(item, CloudflareAccountCreds)
            else CloudflareAccountCreds(account_id=item[0], api_token=item[1])
            for item in accounts
        ]
    acc = (cfg.cloudflare_account_id or "").strip()
    tok = (cfg.cloudflare_api_token or "").strip()
    if acc and tok:
        return [CloudflareAccountCreds(account_id=acc, api_token=tok)]
    return []


def config_report(cfg: Config, env_path: Path | None = None) -> dict:
    """گزارش غیرحساس از همه‌ی حساب‌ها شامل `masked_id`، `status` و `request_count`."""
    accounts = _collect_accounts(cfg)
    db_map = _read_db_accounts(cfg)
    primary_account = accounts[0].account_id if accounts else (cfg.cloudflare_account_id or "").strip()
    primary_token = accounts[0].api_token if accounts else (cfg.cloudflare_api_token or "").strip()

    accounts_meta = []
    for idx, item in enumerate(accounts, start=1):
        masked = mask_account_id(item.account_id)
        db_row = db_map.get(item.account_id)
        accounts_meta.append(
            {
                "index": idx,
                "masked_id": masked,
                "account_masked": masked,
                "status": db_row.status if db_row else "active",
                "request_count": int(db_row.request_count) if db_row else 0,
                "error_count": int(db_row.error_count) if db_row else 0,
                "last_used_at": db_row.last_used_at if db_row else None,
                "account_length": len(item.account_id),
                "account_looks_like_id": bool(ACCOUNT_ID_HEX.match(item.account_id.lower())),
                "account_fingerprint": _fingerprint(item.account_id),
                "token_set": bool(item.api_token),
                "token_length": len(item.api_token),
                "token_fingerprint": _fingerprint(item.api_token),
            }
        )

    return {
        "env_file": str(env_path) if env_path and env_path.is_file() else None,
        "env_file_found": bool(env_path and env_path.is_file()),
        "accounts_count": len(accounts_meta),
        "accounts": accounts_meta,
        "account_set": bool(primary_account),
        "account_length": len(primary_account),
        "account_looks_like_id": bool(ACCOUNT_ID_HEX.match(primary_account.lower())),
        "account_fingerprint": _fingerprint(primary_account) if primary_account else None,
        "token_set": bool(primary_token),
        "token_length": len(primary_token),
        "token_fingerprint": _fingerprint(primary_token) if primary_token else None,
        "model": cfg.ai_model,
        "max_completion_tokens": cfg.ai_max_output_tokens,
        "timeout": cfg.ai_timeout,
        "daily_quota_per_group": cfg.ai_daily_quota,
    }


def endpoint_preview(cfg: Config) -> str:
    """endpoint نهایی با حساب پوشیده."""
    client = CloudflareAI(cfg.cloudflare_account_id, cfg.cloudflare_api_token, cfg.ai_model)
    return client.url_masked()


async def _check_single_account(
    idx: int,
    cred: CloudflareAccountCreds,
    cfg: Config,
    all_secrets: tuple[str, ...],
    db_row: CfAccountRecord | None = None,
    *,
    max_tokens: int | None = None,
    session=None,
) -> dict:
    """تست واقعی یک حساب منفرد و ثبت `masked_id`، `status`، `request_count` و `HTTP status`."""
    client = CloudflareAI(
        cred.account_id,
        cred.api_token,
        cfg.ai_model,
        timeout=cfg.ai_timeout,
        max_output_tokens=max_tokens or cfg.ai_max_output_tokens,
        session=session,
    )
    masked = mask_account_id(cred.account_id)
    base_reqs = int(db_row.request_count) if db_row else 0
    base_errs = int(db_row.error_count) if db_row else 0

    entry = {
        "index": idx,
        "masked_id": masked,
        "account_masked": masked,
        "account_fingerprint": _fingerprint(cred.account_id),
        "token_fingerprint": _fingerprint(cred.api_token),
        "ok": False,
        "status": "error",
        "account_status": "error",
        "http_status": None,
        "request_count": base_reqs,
        "error_count": base_errs,
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
        entry.update(
            ok=True,
            status="active",
            account_status="active",
            http_status=info.status if info else 200,
            request_count=base_reqs + client.pool.accounts[0].request_count,
            error_count=base_errs + client.pool.accounts[0].error_count,
            codes=(info.codes if info else []) or [],
            messages=[_scrub(m, *all_secrets) for m in ((info.messages if info else []) or [])],
            elapsed=(info.elapsed if info else 0.0),
            finish_reason=response.finish_reason,
            usage=response.usage,
            answer=_scrub(response.text, *all_secrets),
        )
    except AIQuotaExceeded as exc:
        info = client.last_status
        msgs = [_scrub(m, *all_secrets) for m in ((info.messages if info else []) or [])]
        entry.update(
            ok=False,
            status="exhausted_today",
            account_status="exhausted_today",
            http_status=info.status if info else 429,
            request_count=base_reqs + client.pool.accounts[0].request_count,
            error_count=base_errs + client.pool.accounts[0].error_count,
            codes=(info.codes if info else []) or [],
            messages=msgs,
            elapsed=(info.elapsed if info else None),
            error_type=type(exc).__name__,
            error=_scrub(str(exc), *all_secrets),
            hint=hint_for((info.codes if info else []), info.status if info else 429, " ".join(msgs)),
        )
    except (AIError, AIConfigError) as exc:
        info = client.last_status
        msgs = [_scrub(m, *all_secrets) for m in ((info.messages if info else []) or [])]
        entry.update(
            ok=False,
            status="error",
            account_status="error",
            http_status=info.status if info else None,
            request_count=base_reqs + (client.pool.accounts[0].request_count if client.pool.accounts else 0),
            error_count=base_errs + (client.pool.accounts[0].error_count if client.pool.accounts else 1),
            codes=(info.codes if info else []) or [],
            messages=msgs,
            elapsed=(info.elapsed if info else None),
            error_type=type(exc).__name__,
            error=_scrub(str(exc), *all_secrets),
            hint=hint_for((info.codes if info else []), info.status if info else None, " ".join(msgs)),
        )
    finally:
        await client.close()

    return entry


async def run_live_check(cfg: Config, *, max_tokens: int | None = None, session=None) -> dict:
    """تست واقعی تک‌تک حساب‌های Cloudflare؛ خروجی فقط شامل داده‌های غیرحساس است."""
    accounts = _collect_accounts(cfg)
    db_map = _read_db_accounts(cfg)
    all_secrets_list = [cfg.cloudflare_api_token or "", cfg.cloudflare_account_id or ""]
    for a in accounts:
        all_secrets_list.extend([a.api_token, a.account_id])
    all_secrets = tuple(s for s in all_secrets_list if s)

    if not accounts:
        return {
            "call": True,
            "ok": False,
            "total_accounts": 0,
            "active_accounts": 0,
            "exhausted_accounts": 0,
            "accounts": [],
            "status": None,
            "codes": [],
            "messages": [],
            "hint": "هیچ حساب Cloudflare در .env تنظیم نشده است.",
            "elapsed": None,
            "finish_reason": None,
            "usage": None,
            "answer": None,
            "error_type": "AIConfigError",
            "error": "تنظیمات ناقص است؛ تماس واقعی انجام نشد.",
        }

    per_account: list[dict] = []
    for idx, cred in enumerate(accounts, start=1):
        res = await _check_single_account(
            idx,
            cred,
            cfg,
            all_secrets,
            db_row=db_map.get(cred.account_id),
            max_tokens=max_tokens,
            session=session,
        )
        per_account.append(res)

    first_ok = next((r for r in per_account if r["ok"]), None)
    representative = first_ok or per_account[0]

    return {
        "call": True,
        "ok": any(r["ok"] for r in per_account),
        "total_accounts": len(per_account),
        "active_accounts": sum(1 for r in per_account if r["status"] == "active"),
        "exhausted_accounts": sum(1 for r in per_account if r["status"] == "exhausted_today"),
        "accounts": per_account,
        "status": representative["http_status"],
        "codes": representative["codes"],
        "messages": representative["messages"],
        "hint": representative["hint"],
        "elapsed": representative["elapsed"],
        "finish_reason": representative["finish_reason"],
        "usage": representative["usage"],
        "answer": representative["answer"],
        "error_type": representative["error_type"],
        "error": representative["error"],
    }


def print_report(cfg_report: dict, cfg: Config, live: dict | None, env_path: Path) -> None:
    ok = "✅"
    bad = "❌"
    print("== ۱) تنظیمات (همه‌ی حساب‌ها) ==")
    print(f"فایل .env               : "
          f"{'پیدا شد (' + str(env_path) + ')' if cfg_report['env_file_found'] else 'پیدا نشد'}")
    print(f"تعداد حساب‌های CF       : {cfg_report.get('accounts_count', 0)}")
    print(f"CLOUDFLARE_ACCOUNT_ID   : "
          f"{'تنظیم‌شده ' + ok if cfg_report['account_set'] else 'تنظیم‌نشده ' + bad} | "
          f"طول={cfg_report['account_length']} | "
          f"قالب 32-کاراکتر hex={'✅' if cfg_report['account_looks_like_id'] else '❌'} | "
          f"اثرانگشت={cfg_report['account_fingerprint'] or '—'}")
    print(f"CLOUDFLARE_API_TOKEN    : "
          f"{'تنظیم‌شده ' + ok if cfg_report['token_set'] else 'تنظیم‌نشده ' + bad} | "
          f"طول={cfg_report['token_length']} | "
          f"اثرانگشت={cfg_report['token_fingerprint'] or '—'}")

    for acc in cfg_report.get("accounts") or []:
        print(
            f"  • حساب #{acc['index']}            : masked_id={acc['masked_id']} | "
            f"status={acc['status']} | request_count={acc['request_count']} | "
            f"طول توکن={acc['token_length']} | SHA-256 توکن={acc['token_fingerprint']}"
        )

    print(f"مدل                     : {cfg_report['model']}")
    print(f"endpoint                : {endpoint_preview(cfg)}")
    print(f"payload                 : "
          f'{{"messages": [{{"role": "user", "content": "…"}}], '
          f'"max_completion_tokens": {cfg_report["max_completion_tokens"]}}}')
    print(f"سهمیه‌ی روزانه هر گروه  : {cfg_report['daily_quota_per_group']} درخواست")

    if live is None:
        print("\n(بدون تماس واقعی — حالت --no-call)")
        return

    print("\n== ۲) درخواست واقعی (تست همه‌ی حساب‌ها) ==")
    per_accounts = live.get("accounts") or []
    if not per_accounts:
        print(f"HTTP status : {live['status'] if live['status'] is not None else '—'}")
        print(f"CF codes    : {live['codes'] or '—'}")
        print(f"نوع خطا     : {live['error_type']}")
        print(f"پیام خطا    : {live['error']}")
        if live["hint"]:
            print(f"راهنما       : {live['hint']}")
        print(f"\nنتیجه       : {bad} ناموفق — علت و راهنمای بالا را ببینید.")
        return

    for item in per_accounts:
        print(
            f"\n--- حساب #{item['index']} | masked_id={item['masked_id']} | "
            f"status={item['status']} | request_count={item['request_count']} ---"
        )
        print(f"HTTP status : {item['http_status'] if item['http_status'] is not None else '—'}")
        print(f"CF codes    : {item['codes'] or '—'}")
        print(f"CF message  : {(item['messages'][0][:300] if item['messages'] else '—')}")
        if item["elapsed"] is not None:
            print(f"زمان        : {item['elapsed']:.2f} ثانیه")
        if item["ok"]:
            print(f"finish      : {item['finish_reason'] or '—'}")
            usage = item["usage"] or {}
            print(f"usage       : completion={usage.get('completion_tokens', '—')} | "
                  f"prompt={usage.get('prompt_tokens', '—')} | "
                  f"reasoning={(usage.get('completion_tokens_details') or {}).get('reasoning_tokens', '—')}")
            print(f"پاسخ مدل    : {item['answer']}")
        else:
            print(f"نوع خطا     : {item['error_type']}")
            print(f"پیام خطا    : {item['error']}")
            if item["hint"]:
                print(f"راهنما       : {item['hint']}")

    active_cnt = live.get("active_accounts", 0)
    total_cnt = live.get("total_accounts", len(per_accounts))
    if live["ok"]:
        print(
            f"\nنتیجه       : {ok} موفق — {active_cnt} از {total_cnt} حساب Cloudflare فعال و آماده پاسخ‌گویی است."
        )
    else:
        print(
            f"\nنتیجه       : {bad} ناموفق — هیچ‌کدام از {total_cnt} حساب قادر به پاسخ‌گویی نبود."
        )


def _clear_cf_env_vars() -> None:
    """پاکسازی همه‌ی متغیرهای محیطی مرتبط با حساب‌های Cloudflare هنگام استفاده از `--env` سفارشی."""
    to_remove = [
        k for k in list(os.environ.keys())
        if k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNTS_JSON")
        or k.startswith("CF_ACCOUNT_")
    ]
    for k in to_remove:
        os.environ.pop(k, None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="بررسی امن اتصال حساب‌های Cloudflare Workers AI")
    parser.add_argument("--no-call", action="store_true", help="فقط بررسی تنظیمات، بدون درخواست")
    parser.add_argument("--max-tokens", type=int, default=None, help="سقف توکن خروجی این تست")
    parser.add_argument("--json", action="store_true", help="خروجی JSON (غیرحساس)")
    parser.add_argument("--env", default=None, help="مسیر فایل .env (پیش‌فرض: کنار پروژه)")
    args = parser.parse_args(argv)

    env_path = Path(args.env) if args.env else BASE_DIR / ".env"
    if args.env:
        _clear_cf_env_vars()
        load_env_file(env_path, override=True)
    else:
        load_env_file(env_path)
    cfg = Config.from_env()

    report = config_report(cfg, env_path)
    live = None
    if not args.no_call:
        if not _collect_accounts(cfg):
            live = {
                "call": True,
                "ok": False,
                "total_accounts": 0,
                "active_accounts": 0,
                "exhausted_accounts": 0,
                "accounts": [],
                "status": None,
                "codes": [],
                "messages": [],
                "hint": "هیچ حساب Cloudflare در .env پر نشده است.",
                "elapsed": None,
                "finish_reason": None,
                "usage": None,
                "answer": None,
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
