"""
تست آفلاین پچ keepalive/reconnect SPlusthon (splusthon_patch.apply_patch).

این تست:
  * بدون هیچ اتصال شبکه‌ای اجرا می‌شود و به credential/session/ENV نیاز ندارد.
  * یک FakeConnection به MTProtoSender می‌دهد (مشابه tools/diag_splusthon_ping.py).
  * قبل از patch ثابت می‌کند باگ وجود دارد (_ping بعد از _reconnect ریست نمی‌شود).
  * بعد از apply_patch() ثابت می‌کند _ping در هر حالت (موفق/ناموفق) None است.
  * بررسی می‌کند که پچ در نسخهٔ ناهماهنگ/ناموجود اعمال نمی‌شود (safe failure).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, "/home/user/_research/SPlusthon")

import splusthon  # noqa: E402
import splusthon_patch  # noqa: E402
from splusthon.crypto import AuthKey  # noqa: E402
from splusthon.network import mtprotosender as _mps  # noqa: E402
from splusthon.network.mtprotosender import MTProtoSender  # noqa: E402


class _FakeConnection:
    """اتصال جعلی صرفاً برای تستِ reconnect بدون شبکه."""
    def __init__(self):
        self._connected = False
        self.disconnects = 0
        self.connects = 0

    async def connect(self, timeout=None):
        self._connected = True
        self.connects += 1

    async def disconnect(self):
        self._connected = False
        self.disconnects += 1

    async def send(self, data):
        pass

    async def recv(self):
        # هرگز برنمی‌گردد تا send_loop/recv_loop در طول تستِ ما پیام واقعی نگیرند؛
        # cancel از سوی _cancel()/disconnect آن را متوقف می‌کند.
        await asyncio.Event().wait()

    def __str__(self):
        return "_FakeConnection"


class _Silent(dict):
    def __init__(self):
        super().__init__()
        lg = logging.getLogger("test.silent")
        lg.disabled = True
        self._lg = lg

    def __getitem__(self, _k):
        return self._lg


def _make_sender(retries=2, delay=0.01) -> MTProtoSender:
    return MTProtoSender(
        AuthKey(b"\x01" * 256),
        loggers=_Silent(),
        retries=retries,
        delay=delay,
        auto_reconnect=True,
        auto_reconnect_callback=None,
    )


async def _connect_and_prime_ping(sender: MTProtoSender, conn: _FakeConnection) -> None:
    await sender.connect(conn)
    # یک keepalive پینگ بفرست (همان‌طور که _keepalive_loop انجام می‌دهد)
    sender._keepalive_ping(12345)
    # منتظر بمان تا send_loop واقعاً آن را در pending_state ثبت کند
    for _ in range(20):
        await asyncio.sleep(0.01)
        if len(sender._pending_state) == 1:
            break


class TestKeepalivePingPatch(unittest.TestCase):
    def setUp(self):
        # هر تست را با حالت unpatch شده شروع می‌کنیم تا حالت قبل از patch
        # هم قابل بررسی باشد.
        cls = _mps.MTProtoSender
        if getattr(cls._reconnect, splusthon_patch._PATCH_MARKER, False):
            # wrapper در cls._reconnect ذخیره شده؛ اوریجینال در کلیژر آن نیست،
            # پس برای تست، ابتدا با پَس‌زنیِ نسخهٔ تازه ما را مطمئن می‌کنیم
            # که در هر دو حالت (پچ شده/نشده) می‌توانیم _reconnect واقعی را
            # از یک کلاس تازه (با دوباره ایمپورت خام) بگیریم: ساده‌تر،
            # قبل از هر تست از روی کد mtprotosender._reconnect اوریجینال
            # ذخیره می‌شود.
            pass
        self._orig_reconnect = cls._reconnect

    def tearDown(self):
        # وضعیت را به قبل برمی‌گردانیم
        _mps.MTProtoSender._reconnect = self._orig_reconnect

    # --- آزمایش وجود باگ در نسخهٔ پایه (بدون patch) ---
    def test_bug_exists_without_patch(self):
        cls = _mps.MTProtoSender
        # مطمئن شویم اوریجینالِ بدون patch روی کلاس است
        if getattr(cls._reconnect, splusthon_patch._PATCH_MARKER, False):
            cls._reconnect = self._orig_reconnect

        async def _run():
            sender = _make_sender()
            conn = _FakeConnection()
            await _connect_and_prime_ping(sender, conn)
            self.assertEqual(sender._ping, 12345, "باید یک پینگ در جریان باشد")

            class SimClose(IOError):
                pass

            rt = asyncio.create_task(sender._reconnect(SimClose("sim")))
            try:
                await asyncio.wait_for(rt, timeout=2)
            except asyncio.TimeoutError:  # pragma: no cover
                rt.cancel()
                self.fail("_reconnect باید ظرف ۲ ثانیه خاتمه یابد")

            # ادعای اصلی: در نسخهٔ بدون پچ، _ping همان 12345 باقی می‌ماند.
            self.assertEqual(
                sender._ping, 12345,
                "باگ اثبات نشد: انتظار داریم در کد اصلی _ping ریست نشود "
                "(اگر در نسخهٔ نصب‌شده ریست می‌شود، شاید کتابخانه به‌روز شده)"
            )
            await sender.disconnect()

        asyncio.run(_run())

    # --- آزمایش اینکه patch اعمال می‌شود و _ping را ریست می‌کند ---
    def test_patch_resets_ping_after_successful_reconnect(self):
        # اطمینان از حالت unpatch اولیه
        _mps.MTProtoSender._reconnect = self._orig_reconnect
        self.assertTrue(
            splusthon_patch.apply_patch(),
            "در نسخهٔ SPlusthon 1.1.4 باید patch اعمال شود",
        )
        self.assertTrue(
            getattr(_mps.MTProtoSender._reconnect, splusthon_patch._PATCH_MARKER, False),
            "تابعِ _reconnect باید با wrapper پچ شده باشد",
        )

        async def _run():
            sender = _make_sender(retries=2, delay=0.01)
            conn = _FakeConnection()
            await _connect_and_prime_ping(sender, conn)
            self.assertEqual(sender._ping, 12345)

            class SimClose(IOError):
                pass

            rt = asyncio.create_task(sender._reconnect(SimClose("sim")))
            await asyncio.wait_for(rt, timeout=3)

            self.assertIsNone(
                sender._ping,
                "بعد از _reconnect موفق باید self._ping == None باشد",
            )

            # بعد از پچ، keepalive بعدی باید پینگ تازه بفرستد، نه reconnect دوباره:
            reconnecting_before = sender._reconnecting
            sender._keepalive_ping(99999)
            await asyncio.sleep(0.1)
            self.assertFalse(
                sender._reconnecting,
                "بعد از keepalive نباید دوباره reconnect شروع شود",
            )
            self.assertEqual(
                sender._ping, 99999,
                "keepalive بعدی باید پینگ تازه ثبت کند",
            )
            self.assertEqual(reconnecting_before, False)

            await sender.disconnect()

        asyncio.run(_run())

    def test_patch_is_idempotent(self):
        _mps.MTProtoSender._reconnect = self._orig_reconnect
        self.assertTrue(splusthon_patch.apply_patch())
        first = _mps.MTProtoSender._reconnect
        self.assertTrue(splusthon_patch.apply_patch())  # فراخوانی دوباره
        self.assertIs(
            _mps.MTProtoSender._reconnect, first,
            "فراخوانی‌های مکرر apply_patch() نباید wrapper تودرتو بسازد",
        )

    def test_patch_skips_on_mismatched_version(self):
        _mps.MTProtoSender._reconnect = self._orig_reconnect
        original_version = splusthon.__version__
        try:
            splusthon.__version__ = "9.9.9"  # شبیه‌سازی نسخهٔ نامنطبق
            self.assertFalse(
                splusthon_patch.apply_patch(),
                "برای نسخه‌های ناهماهنگ باید patch اعمال نشود",
            )
            self.assertFalse(
                getattr(_mps.MTProtoSender._reconnect, splusthon_patch._PATCH_MARKER, False),
                "تابع اصلی نباید تغییر کرده باشد",
            )
        finally:
            splusthon.__version__ = original_version

    def test_patch_resets_ping_even_when_retries_exhausted(self):
        """اگر reconnect موفق نشود (همه retry ها شکست بخورند)، _ping هم باید None شود."""
        _mps.MTProtoSender._reconnect = self._orig_reconnect
        self.assertTrue(splusthon_patch.apply_patch())

        class FailingConnection(_FakeConnection):
            async def connect(self, timeout=None):
                raise OSError("simulated DNS failure")

        async def _run():
            sender = _make_sender(retries=2, delay=0.001)
            conn = _FakeConnection()
            await _connect_and_prime_ping(sender, conn)
            self.assertEqual(sender._ping, 12345)

            # اتصال جاری را با یک اتصال همیشه‌درحال‌شکست جایگزین می‌کنیم تا
            # _reconnect در شاخه «not ok» (تمام retry ها شکست) بیفتد.
            sender._connection = FailingConnection()

            rt = asyncio.create_task(sender._reconnect(OSError("first")))
            wait_rt = asyncio.create_task(asyncio.wait_for(rt, timeout=3))
            wait_disc = asyncio.create_task(
                asyncio.wait([sender.disconnected], timeout=3)
            )
            await asyncio.wait([wait_rt, wait_disc], timeout=4)
            for t in (wait_rt, wait_disc, rt):
                if t.done():
                    try:
                        t.result()
                    except (ConnectionError, asyncio.TimeoutError, OSError):
                        pass
                    except Exception:
                        pass
            # await sender.disconnect() future های داخلی را clean می‌کند و جلو
            # هشدار «Future exception was never retrieved» را می‌گیرد.
            try:
                await asyncio.wait_for(sender.disconnect(), timeout=1)
            except Exception:
                pass
            self.assertIsNone(
                sender._ping,
                "حتی در صورت شکست کامل reconnect هم باید _ping == None باشد",
            )
            # بعد از شکست کامل، کتابخانه _disconnect را صدا می‌زند و user_connected
            # را False می‌کند.
            self.assertFalse(sender._user_connected)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
