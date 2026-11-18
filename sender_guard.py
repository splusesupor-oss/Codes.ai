"""Silent ingress filter. Inspect the actual sender, never the text, forward or reply target."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from enum import Enum, auto

log = logging.getLogger('acod.sender_guard')


class Author(Enum):
    USER = auto()
    BOT = auto()
    SKIP = auto()  # our account, an explicitly muted peer, or unresolved identity


def _bot_handle(handle) -> bool:
    return isinstance(handle, str) and handle.strip().lstrip('@').casefold().endswith('bot')


def _verdict(user, uid: int) -> Author | None:
    """None means the update is too sparse to tell; hydrate the sender."""
    if user is None or getattr(user, 'id', None) != uid:
        return None
    if getattr(user, 'is_self', False):
        return Author.SKIP
    if (getattr(user, 'bot', False)
            or getattr(user, 'is_bot', False) is True
            or getattr(user, 'bot_info_version', None) is not None
            or _bot_handle(getattr(user, 'username', None))):
        return Author.BOT
    for alias in getattr(user, 'usernames', None) or ():
        if getattr(alias, 'active', False) and _bot_handle(getattr(alias, 'username', None)):
            return Author.BOT
    # UserEmpty / InputPeer / user.min cannot establish that a handle is absent.
    if not getattr(user, 'min', False) and hasattr(user, 'username'):
        return Author.USER  # A full user without a public username is valid.
    return None


class SenderGuard:
    def __init__(self, session_user_id=None, ignored_ids=frozenset()):
        self.session_user_id = session_user_id
        self.ignored_ids = ignored_ids
        self._cache: OrderedDict[int, tuple[float, Author]] = OrderedDict()

    def _remember(self, uid: int, kind: Author, seconds: int = 60) -> Author:
        self._cache[uid] = time.monotonic() + seconds, kind
        self._cache.move_to_end(uid)
        if len(self._cache) > 2048:
            self._cache.popitem(last=False)
        return kind

    async def classify(self, client, event) -> Author:
        uid = getattr(event, 'sender_id', None)
        if not uid or uid == self.session_user_id or uid in self.ignored_ids:
            return Author.SKIP
        if uid < 0:
            # Anonymous/channel posts aren't user accounts. Never classify the
            # humans in a group from the group's username.
            return Author.SKIP if getattr(event, 'is_private', False) else Author.USER

        profiles = [getattr(event, 'sender', None)]
        if getattr(event, 'is_private', False) and getattr(event, 'chat_id', None) == uid:
            profiles.append(getattr(event, 'chat', None))
        answers = [_verdict(p, uid) for p in profiles]
        for kind in (Author.SKIP, Author.BOT, Author.USER):
            if kind in answers:
                return self._remember(uid, kind)
        cached = self._cache.get(uid)
        if cached and cached[0] > time.monotonic():
            return cached[1]

        try:
            verdict = await asyncio.wait_for(self._resolve(client, event, uid), timeout=5)
        except Exception as exc:
            # No chat reply on resolution failure. Don't turn a flood wait or
            # a network outage into an auto-reply or repeated immediate RPCs.
            log.debug('Unresolved sender %s: %s', uid, type(exc).__name__)
            return self._remember(uid, Author.SKIP, max(5, int(getattr(exc, 'seconds', 0) or 0)))
        if verdict is None:
            return self._remember(uid, Author.SKIP, 5)
        return self._remember(uid, verdict)

    @staticmethod
    async def _resolve(client, event, uid):
        getters = [getattr(event, 'get_sender', None)]
        if getattr(event, 'is_private', False) and getattr(event, 'chat_id', None) == uid:
            getters.append(getattr(event, 'get_chat', None))
        for getter in getters:
            if callable(getter):
                try:
                    result = _verdict(await getter(), uid)
                except ValueError:  # peer missing from this SDK cache
                    continue
                if result is not None:
                    return result
        # Numeric sender only: no lookup by display name, via_bot_id or fwd_from.
        getter = getattr(client, 'get_entity', None)
        if callable(getter):
            return _verdict(await getter(uid), uid)
        return None
