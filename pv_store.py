"""PV state, quota and durable owner delivery. Uses OwnerStore's existing WAL connection."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Optional
from uuid import uuid4

from pv_rules import QUALITIES, TextStatus, check_text, option_number, wants_main_menu

WINDOW = 13 * 60 * 60
PHOTO_LIMIT = 4

PV_SCHEMA = """
CREATE TABLE IF NOT EXISTS pv_dialogs (
    user_id INTEGER PRIMARY KEY,
    step TEXT NOT NULL CHECK (step IN ('menu','kind','quality','prompt','photo','reset')),
    kind TEXT, quality TEXT, prompt TEXT,
    last_msg_id INTEGER NOT NULL, due_reply TEXT
);
CREATE TABLE IF NOT EXISTS pv_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, username TEXT, display_name TEXT, owner_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('create','edit')),
    quality TEXT NOT NULL CHECK (quality IN ('2K','3K','4K','6K')),
    prompt TEXT NOT NULL, created_at INTEGER NOT NULL,
    source_chat_id INTEGER NOT NULL, photo_msg_id INTEGER, final_msg_id INTEGER NOT NULL,
    UNIQUE(user_id, final_msg_id),
    CHECK ((kind='edit' AND photo_msg_id IS NOT NULL) OR (kind='create' AND photo_msg_id IS NULL))
);
CREATE INDEX IF NOT EXISTS pv_quota ON pv_orders(user_id,created_at);
CREATE TABLE IF NOT EXISTS pv_order_outbox (
    order_id INTEGER PRIMARY KEY REFERENCES pv_orders(id),
    phase TEXT NOT NULL DEFAULT 'report' CHECK (phase IN ('report','photo','sent','photo_unavailable')),
    part INTEGER NOT NULL DEFAULT 0, part_limit INTEGER NOT NULL, rpc_seed TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, retry_at INTEGER NOT NULL DEFAULT 0,
    lease TEXT, lease_until INTEGER NOT NULL DEFAULT 0, last_error TEXT
);
CREATE INDEX IF NOT EXISTS pv_pending ON pv_order_outbox(phase,retry_at,lease_until);
"""


@dataclass(frozen=True)
class Input:
    user_id: int
    chat_id: int
    msg_id: int
    text: str
    has_photo: bool
    is_text: bool
    username: Optional[str]
    display_name: Optional[str]
    idle_command: Optional[str] = None  # existing help or owner-only command


@dataclass(frozen=True)
class Result:
    reply: Optional[str] = None
    queued: bool = False
    feedback: bool = False  # reminder/rejection for one input, not a required transition

    @classmethod
    def from_code(cls, code: Optional[str], queued: bool = False) -> Result:
        if code == 'reject':
            return cls('invalid', queued, True)
        if code and code.startswith('hint:'):
            return cls(code.removeprefix('hint:'), queued, True)
        return cls(code, queued)


@dataclass(frozen=True)
class Order:
    id: int
    user_id: int
    username: Optional[str]
    display_name: Optional[str]
    owner_id: int
    kind: str
    quality: str
    prompt: str
    created_at: int
    source_chat_id: int
    photo_msg_id: Optional[int]
    final_msg_id: int


@dataclass
class Draft:
    step: str = 'menu'
    kind: Optional[str] = None
    quality: Optional[str] = None
    prompt: Optional[str] = None

    @classmethod
    def restore(cls, row) -> Draft:
        if row is None:
            return cls()
        draft = cls(**{k: row[k] for k in ('step', 'kind', 'quality', 'prompt')})
        if ((draft.step in ('quality', 'prompt', 'photo') and draft.kind not in ('edit', 'create'))
                or (draft.step in ('prompt', 'photo') and draft.quality not in QUALITIES)
                or (draft.step == 'photo' and (draft.kind != 'edit' or not draft.prompt))):
            return cls('reset')  # recover an inconsistent draft, never touch order records
        return draft

    def reminder(self) -> str:
        if self.step == 'prompt':
            return 'hint:edit' if self.kind == 'edit' else 'hint:create'
        return 'hint:' + ('menu' if self.step == 'reset' else self.step)


@dataclass(frozen=True)
class Job:
    order: Order
    phase: str
    part: int
    part_limit: int
    rpc_seed: str
    attempts: int
    lease: str


class PvStoreMixin:
    # All public PV operations may run in asyncio.to_thread. Never hold a DB
    # transaction while awaiting a network response or sleeping for a retry.
    @contextmanager
    def _pv_write(self):
        with self._lock, self._conn:
            self._conn.execute('BEGIN IMMEDIATE')
            yield self._conn

    def pv_consume(self, msg: Input, now: int, part_limit: int) -> Result:
        """One transaction: dedup, transition, check combined limit, insert and enqueue."""
        with self._pv_write() as db:
            row = db.execute('SELECT * FROM pv_dialogs WHERE user_id=?', (msg.user_id,)).fetchone()
            draft = Draft.restore(row)
            due = row['due_reply'] if row else None
            if row and msg.msg_id <= row['last_msg_id']:
                if msg.msg_id < row['last_msg_id']:
                    return Result()
                if due == 'invalid':  # ambiguous catch-all saved by older versions
                    due = draft.reminder()
                    # Re-evaluate feedback only; never commit a transition on replay.
                    _, checked = self._pv_step(db, draft, msg, now)
                    if checked == 'reject':
                        due = checked
                return Result.from_code(due)
            queued = False
            if not row or draft.step == 'reset':
                draft, reply = Draft(), 'menu'
            elif msg.is_text and wants_main_menu(msg.text):
                # Reset only this draft. Orders, quota, and other users are untouched.
                reply = 'hint:menu' if draft.step == 'menu' else 'menu'
                draft = Draft()
            elif due in ('menu', 'kind', 'quality', 'create', 'edit', 'photo'):
                # Recover a missed question before interpreting a new answer. A
                # stale error, reminder or link must never swallow a valid choice.
                reply = due
            else:
                draft, reply = self._pv_step(db, draft, msg, now)
                if reply == 'success':
                    self._pv_insert(db, draft, msg, now, part_limit)
                    draft, queued = Draft('reset'), True
            db.execute('''INSERT INTO pv_dialogs(user_id,step,kind,quality,prompt,last_msg_id,due_reply)
                          VALUES (:user_id,:step,:kind,:quality,:prompt,:last_msg_id,:due_reply)
                          ON CONFLICT(user_id) DO UPDATE SET step=excluded.step,kind=excluded.kind,
                          quality=excluded.quality,prompt=excluded.prompt,last_msg_id=excluded.last_msg_id,
                          due_reply=excluded.due_reply''',
                       dict(asdict(draft), user_id=msg.user_id, last_msg_id=msg.msg_id, due_reply=reply))
            return Result.from_code(reply, queued)

    @staticmethod
    def _pv_step(db, draft, msg, now):
        def full():
            n = db.execute('SELECT count(*) FROM pv_orders WHERE user_id=? AND created_at>?',
                           (msg.user_id, now-WINDOW)).fetchone()[0]
            return n >= PHOTO_LIMIT

        if msg.idle_command and (msg.is_text or draft.step == 'menu'):
            if draft.step == 'menu':
                return draft, ('help' if msg.idle_command == 'help' else None)
            return draft, draft.reminder()  # a command is not an order description

        choice = option_number(msg.text) if msg.is_text else None
        if draft.step == 'menu':
            if choice in (1, 2, 3, 4, 5):
                return draft, str(choice)
            if choice == 6:
                return (draft, 'reject') if full() else (Draft('kind'), 'kind')
            if choice == 7:
                return Draft('reset'), 'video'  # handoff, no order and no quota use
            if choice == 8:
                return draft, 'chat_test'
        elif draft.step == 'kind' and choice in (1, 2):
            return Draft('quality', 'edit' if choice == 1 else 'create'), 'quality'
        elif draft.step == 'quality' and choice in (1, 2, 3, 4):
            draft.step, draft.quality = 'prompt', QUALITIES[choice-1]
            return draft, draft.kind
        elif draft.step == 'prompt' and msg.is_text:
            status = check_text(msg.text)
            if status is TextStatus.INVALID:
                return draft, 'reject'
            if status is TextStatus.READY:
                draft.prompt = msg.text
                if draft.kind == 'edit':
                    draft.step = 'photo'
                    return draft, 'photo'
                return (Draft(), 'reject') if full() else (draft, 'success')
        elif draft.step == 'photo' and msg.has_photo:
            if check_text(msg.text, require_description=False) is TextStatus.INVALID:
                return draft, 'reject'
            return (Draft(), 'reject') if full() else (draft, 'success')
        # Ordinary chat, an out-of-range digit, or the wrong attachment is not
        # an order description. Show the original card without losing the draft.
        return draft, draft.reminder()

    @staticmethod
    def _pv_insert(db, draft, msg, now, part_limit):
        # Snapshot the GLOBAL owner, never the group's registered owner or @osine2.
        owner = db.execute('SELECT user_id FROM global_owner WHERE slot=1').fetchone()
        if not owner:
            raise RuntimeError('No global owner; PV order transaction rolled back')
        user = db.execute('SELECT username, display_name FROM pv_users WHERE user_id=?',
                          (msg.user_id,)).fetchone()
        cursor = db.execute('''INSERT INTO pv_orders(user_id,username,display_name,owner_id,kind,
                            quality,prompt,created_at,source_chat_id,photo_msg_id,final_msg_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                            (msg.user_id, msg.username or (user and user['username']),
                             msg.display_name or (user and user['display_name']), owner[0], draft.kind,
                             draft.quality, draft.prompt, now, msg.chat_id,
                             msg.msg_id if draft.kind == 'edit' else None, msg.msg_id))
        db.execute('INSERT INTO pv_order_outbox(order_id,part_limit,rpc_seed) VALUES (?,?,?)',
                   (cursor.lastrowid, max(2, min(part_limit, 4096)), uuid4().hex))

    def pv_replied(self, user_id, msg_id):
        with self._lock, self._conn:
            self._conn.execute('UPDATE pv_dialogs SET due_reply=NULL WHERE user_id=? AND last_msg_id=?',
                               (user_id, msg_id))

    def pv_take_job(self, now) -> Optional[Job]:
        with self._pv_write() as db:
            row = db.execute("""SELECT * FROM pv_order_outbox WHERE phase IN ('report','photo')
                              AND retry_at<=? AND lease_until<=? ORDER BY order_id LIMIT 1""",
                             (now, now)).fetchone()
            if not row:
                return None
            oid, lease = row['order_id'], uuid4().hex
            db.execute('UPDATE pv_order_outbox SET lease=?,lease_until=? WHERE order_id=?',
                       (lease, now+120, oid))
            order = db.execute('SELECT * FROM pv_orders WHERE id=?', (oid,)).fetchone()
            return Job(Order(**dict(order)), row['phase'], row['part'], row['part_limit'],
                       row['rpc_seed'], row['attempts'], lease)

    def pv_finish_job(self, job, *, phase, part, retry_at=0, error=None):
        with self._lock, self._conn:
            self._conn.execute('''UPDATE pv_order_outbox SET phase=?,part=?,retry_at=?,last_error=?,
                                  attempts=?,lease=NULL,lease_until=0 WHERE order_id=? AND lease=?''',
                               (phase, part, retry_at, error, job.attempts+1 if error else 0,
                                job.order.id, job.lease))
