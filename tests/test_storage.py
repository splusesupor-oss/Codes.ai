"""تست حافظه‌ی دائمی و اتمیک بودن ثبت مالک سراسری (بندهای ۶ تا ۸ نیازمندی‌ها)."""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import OwnerStore  # noqa: E402


def _try_claim_worker(db_path: str, user_id: int, barrier, queue) -> None:
    """کارگر پروسه‌ای برای تست هم‌زمانی واقعی (چند پروسه روی یک فایل)."""
    store = OwnerStore(db_path)
    try:
        barrier.wait(timeout=30)
        result = store.claim(user_id, chat_id=-100000 - user_id, message_id=user_id)
        queue.put((user_id, result.claimed, result.reason))
    finally:
        store.close()


class TestOwnerStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "owner.sqlite3"
        self.store = OwnerStore(self.db)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    # ------------------------------------------------------------------ پایه
    def test_first_claim_wins(self):
        result = self.store.claim(111, chat_id=-1001, message_id=5, display_name="کاربر یک")
        self.assertTrue(result.claimed)
        self.assertEqual(result.reason, "claimed")
        self.assertEqual(self.store.get_owner().user_id, 111)

    def test_second_user_is_rejected(self):
        self.store.claim(111, chat_id=-1001)
        result = self.store.claim(222, chat_id=-1002, message_id=9)
        self.assertFalse(result.claimed)
        self.assertEqual(result.reason, "already_other_owner")
        self.assertEqual(self.store.get_owner().user_id, 111)   # مالک عوض نشد

    def test_ownership_is_not_group_dependent(self):
        # کاربر دوم در «گروه دیگر» هم نمی‌تواند مالک شود
        self.store.claim(111, chat_id=-5001)
        result = self.store.claim(222, chat_id=-9999, message_id=77)
        self.assertFalse(result.claimed)
        self.assertEqual(self.store.get_owner().user_id, 111)
        self.assertEqual(self.store.get_owner().claimed_chat_id, -5001)

    def test_same_owner_repeating_does_not_create_new_owner(self):
        self.store.claim(111, chat_id=-1001)
        before = self.store.get_owner()
        result = self.store.claim(111, chat_id=-2002, message_id=42)
        self.assertFalse(result.claimed)
        self.assertEqual(result.reason, "already_same_owner")
        after = self.store.get_owner()
        self.assertEqual(before.claimed_chat_id, after.claimed_chat_id)   # رکورد دست‌نخورده
        self.assertEqual(after.user_id, 111)

    # -------------------------------------------------- پایداری بعد از ری‌استارت
    def test_owner_survives_restart(self):
        self.store.claim(111, chat_id=-1001, display_name="مالک اول")
        self.store.close()                       # «خاموش شدن» ربات

        store2 = OwnerStore(self.db)             # «روشن شدن» دوباره
        try:
            owner = store2.get_owner()
            self.assertIsNotNone(owner)
            self.assertEqual(owner.user_id, 111)
            # و کاربر دیگری بعد از ری‌استارت هم نمی‌تواند مالک شود
            result = store2.claim(333, chat_id=-4004)
            self.assertFalse(result.claimed)
            self.assertEqual(store2.get_owner().user_id, 111)
        finally:
            store2.close()
            self.store = OwnerStore(self.db)     # برای tearDown

    # ------------------------------------------------------------- هم‌زمانی
    def test_atomicity_with_threads(self):
        """۲۰ نخ هم‌زمان؛ فقط یکی باید برنده شود."""
        results = []
        lock = threading.Lock()
        start = threading.Barrier(20)

        def worker(uid: int):
            start.wait(timeout=30)
            res = self.store.claim(uid, chat_id=-1000 - uid)
            with lock:
                results.append((uid, res.claimed))

        threads = [threading.Thread(target=worker, args=(1000 + i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        winners = [uid for uid, claimed in results if claimed]
        self.assertEqual(len(winners), 1, f"باید فقط یک برنده باشد: {results}")
        self.assertEqual(self.store.get_owner().user_id, winners[0])

    def test_atomicity_with_processes(self):
        """۸ پروسه‌ی جداگانه روی یک فایل؛ فقط یکی باید برنده شود."""
        store = OwnerStore(self.db)
        store.close()  # دیتابیس ساخته شد و اتصال بسته شد
        self.store.close()  # اتصال اصلی هم قبل از fork بسته می‌شود (تمیز)

        ctx = multiprocessing.get_context("fork")
        barrier = ctx.Barrier(8)
        queue = ctx.Queue()
        procs = [
            ctx.Process(target=_try_claim_worker, args=(str(self.db), 2000 + i, barrier, queue))
            for i in range(8)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)

        for p in procs:
            p.close()

        outcomes = [queue.get(timeout=10) for _ in range(8)]
        winners = [uid for uid, claimed, _ in outcomes if claimed]
        self.assertEqual(len(winners), 1, f"ثبت اتمیک شکست خورد: {outcomes}")

        verify = OwnerStore(self.db)
        try:
            self.assertEqual(verify.get_owner().user_id, winners[0])
        finally:
            verify.close()
            self.store = OwnerStore(self.db)

    # ------------------------------------------------------------------ لاگ
    def test_attempts_are_logged(self):
        self.store.claim(111, chat_id=-1)
        self.store.claim(222, chat_id=-2)
        self.store.claim(111, chat_id=-3)
        results = [a["result"] for a in self.store.attempts()]
        self.assertEqual(
            results, ["claimed", "already_other_owner", "already_same_owner"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
