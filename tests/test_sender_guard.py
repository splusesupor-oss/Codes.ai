"""Regression for PV error ping-pong and silent bot handling in group dispatch."""
import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from splusthon import events, types

import brand
import pv_messages as ui
from config import Config
from core import BotCore
from storage import OwnerStore
from tests.fakes import FakeClient, FakeEvent, FakeReplyMessage

OWNER, SELF, PEER, OTHER, GROUP = 501, 502, 503, 504, -101
BLOCKED = '🚫 شما از این ربات مسدود شده‌اید.'


def user(uid=PEER, **kwargs):
    return types.User(id=uid, user_type=types.UserTypeNormal(), **kwargs)


class SenderLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = OwnerStore(Path(tmp.name) / 'owner.sqlite3')
        self.addCleanup(self.store.close)
        self.store.claim(OWNER, chat_id=GROUP)
        self.core = BotCore(Config(), self.store, session_user_id=SELF)
        self.client = FakeClient(me_id=SELF)

    def event(self, text=BLOCKED, *, uid=PEER, group=False, **kw):
        return FakeEvent(text, user_id=uid, chat_id=GROUP if group else uid, is_group=group, **kw)

    async def receive(self, event):
        await self.core.on_new_message(self.client, event)

    def assert_pv_untouched(self):
        self.assertEqual(self.client.all_requests, [])  # includes typing requests
        self.assertEqual(self.store.count_pv_users(), 0)
        for table in ('pv_dialogs', 'pv_orders', 'pv_order_outbox'):
            self.assertEqual(self.store._conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)

    async def test_reported_loop_bot_receives_zero_replies_for_distinct_messages(self):
        for name in ('@Aifox_bot', 'FoxBOT', '  @SupportBoT  '):
            for text in (BLOCKED, 'درخواست نامعتبر', '۶', BLOCKED):
                await self.receive(self.event(text, username=name))
        self.assert_pv_untouched()

    async def test_native_bot_flag_even_without_username(self):
        peer = types.PeerUser(PEER)
        msg = types.Message(id=1, peer_id=peer, from_id=peer, out=False, message='سلام')
        msg._sender = user(bot=True, bot_info_version=0, username=None)
        await self.receive(events.NewMessage.Event(msg))
        self.assert_pv_untouched()

    async def test_active_alias_blocks_but_inactive_alias_does_not(self):
        e = self.event(); e.sender = user(username='fox', usernames=[types.Username('foxBot', active=True)])
        await self.receive(e)
        self.assert_pv_untouched()
        e = self.event(); e.sender = user(username='fox', usernames=[types.Username('old_bot', active=False)])
        await self.receive(e)
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT])

    async def test_missing_and_minimal_sender_resolved_and_not_queried_on_every_echo(self):
        getter = AsyncMock(return_value=user(username='FoxBot'))
        self.client.get_entity = AsyncMock(side_effect=AssertionError('unnecessary lookup'))
        for partial in (None, user(min=True), types.UserEmpty(PEER)):
            e = self.event(); e.sender = partial; e.get_sender = getter
            await self.receive(e)
        self.assert_pv_untouched()
        getter.assert_awaited_once()
        self.client.get_entity.assert_not_awaited()

    async def test_private_chat_can_supply_missing_bot_profile(self):
        e = self.event(); e.sender = None; e.chat = user(bot=True)
        e.get_sender = AsyncMock(side_effect=AssertionError('no RPC required'))
        await self.receive(e)
        self.assert_pv_untouched()
        e.get_sender.assert_not_awaited()

    async def test_private_peer_resolves_when_sender_cache_empty(self):
        e = self.event(); e.sender = types.UserEmpty(PEER)
        e.get_sender = AsyncMock(side_effect=ValueError('no cached sender'))
        e.get_chat = AsyncMock(return_value=user(username='FoxBOT'))
        await self.receive(e)
        e.get_chat.assert_awaited_once()
        self.assert_pv_untouched()

    async def test_unknown_peer_is_silent_full_user_without_username_can_recover(self):
        self.client.get_entity = AsyncMock(side_effect=ConnectionError('disconnected'))
        for _ in range(2):
            e = self.event(); e.sender = None
            await self.receive(e)
        self.assert_pv_untouched()
        self.client.get_entity.assert_awaited_once_with(PEER)
        e = self.event('سلام'); e.sender = user(first_name='بدون نام کاربری')
        await self.receive(e)
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT])

    async def test_actual_sender_not_text_display_name_forward_via_or_replied_bot(self):
        for value in ('کار با @FoxBot', '۴'):
            e = self.event(value, username='bot_fan', display_name='Fox Bot',
                           reply_to=FakeReplyMessage(sender_id=OTHER, username='fox_bot'))
            e.via_bot_id = OTHER
            e.fwd_from = SimpleNamespace(from_id=types.PeerUser(OTHER))
            await self.receive(e)
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT, 'https://acod.osine2.workers.dev/'])

    async def test_self_input_with_missing_out_flag_and_outgoing_echo(self):
        await self.receive(self.event(uid=SELF, out=False))
        e = self.event(); e.sender = user(is_self=True)
        await self.receive(e)
        await self.receive(self.event(uid=OTHER, out=True))
        self.assert_pv_untouched()

    async def test_no_mutation_of_existing_draft_or_quota_even_with_cached_human(self):
        for text in ('سلام', '6', '2', '2'):
            await self.receive(self.event(text, username='old_handle'))
        self.client.clear_requests()
        before = dict(self.store._conn.execute('SELECT * FROM pv_dialogs').fetchone())
        await self.receive(self.event('نور و رنگ درخت را اصلاح کن', username='new_BOT'))
        self.assertEqual(self.client.all_requests, [])
        self.assertEqual(dict(self.store._conn.execute('SELECT * FROM pv_dialogs').fetchone()), before)
        self.assertEqual(self.store._conn.execute('SELECT COUNT(*) FROM pv_orders').fetchone()[0], 0)

    async def test_group_bot_commands_and_ai_reply_never_run(self):
        self.core.ai.handle_chat = AsyncMock()
        self.core.ai.handle_admin_command = AsyncMock()
        for text in ('ai cod', 'کدرز', 'راهنما', 'ai', 'ai online', 'سلام'):
            # Even an existing owner ID is not a bypass: no toggles, typing or AI.
            await self.receive(self.event(text, uid=OWNER, username='owner_bot', group=True,
                                         reply_to=FakeReplyMessage(sender_id=SELF, out=True)))
        self.assertEqual(self.client.all_requests, [])
        self.assertTrue(self.store.is_active())
        self.core.ai.handle_chat.assert_not_awaited()
        self.core.ai.handle_admin_command.assert_not_awaited()
        self.assertEqual(self.core.ai.get_recent_group_messages(GROUP), [])
        self.assertEqual(self.store.get_owner().user_id, OWNER)

    async def test_group_title_and_group_handle_are_not_the_sender(self):
        e = self.event('کدرز', group=True, username='human')
        e.chat = SimpleNamespace(title='FoxBot', username='group_bot', id=PEER, bot=True)
        await self.receive(e)
        self.assertEqual(self.client.text_messages(), [brand.FULL_TEXT])

    async def test_group_ad_by_bot_is_moderated_without_a_reply_or_owner_ping(self):
        self.store.add_filtered_word(GROUP, 'تبلیغ', OWNER)
        await self.receive(self.event('این یک تبلیغ است', username='offers_bot', group=True, msg_id=10))
        self.assertEqual(self.client.deleted_messages, [(GROUP, [10])])
        self.assertEqual(self.client.all_requests, [])
        ads = self.core.ai.get_recent_group_messages(GROUP)
        self.assertTrue(any(m.get('is_ad') for m in ads))

    async def test_user_session_clone_can_be_muted_by_id_without_affecting_others(self):
        cfg = replace(Config(), ignored_sender_ids=frozenset({PEER}))
        self.core = BotCore(cfg, self.store, session_user_id=SELF)
        await self.receive(self.event(username='foxora'))
        await self.receive(self.event('کدرز', group=True, username='different_handle'))
        self.assert_pv_untouched()
        await self.receive(self.event(uid=OTHER, username='foxora'))
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT])

    async def test_unmarked_echo_breaks_per_peer_and_does_not_mute_valid_choices(self):
        # This clone looks exactly like a human. Feed back a new incoming notice
        # for every outgoing reply, as in the screenshot. The chain must stop.
        for _ in range(10):
            before = len(self.client.requests)
            await self.receive(self.event(BLOCKED, username='foxora'))
            if len(self.client.requests) == before:
                break
        else:
            self.fail('unbounded auto-reply loop')
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT, brand.MENU_TEXT])
        await self.receive(self.event('سلام', uid=OTHER))
        await self.receive(self.event(BLOCKED, uid=OTHER))
        await self.receive(self.event('1'))
        self.assertEqual(self.client.text_messages()[-3:], [brand.MENU_TEXT, brand.MENU_TEXT, '@osine2'])
        await self.receive(self.event(BLOCKED))  # a valid choice reset the fuse
        self.assertEqual(self.client.text_messages()[-1], brand.MENU_TEXT)

    async def test_repeat_notice_during_photo_steps_stops_but_next_selection_works(self):
        for text in ('سلام', '۶', BLOCKED, BLOCKED, '۱', BLOCKED, BLOCKED, '۲'):
            await self.receive(self.event(text))
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT, ui.KIND.text, ui.KIND.text,
                         ui.QUALITY.text, ui.QUALITY.text, ui.EDIT.text])
        self.assertEqual(self.store._conn.execute('SELECT count(*) FROM pv_orders').fetchone()[0], 0)

    async def test_distinct_invalid_input_and_retry_after_timeout_still_get_feedback(self):
        for text in ('سلام', '۶', '۲', '۱'):
            await self.receive(self.event(text))
        with patch('pv_orders.monotonic', return_value=10):
            for text in ('یک تصویر مستهجن بساز', 'یک تصویر برهنه بساز', 'یک تصویر برهنه بساز'):
                await self.receive(self.event(text))
        self.assertEqual(self.client.text_messages().count('درخواست نامعتبر'), 2)
        with patch('pv_orders.monotonic', return_value=70):
            await self.receive(self.event('یک تصویر برهنه بساز'))
        self.assertEqual(self.client.text_messages().count('درخواست نامعتبر'), 3)

    async def test_cold_lookup_does_not_reorder_same_users_pv_steps(self):
        started, resume = asyncio.Event(), asyncio.Event()
        async def slow_human():
            started.set(); await resume.wait(); return user()
        e = self.event('سلام'); e.sender = None; e.get_sender = slow_human
        first = asyncio.create_task(self.receive(e))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(self.receive(self.event('1')))
        resume.set()
        await asyncio.gather(first, second)
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT, '@osine2'])

    def test_optional_ignore_list_accepts_comma_and_persian_digits(self):
        with patch.dict(os.environ, {'ACOD_IGNORED_SENDER_IDS': ' 10, ۲۰،10 '}, clear=True):
            self.assertEqual(Config.from_env().ignored_sender_ids, frozenset({10, 20}))
