"""Async adapter for the existing NewMessage handler and the restart-safe owner outbox."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import weakref

from splusthon import errors, functions, types

import brand
from pv_messages import CARDS, owner_report
from pv_rules import is_attached_photo, is_text_message, media_of
from pv_store import Input
from sender import resolve_peer

log = logging.getLogger('acod.pv')


def _rpc_id(key):
    # Stable through retries, including ambiguous disconnects after server acceptance.
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), 'big', signed=True) or 1


class PvOrders:
    def __init__(self, cfg, store, sender, now):
        self.cfg, self.store, self.sender = cfg, store, sender
        self._now = now
        self._locks = weakref.WeakValueDictionary()
        self._wake = asyncio.Event()

    def user_lock(self, uid):
        # Single event-loop access; unused users don't leave an unbounded lock map.
        lock = self._locks.get(uid)
        if lock is None:
            self._locks[uid] = lock = asyncio.Lock()
        return lock

    def timestamp(self):
        return int(self._now().timestamp())

    async def handle(self, client, event, *, username, display_name, idle_command):
        media = media_of(event)
        msg = Input(int(event.sender_id), int(event.chat_id), int(event.id), event.raw_text or '',
                    is_attached_photo(media), is_text_message(media), username, display_name, idle_command)
        result = await asyncio.to_thread(self.store.pv_consume, msg, self.timestamp(), self.cfg.max_message_chars)
        if result.queued:
            self._wake.set()  # after COMMIT; never block this user on owner delivery
        if not result.reply:
            return
        key = result.reply
        if key in ('1','2','3','4','5'):
            text = brand.PV_REPLIES[brand.MENU_OPTIONS[int(key)-1]]
            entities = brand.quote_bold_entities(text)
        elif key == 'help':
            text, entities = brand.build_help_message()
        else:
            card = CARDS[key]
            text, entities = card.text, card.entities()
        report = await self.sender.send_entities(
            client, await resolve_peer(event), text, entities,
            reply_to_msg_id=msg.msg_id if key == 'help' else None,
            random_id=_rpc_id(f'pv:{msg.user_id}:{msg.msg_id}:{key}'))
        if report.ok:
            await asyncio.to_thread(self.store.pv_replied, msg.user_id, msg.msg_id)

    async def _owner_peer(self, client, owner_id):
        owner = await asyncio.to_thread(self.store.get_owner)
        if owner is None or owner.user_id != owner_id:
            # Revoked or manually replaced while an order was pending. Do not leak it.
            raise ValueError('Global owner changed before delivery')
        try:
            peer = await client.get_input_entity(owner_id)
        except (ValueError, TypeError):
            if not owner.username:
                raise
            # The address may help fill a cold session cache, but can NEVER
            # authorize a recipient: usernames can change hands.
            peer = await client.get_input_entity('@' + owner.username.lstrip('@'))
        if isinstance(peer, types.InputPeerSelf) and (await client.get_me()).id == owner_id:
            return peer
        if not isinstance(peer, (types.InputPeerUser, types.InputPeerUserFromMessage)) or peer.user_id != owner_id:
            raise ValueError('Could not verify global owner peer')
        return peer

    async def _deliver(self, client, job):
        order = job.order
        owner_peer = await self._owner_peer(client, order.owner_id)
        rid = _rpc_id(f'{job.rpc_seed}:{job.phase}:{job.part}')
        if job.phase == 'report':
            parts = list(owner_report(order).parts(job.part_limit))
            card = parts[job.part]
            report = await self.sender.send_entities(client, owner_peer, card.text, card.entities(), random_id=rid)
            if not report.ok:
                raise RuntimeError('OwnerReportNotAcknowledged')
            if job.part + 1 < len(parts):
                return 'report', job.part + 1
            return ('photo' if order.photo_msg_id is not None else 'sent'), 0
        # Server-side forward by message ID only. No byte/file access. Protected
        # messages are NOT copied via any fallback if forwarding is forbidden.
        try:
            await client(functions.messages.ForwardMessagesRequest(
                from_peer=await client.get_input_entity(order.source_chat_id), id=[order.photo_msg_id],
                to_peer=owner_peer, random_id=[rid]))
        except errors.RandomIdDuplicateError:
            pass  # the same persisted forward id was already accepted
        return 'sent', 0

    async def deliver_due(self, client, *, batch=32):
        """May also be called on startup without ANY new user message."""
        processed = 0
        for _ in range(batch):
            job = await asyncio.to_thread(self.store.pv_take_job, self.timestamp())
            if job is None:
                break
            try:
                phase, part = await asyncio.wait_for(self._deliver(client, job), timeout=45)
            except Exception as exc:
                name = type(exc).__name__
                permanent = job.phase == 'photo' and name in {
                    'ChatForwardsRestrictedError', 'MessageIdInvalidError', 'MsgIdInvalidError',
                    'MessageEmptyError', 'ChatSendMediaForbiddenError', 'ChatSendPhotosForbiddenError',
                }
                delay = max(min(300, 5 * 2**min(job.attempts, 6)), int(getattr(exc, 'seconds', 0) or 0))
                await asyncio.to_thread(self.store.pv_finish_job, job,
                                        phase='photo_unavailable' if permanent else job.phase, part=job.part,
                                        retry_at=0 if permanent else self.timestamp() + delay, error=name)
                log.warning('Order %s: owner %s delivery %s (%s)', job.order.id, job.phase,
                            'unavailable' if permanent else 'pending', name)
            else:
                await asyncio.to_thread(self.store.pv_finish_job, job, phase=phase, part=part)
            processed += 1
        return processed

    async def run_outbox(self, client):
        while True:
            self._wake.clear()
            try:
                if await self.deliver_due(client) == 32:
                    continue
            except Exception:
                log.exception('PV outbox temporarily unavailable')
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass
