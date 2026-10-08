"""
پچ امن و محدود برای باگ keepalive/reconnect در SPlusthon 1.1.4.

علت باگ (مستند در WS_RECONNECT_REPORT.md):
    MTProtoSender._reconnect() در پایان کار self._ping را None نمی‌کند.
    در نتیجه keepalive بعدی به‌جای ارسال پینگ تازه، بلافاصله _start_reconnect
    را دوباره صدا می‌زند → طوفان reconnect و انباشت pending PingRequest.

این پچ:
  * فقط روی SPlusthon 1.1.4 فعال می‌شود (اگر نسخهٔ نصب‌شده متفاوت بود no-op).
  * امضای تابع و رفتار اصلی را حفظ می‌کند (wrapper به‌صورت await اوریجینال
    را صدا می‌زند و فقط self._ping = None را بعد از آن تضمین می‌کند).
  * قبل از اعمال، sanity-check روی ساختار متد انجام می‌دهد تا اگر نسخهٔ
    آینده متد را عوض کرده باشد، به‌جای شکستِ سایلنت، لاگ هشدار می‌دهد و
    از اعمال patch خودداری می‌کند.
  * به هیچ فایلی در site-packages دست نمی‌زند.
  * تکراری apply_patch() هیچ مشکلی ایجاد نمی‌کند (idempotent).
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger("acod.splusthon_patch")

_PATCH_MARKER = "_acod_ping_reset_patch_applied"
_TARGET_VERSION = "1.1.4"


def _expected_source_sanity(source: str) -> bool:
    """بررسی سریع اینکه آیا ساختار _reconnect همان چیزی است که در ۱.۱.۴ دیدیم."""
    # نشانه‌های کلیدی که انتظار داریم در متد موجود باشند؛ در صورت تغییر اساسی
    # در نسخهٔ دیگر، patch را اعمال نمی‌کنیم.
    markers = (
        "async def _reconnect(",
        "self._state.reset()",
        "self._reconnecting = False",
        "self._disconnect(error=error)",
    )
    return all(m in source for m in markers)


def apply_patch() -> bool:
    """اعمال پچ. True یعنی patch اعمال شد / قبلاً اعمال شده بود؛ False یعنی صرف‌نظر شد."""
    try:
        import splusthon
        from splusthon.network import mtprotosender as _mps
    except Exception as e:  # pragma: no cover - import نباید شکست بخورد
        log.warning("نمی‌توان splusthon را وارد کرد؛ patch اعمال نشد (%s)", e)
        return False

    version = getattr(splusthon, "__version__", None)
    if version != _TARGET_VERSION:
        log.info(
            "پچ keepalive/reconnect فقط برای SPlusthon %s طراحی شده؛ "
            "نسخهٔ نصب‌شده %s است → بدون تغییر ادامه می‌دهیم.",
            _TARGET_VERSION, version,
        )
        return False

    cls = _mps.MTProtoSender
    original = cls._reconnect

    # قبلاً اعمال شده؟
    if getattr(original, _PATCH_MARKER, False):
        return True

    # Sanity-check ساختار متد (با خواندن سورش)
    try:
        src = inspect.getsource(original)
    except (OSError, TypeError) as e:
        log.warning("نمی‌توان سورس _reconnect را خواند (%s)؛ patch اعمال نشد.", e)
        return False

    if not _expected_source_sanity(src):
        log.warning(
            "ساختار MTProtoSender._reconnect با انتظارات patch مطابقت ندارد "
            "(احتمالاً نسخهٔ ۱.۱.۴ تغییری کرده یا نسخهٔ جدیدتری نصب است)؛ "
            "patch اعمال نشد تا جلو شکست اتفاقی گرفته شود."
        )
        return False

    # بررسی وجود فیلد _ping (در ۱.۱.۴ در __init__ تنظیم می‌شود)
    if "_ping" not in cls._keepalive_ping.__code__.co_names:  # pragma: no cover - ساده‌ترین sanity
        log.warning("فیلد self._ping در _keepalive_ping پیدا نشد؛ patch اعمال نشد.")
        return False

    async def patched_reconnect(self, last_error):
        # قبل از هر چیز مقدار _ping را ریست می‌کنیم تا keepalive بعدی تصور نکند
        # پینگ قبلی هنوز باز است و بی‌دلیل reconnect راه نیندازد.
        # این خط باید پیش از هر await بیاید تا در مقابل re-entryهای ناشی از
        # _keepalive_loop هم که ممکن است در لحظه صدا زده شود، ایمن باشد.
        self._ping = None
        try:
            return await original(self, last_error)
        finally:
            # تضمین مضاعف: چه reconnect موفق باشد چه تمام retry ها شکست بخورد
            # و چه در میانهٔ کار استثنایی بالا بیاید، _ping باید ریست شده باشد.
            self._ping = None

    patched_reconnect.__name__ = original.__name__
    patched_reconnect.__qualname__ = original.__qualname__
    patched_reconnect.__doc__ = original.__doc__
    setattr(patched_reconnect, _PATCH_MARKER, True)

    cls._reconnect = patched_reconnect
    log.info(
        "پچ ریست self._ping در MTProtoSender._reconnect اعمال شد (SPlusthon %s).",
        version,
    )
    return True
