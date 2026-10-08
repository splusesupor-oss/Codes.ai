"""
حافظه‌ی دائمی «مالک سراسری» (Global Owner) و تنظیمات ربات روی SQLite — با ثبت اتمیک (Atomic).

قواعدی که این ماژول تضمین می‌کند:
  * مالک سراسری بر اساس «شناسه یکتای حساب کاربر» (user_id) ذخیره می‌شود.
  * فقط «اولین» درخواست معتبر در کل ربات مالک را تعیین می‌کند.
  * ثبت به‌صورت Atomic است (BEGIN IMMEDIATE + WAL).
  * مالک ثبت‌شده (Registered Bot Owner) توسط مالک سراسری با دستور «Tery ai» ثبت و جایگزین می‌شود.
  * سهمیه روزانه و سقف کاربران مجاز به تفکیک گروه و با ریست در 00:00 Asia/Tehran مدیریت می‌شود.
  * تاریخ انقضای ربات با دقت زمانی و پشتیبانی از restart ذخیره می‌شود.
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

-- مالک ثبت‌شده (Registered Bot Owner) — تعیین‌شده توسط مالک سراسری با «Tery ai»
CREATE TABLE IF NOT EXISTS registered_bot_owner (
    slot          INTEGER PRIMARY KEY CHECK (slot = 1),
    user_id       INTEGER NOT NULL UNIQUE,
    username      TEXT,
    display_name  TEXT,
    registered_at TEXT NOT NULL,
    registered_by INTEGER NOT NULL
);

-- گروه‌هایی که با دستور «ai x cod» توسط مالک سراسری فعال شده‌اند
CREATE TABLE IF NOT EXISTS group_activations (
    chat_id      INTEGER PRIMARY KEY,
    activated_at TEXT NOT NULL,
    activated_by INTEGER NOT NULL
);

-- تنظیمات سراسری ربات (کلید-مقدار)
CREATE TABLE IF NOT EXISTS bot_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS owner_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    user_id     INTEGER,
    chat_id     INTEGER,
    message_id  INTEGER,
    result      TEXT NOT NULL            -- claimed | already_same_owner | already_other_owner
);

CREATE TABLE IF NOT EXISTS pv_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL UNIQUE,
    username      TEXT,
    display_name  TEXT,
    first_seen_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- هوش مصنوعی گروه‌ها (فقط GROUP) — همه‌ی وضعیت‌ها per-group
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_group_state (
    chat_id    INTEGER PRIMARY KEY,          -- هر گروه وضعیت مستقل خودش را دارد
    enabled    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_allowed_users (
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    username     TEXT,
    display_name TEXT,
    added_at     TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)           -- مجوز per-group و بر اساس user_id واقعی
);

CREATE TABLE IF NOT EXISTS ai_usage (
    chat_id  INTEGER NOT NULL,
    day      TEXT NOT NULL,                  -- YYYY-MM-DD بر اساس Asia/Tehran
    requests INTEGER NOT NULL DEFAULT 0,     -- تعداد درخواست‌های مصرف‌شده
    PRIMARY KEY (chat_id, day)
);
"""


@dataclass(frozen=True)
class PvUser:
    """یک کاربر ثبت‌شده‌ی چت خصوصی."""

    user_id: int
    username: Optional[str]
    display_name: Optional[str]
    first_seen_at: str


@dataclass(frozen=True)
class AiAllowedUser:
    """کاربر مجاز برای گفت‌وگو با AI در یک گروه مشخص."""

    chat_id: int
    user_id: int
    username: Optional[str]
    display_name: Optional[str]
    added_at: str


@dataclass(frozen=True)
class OwnerRecord:
    user_id: int
    username: Optional[str]
    display_name: Optional[str]
    claimed_chat_id: int
    claimed_message_id: Optional[int]
    claimed_at: str


@dataclass(frozen=True)
class RegisteredOwnerRecord:
    user_id: int
    username: Optional[str]
    display_name: Optional[str]
    registered_at: str
    registered_by: int


@dataclass(frozen=True)
class ClaimResult:
    """نتیجه‌ی تلاش برای ثبت مالکیت."""

    claimed: bool
    reason: str                       # claimed | already_same_owner | already_other_owner
    owner: Optional[OwnerRecord]      # مالک فعلی (چه تازه ثبت‌شده، چه قبلی)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


