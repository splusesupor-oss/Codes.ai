"""
حافظه‌ی دائمی «مالک سراسری» (Global Owner) روی SQLite — با ثبت اتمیک (Atomic).

قواعدی که این ماژول تضمین می‌کند:
  * مالک بر اساس «شناسه یکتای حساب کاربر» (user_id) ذخیره می‌شود و وابسته به گروه نیست.
  * فقط «اولین» درخواست معتبر در کل ربات مالک را تعیین می‌کند.
  * ثبت به‌صورت Atomic است: حتی اگر دو پیام «ai cod» هم‌زمان برسند (حتی از دو پروسه‌ی
    جداگانه)، فقط یکی برنده می‌شود. این کار با ترکیب این‌ها انجام می‌شود:
        - CONSTRAINT یکتا: slot = 1 و PRIMARY KEY روی همان ستون
        - تراکنش BEGIN IMMEDIATE (قفل نوشتن در سطح دیتابیس)
        - حالت WAL و busy_timeout برای مقاومت در برابر قفل هم‌زمان
  * با Restart ربات، مالک تغییر نمی‌کند (چون روی دیسک ذخیره می‌شود).
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS global_owner (
    slot               INTEGER PRIMARY KEY CHECK (slot = 1),   -- فقط یک رکورد مجاز است
    user_id            INTEGER NOT NULL UNIQUE,                -- شناسه یکتای حساب کاربری
    username           TEXT,
    display_name       TEXT,
    claimed_chat_id    INTEGER NOT NULL,
    claimed_message_id INTEGER,
    claimed_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    user_id     INTEGER,
    chat_id     INTEGER,
    message_id  INTEGER,
    result      TEXT NOT NULL            -- claimed | already_same_owner | already_other_owner
);
"""


@dataclass(frozen=True)
class OwnerRecord:
    user_id: int
    username: Optional[str]
    display_name: Optional[str]
    claimed_chat_id: int
    claimed_message_id: Optional[int]
    claimed_at: str


@dataclass(frozen=True)
class ClaimResult:
    """نتیجه‌ی تلاش برای ثبت مالکیت."""

    claimed: bool
    reason: str                       # claimed | already_same_owner | already_other_owner
    owner: Optional[OwnerRecord]      # مالک فعلی (چه تازه ثبت‌شده، چه قبلی)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class OwnerStore:
    """ذخیره‌ساز اتمیک مالک سراسری."""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 30_000):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # check_same_thread=False + قفل داخلی: هم برای استفاده‌ی تک‌پروسه‌ای تمیز است،
        # هم تست هم‌زمانی چندنخی را ممکن می‌کند.
        # NOTE: استفاده از BEGIN IMMEDIATE + WAL باعث می‌شود چند پروسه هم امن باشند.
        self._conn = sqlite3.connect(
            str(self.db_path), timeout=busy_timeout_ms / 1000.0, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ API
    def get_owner(self) -> Optional[OwnerRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM global_owner WHERE slot = 1"
            ).fetchone()
        return self._row_to_owner(row)

    def is_owner(self, user_id: int) -> bool:
        owner = self.get_owner()
        return owner is not None and int(owner.user_id) == int(user_id)

    def claim(
        self,
        user_id: int,
        *,
        chat_id: int,
        message_id: Optional[int] = None,
        username: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> ClaimResult:
        """تلاش برای ثبت این کاربر به‌عنوان مالک سراسری.

        این متد Atomic است: اگر مالکی وجود داشته باشد، هیچ‌چیز نوشته نمی‌شود.
        """
        user_id = int(user_id)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT * FROM global_owner WHERE slot = 1"
                ).fetchone()

                if row is not None:
                    owner = self._row_to_owner(row)
                    reason = (
                        "already_same_owner"
                        if int(owner.user_id) == user_id
                        else "already_other_owner"
                    )
                    self._conn.execute("COMMIT")
                    self._log_attempt(user_id, chat_id, message_id, reason)
                    return ClaimResult(False, reason, owner)

                try:
                    self._conn.execute(
                        """INSERT INTO global_owner
                               (slot, user_id, username, display_name,
                                claimed_chat_id, claimed_message_id, claimed_at)
                           VALUES (1, ?, ?, ?, ?, ?, ?)""",
                        (user_id, username, display_name, int(chat_id),
                         message_id, _now()),
                    )
                except sqlite3.IntegrityError:
                    # مسابقه باخته شد (پروسه‌ی دیگری زودتر ثبت کرده است)
                    self._conn.execute("ROLLBACK")
                    owner = self.get_owner()
                    reason = (
                        "already_same_owner"
                        if owner and int(owner.user_id) == user_id
                        else "already_other_owner"
                    )
                    self._log_attempt(user_id, chat_id, message_id, reason)
                    return ClaimResult(False, reason, owner)

                self._conn.execute("COMMIT")
                owner = self.get_owner()
                self._log_attempt(user_id, chat_id, message_id, "claimed")
                return ClaimResult(True, "claimed", owner)
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def attempts(self) -> list[dict]:
        """سابقه‌ی همه‌ی تلاش‌های «ai cod» (برای تست و دیباگ)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM owner_attempts ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def export_json(self) -> str:
        """نسخه‌ی JSON از مالک فعلی (برای بکاپ/بازرسی)."""
        import json

        owner = self.get_owner()
        return json.dumps(
            None if owner is None else owner.__dict__, ensure_ascii=False, indent=2
        )

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # -------------------------------------------------------------- internal
    def _log_attempt(self, user_id, chat_id, message_id, result) -> None:
        try:
            self._conn.execute(
                """INSERT INTO owner_attempts (at, user_id, chat_id, message_id, result)
                   VALUES (?, ?, ?, ?, ?)""",
                (_now(), user_id, chat_id, message_id, result),
            )
            self._conn.commit()
        except sqlite3.Error:
            # لاگ نباید مسیر اصلی را خراب کند
            pass

    @staticmethod
    def _row_to_owner(row) -> Optional[OwnerRecord]:
        if row is None:
            return None
        return OwnerRecord(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            claimed_chat_id=row["claimed_chat_id"],
            claimed_message_id=row["claimed_message_id"],
            claimed_at=row["claimed_at"],
        )