class OwnerStore:
    """ذخیره‌ساز اتمیک مالک سراسری و تنظیمات ربات."""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 30_000):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
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

    # ------------------------------------------------------------------ API مالک سراسری
    def get_owner(self) -> Optional[OwnerRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM global_owner WHERE slot = 1"
            ).fetchone()
        return self._row_to_owner(row)

    def is_owner(self, user_id: int) -> bool:
        owner = self.get_owner()
        return owner is not None and int(owner.user_id) == int(user_id)

    def has_owner(self) -> bool:
        """آیا هنوز هیچ مالک سراسری با «ai cod» ثبت نشده است؟"""
        return self.get_owner() is not None

    # ------------------------------------------------- مالک ثبت‌شده (Registered Bot Owner)
    def get_registered_bot_owner(self) -> Optional[RegisteredOwnerRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM registered_bot_owner WHERE slot = 1"
            ).fetchone()
        if row is None:
            return None
        return RegisteredOwnerRecord(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            registered_at=row["registered_at"],
            registered_by=row["registered_by"],
        )

    def is_registered_bot_owner(self, user_id: int) -> bool:
        rec = self.get_registered_bot_owner()
        return rec is not None and int(rec.user_id) == int(user_id)

    def set_registered_bot_owner(
        self,
        user_id: int,
        *,
        username: Optional[str] = None,
        display_name: Optional[str] = None,
        registered_by: int,
    ) -> None:
        user_id = int(user_id)
        registered_by = int(registered_by)
        with self._lock:
            self._conn.execute(
                """INSERT INTO registered_bot_owner
                       (slot, user_id, username, display_name, registered_at, registered_by)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(slot) DO UPDATE SET
                       user_id = excluded.user_id,
                       username = excluded.username,
                       display_name = excluded.display_name,
                       registered_at = excluded.registered_at,
                       registered_by = excluded.registered_by""",
                (user_id, username, display_name, _now(), registered_by),
            )
            self._conn.commit()

    def revoke_registered_bot_owner(self) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM registered_bot_owner WHERE slot = 1")
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------- فعال‌سازی پر-گروه (ai x cod)
    def activate_group(self, chat_id: int, activated_by: int) -> bool:
        chat_id = int(chat_id)
        with self._lock:
            self._conn.execute(
                """INSERT INTO group_activations (chat_id, activated_at, activated_by)
                   VALUES (?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       activated_at = excluded.activated_at,
                       activated_by = excluded.activated_by""",
                (chat_id, _now(), int(activated_by)),
            )
            self._conn.commit()
            return True

    def is_group_activated(self, chat_id: int) -> bool:
        chat_id = int(chat_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM group_activations WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return row is not None

    def deactivate_group(self, chat_id: int) -> bool:
        chat_id = int(chat_id)
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM group_activations WHERE chat_id = ?",
                (chat_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------- سهمیه و سقف اعضا
    def get_daily_quota(self, default_val: int = 5000) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_settings WHERE key = 'daily_quota'"
            ).fetchone()
        if row and row["value"]:
            try:
                return int(row["value"])
            except ValueError:
                pass
        return int(default_val)

    def set_daily_quota(self, quota: int) -> None:
        quota = int(quota)
        with self._lock:
            self._conn.execute(
                "INSERT INTO bot_settings(key, value) VALUES('daily_quota', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(quota),),
            )
            self._conn.commit()

    def get_max_allowed_users(self, default_val: int = 3) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_settings WHERE key = 'max_allowed_users'"
            ).fetchone()
        if row and row["value"]:
            try:
                return int(row["value"])
            except ValueError:
                pass
        return int(default_val)

    def set_max_allowed_users(self, limit: int) -> None:
        limit = int(limit)
        with self._lock:
            self._conn.execute(
                "INSERT INTO bot_settings(key, value) VALUES('max_allowed_users', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(limit),),
            )
            self._conn.commit()

    # ------------------------------------------------- انقضای ربات
    def get_expiration(self) -> Optional[_dt.datetime]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_settings WHERE key = 'bot_expires_at'"
            ).fetchone()
        if not row or not row["value"]:
            return None
        try:
            return _dt.datetime.fromisoformat(row["value"])
        except Exception:
            return None

    def set_expiration(self, expires_at: _dt.datetime | str) -> None:
        iso_val = expires_at if isinstance(expires_at, str) else expires_at.isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO bot_settings(key, value) VALUES('bot_expires_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (iso_val,),
            )
            self._conn.commit()

    def clear_expiration(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM bot_settings WHERE key = 'bot_expires_at'")
            self._conn.commit()

    def is_expired(self, now: Optional[_dt.datetime] = None) -> bool:
        exp = self.get_expiration()
        if exp is None:
            return False
        if now is None:
            if exp.tzinfo is not None:
                now = _dt.datetime.now(exp.tzinfo)
            else:
                now = _dt.datetime.now(_dt.timezone.utc)
        elif exp.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=exp.tzinfo)
        elif exp.tzinfo is not None and now.tzinfo is not None:
            now = now.astimezone(exp.tzinfo)
        return now >= exp

    # ------------------------------------------------- خاموشی کلی ربات
    _KEY_SUSPENDED = "global_suspended"

    def is_global_suspended(self) -> bool:
        if not self.has_owner():
            return True
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (self._KEY_SUSPENDED,),
            ).fetchone()
        return bool(row) and (row["value"] == "1")

    def set_global_suspended(self, suspended: bool) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO bot_settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (self._KEY_SUSPENDED, "1" if suspended else "0"),
            )
            self._conn.commit()

    def is_active(self, now: Optional[_dt.datetime] = None) -> bool:
        """آیا ربات در حالت فعال است؟ (مالک دارد، معلق نیست و منقضی نشده)"""
        return self.has_owner() and not self.is_global_suspended() and not self.is_expired(now)

    def claim(
        self,
        user_id: int,
        *,
        chat_id: int,
        message_id: Optional[int] = None,
        username: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> ClaimResult:
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

    # -------------------------------------------------- کاربران چت خصوصی (PV)
    def register_pv_user(
        self,
        user_id: int,
        *,
        username: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> bool:
        user_id = int(user_id)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                cur = self._conn.execute(
                    """INSERT OR IGNORE INTO pv_users
                           (user_id, username, display_name, first_seen_at)
                       VALUES (?, ?, ?, ?)""",
                    (user_id, username, display_name, _now()),
                )
                self._conn.execute("COMMIT")
                return cur.rowcount == 1
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def count_pv_users(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM pv_users").fetchone()
        return int(row["c"])

    def list_pv_users(self) -> list[PvUser]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pv_users ORDER BY id ASC"
            ).fetchall()
        return [
            PvUser(
                user_id=r["user_id"],
                username=r["username"],
                display_name=r["display_name"],
                first_seen_at=r["first_seen_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------ هوش مصنوعی گروه‌ها (AI)
    def ai_set_enabled(self, chat_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO ai_group_state (chat_id, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       enabled = excluded.enabled,
                       updated_at = excluded.updated_at""",
                (int(chat_id), 1 if enabled else 0, _now()),
            )
            self._conn.commit()

    def ai_is_enabled(self, chat_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT enabled FROM ai_group_state WHERE chat_id = ?", (int(chat_id),)
            ).fetchone()
        return bool(row["enabled"]) if row else False

    def ai_allow_user(
        self,
        chat_id: int,
        user_id: int,
        *,
        username: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> bool:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                existed = self._conn.execute(
                    "SELECT 1 FROM ai_allowed_users WHERE chat_id = ? AND user_id = ?",
                    (int(chat_id), int(user_id)),
                ).fetchone() is not None
                self._conn.execute(
                    """INSERT INTO ai_allowed_users
                           (chat_id, user_id, username, display_name, added_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(chat_id, user_id) DO UPDATE SET
                           username = excluded.username,
                           display_name = excluded.display_name""",
                    (int(chat_id), int(user_id), username, display_name, _now()),
                )
                self._conn.execute("COMMIT")
                return not existed
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def ai_is_allowed(self, chat_id: int, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM ai_allowed_users WHERE chat_id = ? AND user_id = ?",
                (int(chat_id), int(user_id)),
            ).fetchone()
        return row is not None

    def ai_revoke_user(self, chat_id: int, user_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM ai_allowed_users WHERE chat_id = ? AND user_id = ?",
                (int(chat_id), int(user_id)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def ai_allowed_users(self, chat_id: int) -> list[AiAllowedUser]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ai_allowed_users WHERE chat_id = ? ORDER BY added_at, user_id",
                (int(chat_id),),
            ).fetchall()
        return [
            AiAllowedUser(
                chat_id=r["chat_id"],
                user_id=r["user_id"],
                username=r["username"],
                display_name=r["display_name"],
                added_at=r["added_at"],
            )
            for r in rows
        ]

    def ai_usage(self, chat_id: int, day: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT requests FROM ai_usage WHERE chat_id = ? AND day = ?",
                (int(chat_id), day),
            ).fetchone()
        return int(row["requests"]) if row else 0

    def ai_used_quota(self, chat_id: int, day: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT requests FROM ai_usage WHERE chat_id = ? AND day = ?",
                (int(chat_id), day),
            ).fetchone()
            return int(row["requests"]) if row else 0

    def ai_consume_quota(self, chat_id: int, day: str, limit: int) -> bool:
        chat_id, limit = int(chat_id), int(limit)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                used = self.ai_used_quota(chat_id, day)
                if used >= limit:
                    self._conn.execute("ROLLBACK")
                    return False
                self._conn.execute(
                    """INSERT INTO ai_usage (chat_id, day, requests) VALUES (?, ?, 1)
                       ON CONFLICT(chat_id, day) DO UPDATE SET requests = requests + 1""",
                    (chat_id, day),
                )
                self._conn.execute("COMMIT")
                return True
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def ai_exhaust_quota(self, chat_id: int, day: str, limit: int) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO ai_usage (chat_id, day, requests) VALUES (?, ?, ?)
                   ON CONFLICT(chat_id, day) DO UPDATE SET requests = ?""",
                (int(chat_id), day, int(limit), int(limit)),
            )
            self._conn.commit()

    def attempts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM owner_attempts ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def export_json(self) -> str:
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
