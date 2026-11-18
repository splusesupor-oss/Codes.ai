"""Offline end-to-end tests with real TL media/entities/requests and on-disk SQLite."""
import asyncio
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
import sqlite3
import tempfile
import unittest

from splusthon import errors, events, functions, types

import brand
from config import Config
from core import BotCore
import pv_messages as ui
from pv_rules import allowed_text, is_attached_photo, media_of
from pv_store import Input, Order, WINDOW
from storage import OwnerStore
from tests.fakes import FakeClient, FakeEvent

OWNER, REGISTERED, ALI, BAHAR = 101, 102, 201, 202
PROMPT = 'لطفاً آسمان عکس را روشن کن و رنگ برگ های درخت را سبز نگه دار.'
OK = '✅ : سفارش شما ثبت شد پس از تایید مدیریت براتون ارسال میکنیم'
INVALID = 'درخواست نامعتبر'
FWD = functions.messages.ForwardMessagesRequest
SEND = functions.messages.SendMessageRequest


def document(mime, attrs=()):
    return types.MessageMediaDocument(document=types.Document(
        id=10, access_hash=11, file_reference=b'', date=None, mime_type=mime,
        size=100, dc_id=2, attributes=list(attrs)))


def styled(card, cls):
    data = card.text.encode('utf-16-le')
    return [data[e.offset*2:(e.offset+e.length)*2].decode('utf-16-le')
            for e in card.entities() if isinstance(e, cls)]


class ReferenceAndRules(unittest.TestCase):
    def test_distinct_quotes_and_quality_weights(self):
        self.assertEqual(styled(ui.MENU, ui.Q), [brand.MENU_TITLE])
        self.assertEqual(styled(ui.KIND, ui.Q), ['1- ویرایش عکس', '2- ساخت عکس جدید'])
        self.assertEqual(ui.KIND.text.split('\n')[:2], ['🌁 : سفارش عکس 4 سفارش رایگان', 'انتخاب کنید'])
        self.assertEqual(styled(ui.QUALITY, ui.Q), [f'{i}ـ {q}' for i,q in enumerate(('2K','3K','4K','6K'), 1)])
        self.assertEqual(styled(ui.QUALITY, ui.B)[1:], ['2K','3K','4K','6K'])

    def test_permissions_are_plain_and_only_rule_heading_is_quoted(self):
        for card in (ui.EDIT, ui.CREATE):
            with self.subTest(card=card.text[:30]):
                self.assertEqual(styled(card, ui.Q), ['⚠️ : قوانین قبل نوشتن پرامپت خود'])
                self.assertTrue(styled(card, ui.B)[0].endswith('در حد 500 خط'))
                self.assertTrue(card.text.endswith('⛔ توضیحات دارای الفاظ توهین آمیز رد میشود'))
                self.assertEqual(card.text.count('⛔'), 6)
                self.assertNotIn('⛔', ''.join(styled(card, ui.B)))
        self.assertEqual(styled(ui.PHOTO, ui.B), ['🔍📸 عکس خودتون رو ارسال کنید'])

    def test_video_reference_urls_and_test_chat_exact(self):
        self.assertEqual(styled(ui.VIDEO, ui.Q), ['🎥 کانال ویدیو : https://splus.ir/vidofox'])
        self.assertEqual(styled(ui.VIDEO, ui.URL), ['https://splus.ir/vidofox'])
        self.assertEqual(styled(ui.VIDEO, ui.MENTION), ['@osine2'])
        self.assertEqual(ui.CHAT_TEST.text, 'برای تست وارد گروه زیر شوید\n\nhttps://splus.ir/joingroup/AI_hfuzaN9GGKPWF0MsDJg')
        self.assertEqual(ui.SUCCESS.text, OK)

    def test_text_checks_apply_regardless_of_script_digits_and_half_spaces(self):
        for bad in ('  ', 'تصویر', 'یک عکس بساز', '۱ ۲ ۳', 'گل گل گل', 'یک تصویر مستهجن درست کن',
                    'روی عکس بنویس بی\u200cشعور', 'یک تصویر ضد قوانین اسلامی بکش',
                    'این شخص ١٤ ساله باشد', 'برای پسر سیزده ساله پرتره بساز',
                    'در پس زمینه تبلیغ جعل مدارک بگذار', 'make this portrait naked'):
            with self.subTest(bad=bad): self.assertFalse(allowed_text(bad))
        for good in (PROMPT, 'پرتره یک فرد ۱۵ ساله را در باغ بکش',
                     'یک تصویر بدون برهنگی در طبیعت بساز', 'عکس مستهجن نباشد و پس زمینه آبی باشد',
                     'درخت چهارده ساله را روشن کن', 'نور استودیو را با ۱۴ لامپ گرم نشان بده'):
            with self.subTest(good=good): self.assertTrue(allowed_text(good))

    def test_native_media_marker_without_payload_and_static_document(self):
        class Event:
            message = types.Message(id=7, peer_id=types.PeerUser(ALI), media=types.MessageMediaPhoto())
        self.assertIsNone(Event.message.media.photo)
        self.assertTrue(is_attached_photo(media_of(Event())))
        self.assertTrue(is_attached_photo(document('image/jpeg')))
        self.assertTrue(is_attached_photo(document('image/png')))

    def test_sticker_preview_gif_video_and_arbitrary_files_are_not_photos(self):
        sticker = types.DocumentAttributeSticker(alt='🐈', stickerset=types.InputStickerSetEmpty())
        for media in (None, types.MessageMediaEmpty(), types.MessageMediaUnsupported(),
                      types.MessageMediaWebPage(webpage=types.WebPageEmpty(5)), document('application/pdf'),
                      document('video/mp4'), document('image/webp', [sticker]),
                      document('image/gif'), document('image/png', [types.DocumentAttributeAnimated()])):
            with self.subTest(media=type(media).__name__): self.assertFalse(is_attached_photo(media))


class Orders(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'owner.sqlite3'
        self.store = OwnerStore(self.db)
        self.store.claim(OWNER, chat_id=-11)
        self.store.set_registered_bot_owner(REGISTERED, registered_by=OWNER)
        self.cfg = Config()
        self.client = FakeClient()
        self.now = 1_800_000_000
        self.ids = count(1)
        self._core()

    def _core(self):
        self.core = BotCore(self.cfg, self.store, now_provider=lambda tz=None: datetime.fromtimestamp(self.now, timezone.utc))

    def restart(self):
        self.store.close()
        self.store = OwnerStore(self.db)
        self.client = FakeClient()
        self._core()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def rows(self, sql, params=()):
        with self.store._lock:
            return [dict(r) for r in self.store._conn.execute(sql, params)]

    def orders(self):
        return self.rows('SELECT * FROM pv_orders ORDER BY id')

    def queue(self):
        return self.rows('SELECT * FROM pv_order_outbox ORDER BY order_id')

    async def pv(self, text='', *, user=ALI, msg_id=None, media=None, username='ali', name='علی'):
        event = FakeEvent(text, user_id=user, chat_id=user, msg_id=msg_id or next(self.ids),
                          media=media, is_group=False, username=username, display_name=name)
        await self.core.on_new_message(self.client, event)
        return event.id

    async def begin(self, *, edit=False, user=ALI, quality='3'):
        for text in ('سلام', '۶', '۱' if edit else '2', quality):
            await self.pv(text, user=user)

    async def complete(self, *, edit=False, user=ALI):
        await self.begin(edit=edit, user=user)
        mid = await self.pv(PROMPT, user=user)
        if edit: mid = await self.pv(media=types.MessageMediaPhoto(), user=user)
        return mid

    def last(self):
        return self.client.text_messages()[-1]

    async def test_create_happy_path_report_identity_recipient_and_reset(self):
        await self.complete()
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT, ui.KIND.text, ui.QUALITY.text, ui.CREATE.text, OK])
        [o] = self.orders()
        self.assertEqual((o['user_id'], o['username'], o['display_name'], o['owner_id']), (ALI,'ali','علی',OWNER))
        self.assertEqual((o['kind'],o['quality'],o['prompt'],o['photo_msg_id']), ('create','4K',PROMPT,None))
        await self.core.pv.deliver_due(self.client)
        req = self.client.last_request()
        self.assertEqual(req.peer.user_id, OWNER)
        self.assertNotEqual(req.peer.user_id, REGISTERED)
        self.assertEqual(req.message, f'سفارش شماره #1\n⚙️ نوع سفارش : ساخت عکس جدید\n⚙️ یوزنیم کاربر : @ali\n⚙️ اسم نمایشی کاربر : علی\n\n🔍 متن نوشته شده توسط کاربر\n{PROMPT}\n\n⚙️ شناسه کاربر : {ALI}\n⚙️ کیفیت تصویر : 4K')
        self.assertEqual(self.queue()[0]['phase'], 'sent')
        await self.pv('۶')
        self.assertEqual(self.last(), brand.MENU_TEXT)
        await self.pv('۶')
        self.assertEqual(self.last(), ui.KIND.text)
        self.assertEqual(len(self.orders()), 1)

    async def test_edit_requires_photo_marker_no_bytes_and_forwards_exact_source(self):
        msg_id = await self.complete(edit=True)
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT,ui.KIND.text,ui.QUALITY.text,ui.EDIT.text,ui.PHOTO.text,OK])
        self.assertEqual(self.orders()[0]['photo_msg_id'], msg_id)
        await self.core.pv.deliver_due(self.client)
        self.assertEqual(self.client.requests[-2].peer.user_id, OWNER)
        req = self.client.last_request()
        self.assertIsInstance(req, FWD)
        self.assertEqual((req.from_peer.user_id, req.to_peer.user_id, req.id), (ALI,OWNER,[msg_id]))
        self.assertEqual(self.queue()[0]['phase'], 'sent')

    async def test_real_newmessage_event_without_photo_bytes_and_wire_serialization(self):
        await self.begin(edit=True)
        await self.pv(PROMPT)
        peer = types.PeerUser(ALI)
        raw = types.Message(id=next(self.ids), peer_id=peer, from_id=peer, out=False,
                            message='', media=types.MessageMediaPhoto())
        await self.core.on_new_message(self.client, events.NewMessage.Event(raw))
        self.assertEqual(self.last(), OK)
        await self.core.pv.deliver_due(self.client)
        self.assertTrue(all(bytes(req) for req in self.client.requests))
        self.assertIsInstance(self.client.last_request(), FWD)

    async def test_invalid_steps_do_not_advance_and_sticker_does_not_consume_quota(self):
        for value in ('hi','6','0','1','11','4','کوتاه','یک تصویر برهنه بساز',PROMPT):
            await self.pv(value)
        self.assertEqual(self.client.text_messages().count(INVALID), 4)
        self.assertEqual(self.last(), ui.PHOTO.text)
        for media in (None, document('application/pdf'), types.MessageMediaWebPage(webpage=types.WebPageEmpty(1))):
            await self.pv('عکس.jpg', media=media)
            self.assertEqual(self.last(), INVALID)
        await self.pv('یک عکس مستهجن', media=types.MessageMediaPhoto())
        self.assertEqual(self.last(), INVALID)
        self.assertEqual(self.orders(), [])
        await self.pv(media=document('image/jpeg'))
        self.assertEqual(self.last(), OK)
        self.assertEqual(len(self.orders()), 1)

    async def test_video_is_only_a_handoff_even_if_repeated(self):
        for _ in range(8):
            await self.pv('سلام')
            await self.pv('٧')
            self.assertEqual(self.last(), ui.VIDEO.text)
        self.assertEqual(self.orders(), [])
        self.assertEqual(self.queue(), [])
        self.assertTrue(all(r.peer.user_id == ALI for r in self.client.requests))
        await self.pv('7')
        self.assertEqual(self.last(), brand.MENU_TEXT)

    async def test_all_main_choices_including_paid_video_and_exact_chat_test(self):
        expected = ['@osine2','@ai_fox','@Aifox_bot','https://foxbot.osine2.workers.dev/',
                    'ربات های روباه\n\nنسخه یک🔹 @fox_bot\nنسخه دو🔹 @aifox\nنسخه سه🔹 @bot_fox',
                    ui.KIND.text, ui.VIDEO.text, ui.CHAT_TEST.text]
        for n, text in enumerate(expected, 1):
            await self.pv('hi', user=300+n)
            await self.pv(str(n), user=300+n)
            self.assertEqual(self.last(), text)
            self.assertEqual(self.client.last_request().peer.user_id, 300+n)

    async def test_all_quality_choices_are_stored_as_selected(self):
        for user, (key,quality) in enumerate(zip('۱۲٣4', ('2K','3K','4K','6K')), 400):
            await self.begin(user=user, quality=key)
            await self.pv(PROMPT, user=user)
            self.assertEqual(self.orders()[-1]['quality'], quality)

    async def test_shared_rolling_quota_exact_boundary_and_restart(self):
        start = self.now
        for i in range(4):
            self.now = start+i*3600
            await self.complete(edit=bool(i%2))
        self.restart()
        self.now = start + WINDOW - 1
        await self.pv('۶')    # next interaction only shows menu
        await self.pv('۶')
        self.assertEqual(self.last(), INVALID)
        await self.pv('۸')
        self.assertEqual(self.last(), ui.CHAT_TEST.text)
        await self.pv('۷')
        self.assertEqual(self.last(), ui.VIDEO.text)
        await self.complete(user=BAHAR)
        self.assertEqual(len(self.orders()), 5)
        self.now = start + WINDOW
        await self.complete()
        self.assertEqual(self.last(), OK)
        self.assertEqual(len(self.orders()), 6)
        await self.pv('منو'); await self.pv('6')
        self.assertEqual(self.last(), INVALID)  # only the oldest slot was released

    async def test_interleaved_users_resume_distinct_drafts_after_restart(self):
        await asyncio.gather(self.begin(edit=True, user=ALI, quality='۱'), self.begin(user=BAHAR, quality='۴'))
        await self.pv('شاخه گل را آبی کن', user=ALI)
        self.restart()
        await asyncio.gather(self.pv('رنگ کوه را سبز کن', user=BAHAR), self.pv(media=types.MessageMediaPhoto(), user=ALI))
        by_user = {o['user_id']:o for o in self.orders()}
        self.assertEqual((by_user[ALI]['quality'],by_user[ALI]['prompt']), ('2K','شاخه گل را آبی کن'))
        self.assertEqual((by_user[BAHAR]['quality'],by_user[BAHAR]['prompt']), ('6K','رنگ کوه را سبز کن'))
        self.restart()
        await self.pv('2', user=BAHAR)
        self.assertEqual(self.last(), brand.MENU_TEXT)
        self.assertEqual(len(self.queue()), 2)

    async def test_replayed_completion_and_earlier_media_never_create_another_order(self):
        await self.begin(edit=True)
        await self.pv(PROMPT)
        mid = next(self.ids)
        await asyncio.gather(*(self.pv(msg_id=mid, media=types.MessageMediaPhoto()) for _ in range(8)))
        self.assertEqual(self.client.text_messages().count(OK), 1)
        self.restart()
        await self.pv(msg_id=mid, media=types.MessageMediaPhoto())
        self.assertEqual(self.client.requests, [])
        await self.pv('2')
        self.assertEqual(self.last(), brand.MENU_TEXT)
        await self.pv(msg_id=mid-1, media=types.MessageMediaPhoto())
        self.assertEqual(len(self.orders()), 1)
        self.assertEqual(len(self.client.requests), 1)

    async def test_user_notification_recovers_after_send_error(self):
        self.client.reject_all = True
        await self.pv('hi')
        self.client.reject_all = False
        await self.pv('6')
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT])  # missed menu delivered first
        await self.pv('6')
        self.assertEqual(self.last(), ui.KIND.text)

    async def test_missing_username_is_not_fabricated(self):
        for text in ('hi','6','2','1',PROMPT):
            await self.pv(text, username=None, name='')
        await self.core.pv.deliver_due(self.client)
        self.assertIn('⚙️ یوزنیم کاربر : —', self.last())
        self.assertIn(str(ALI), self.last())
        self.assertNotIn('@', self.last())

    async def test_recipient_resolution_failure_survives_restart_without_new_pv(self):
        await self.complete()
        original = self.client.get_input_entity
        async def no_owner(peer):
            if peer == OWNER: raise ValueError('peer not cached')
            return await original(peer)
        self.client.get_input_entity = no_owner
        await self.core.pv.deliver_due(self.client)
        self.assertEqual(self.queue()[0]['phase'], 'report')
        self.assertGreater(self.queue()[0]['retry_at'], self.now)
        self.restart(); self.now += 10
        await self.core.pv.deliver_due(self.client)
        self.assertEqual(self.client.last_request().peer.user_id, OWNER)
        self.assertEqual(len(self.orders()), 1)
        self.assertEqual(await self.core.pv.deliver_due(self.client), 0)

    async def test_reassigned_username_cannot_redirect_a_private_order(self):
        await self.complete()
        with self.store._conn:
            self.store._conn.execute('UPDATE global_owner SET username=?', ('old_owner_handle',))
        async def moved_handle(key):
            if key == OWNER: raise ValueError('owner is not in cache')
            self.assertEqual(key, '@old_owner_handle')
            return types.InputPeerUser(REGISTERED, 0)
        self.client.get_input_entity = moved_handle
        self.client.clear_requests()
        await self.core.pv.deliver_due(self.client)
        self.assertEqual(self.client.requests, [])
        self.assertEqual(self.queue()[0]['phase'], 'report')

    async def test_protected_forward_does_not_erase_or_repeat_owner_report(self):
        await self.complete(edit=True)
        self.client._side_effect = lambda r: errors.ChatForwardsRestrictedError(r) if isinstance(r, FWD) else None
        await self.core.pv.deliver_due(self.client)
        self.assertEqual(self.queue()[0]['phase'], 'photo_unavailable')
        self.assertEqual(self.queue()[0]['last_error'], 'ChatForwardsRestrictedError')
        self.assertEqual(len(self.orders()), 1)
        self.assertEqual(sum(getattr(r, 'peer',None) == types.InputPeerUser(OWNER,0) for r in self.client.requests), 1)
        self.assertEqual(await self.core.pv.deliver_due(self.client), 0)

    async def test_delayed_or_duplicate_report_uses_persisted_random_id(self):
        await self.complete()
        attempts = []
        def offline(req):
            attempts.append(req.random_id)
            return ConnectionError('disconnect after submission')
        self.client._side_effect = offline
        await self.core.pv.deliver_due(self.client)
        self.restart(); self.now += 30
        def duplicate(req):
            attempts.append(req.random_id)
            return errors.RandomIdDuplicateError(req)
        self.client._side_effect = duplicate
        await self.core.pv.deliver_due(self.client)
        self.assertEqual(self.queue()[0]['phase'], 'sent')
        self.assertEqual(len(set(attempts)), 1)
        self.assertEqual(len(self.orders()), 1)

    async def test_large_report_utf16_chunk_cursor_resumes_without_text_loss(self):
        self.cfg = replace(self.cfg, max_message_chars=160)
        self._core()
        await self.begin()
        prompt = ('🦊 آسمان شب روشن باشد 🌙\n' * 500).rstrip('\n')
        await self.pv(prompt)
        self.client.clear_requests()
        await self.core.pv.deliver_due(self.client, batch=2)
        first = self.client.text_messages()
        self.assertEqual(self.queue()[0]['part'], 2)
        self.restart()
        while await self.core.pv.deliver_due(self.client): pass
        chunks = first + self.client.text_messages()
        self.assertTrue(all(brand.utf16_len(c) <= 160 for c in chunks))
        self.assertEqual(''.join(chunks), ui.owner_report(Order(**self.orders()[0])).text)
        self.assertEqual(self.queue()[0]['phase'], 'sent')

    async def test_worker_at_startup_wakes_without_user_activity(self):
        await self.complete(); self.restart()
        sent = asyncio.Event()
        def mark(req):
            if isinstance(req, SEND) and req.peer.user_id == OWNER: sent.set()
        self.client._side_effect = mark
        worker = asyncio.create_task(self.core.pv.run_outbox(self.client))
        try:
            await asyncio.wait_for(sent.wait(), timeout=3)
        finally:
            worker.cancel(); await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(self.client.last_request().peer.user_id, OWNER)

    async def test_db_rollback_couples_order_quota_queue_and_state(self):
        await self.begin()
        with self.store._conn:
            self.store._conn.execute("CREATE TRIGGER fail_enqueue BEFORE INSERT ON pv_order_outbox BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): await self.pv(PROMPT)
        self.assertEqual(self.orders(), [])
        self.assertEqual(self.queue(), [])
        self.assertNotIn(OK, self.client.text_messages())
        self.assertEqual(self.rows('SELECT step FROM pv_dialogs')[0]['step'], 'prompt')
        self.store._conn.execute('DROP TRIGGER fail_enqueue')
        await self.pv(PROMPT)
        self.assertEqual(self.last(), OK)

    async def test_final_commit_rechecks_quota_not_just_the_entry_menu(self):
        for _ in range(3): await self.complete()
        await self.begin()
        # Simulate a fourth historical completion arriving from another connection.
        with closing(sqlite3.connect(self.db)) as other, other:
            other.execute('''INSERT INTO pv_orders(user_id,owner_id,kind,quality,prompt,created_at,source_chat_id,final_msg_id)
                          VALUES (?,?,'create','2K',?,?,?,?)''', (ALI,OWNER,PROMPT,self.now,ALI,99999))
        await self.pv(PROMPT)
        self.assertEqual(self.last(), INVALID)
        self.assertEqual(len(self.orders()), 4)
        await self.pv('7')
        self.assertEqual(self.last(), ui.VIDEO.text)

    async def test_two_db_connections_cannot_process_same_completion_or_claim_same_job(self):
        await self.begin()
        other = OwnerStore(self.db)
        try:
            item = Input(ALI,ALI,next(self.ids),PROMPT,False,True,'ali','علی')
            results = await asyncio.gather(*(asyncio.to_thread(s.pv_consume, item,self.now,3500)
                                             for s in (self.store,other)))
            self.assertEqual(sum(r.queued for r in results), 1)
            jobs = await asyncio.gather(*(asyncio.to_thread(s.pv_take_job,self.now) for s in (self.store,other)))
            self.assertEqual(sum(j is not None for j in jobs), 1)
            # Simulated crash after taking lease: another worker can resume after expiry.
            self.now += 121
            await self.core.pv.deliver_due(self.client)
            self.assertEqual(self.queue()[0]['phase'], 'sent')
        finally: other.close()

    async def test_existing_users_and_ownership_preserved_on_schema_upgrade(self):
        self.store.register_pv_user(ALI, username='old_name')
        self.store.close()
        with closing(sqlite3.connect(self.db)) as legacy, legacy:
            for name in ('pv_order_outbox','pv_orders','pv_dialogs'):
                legacy.execute(f'DROP TABLE {name}')
        self.store = OwnerStore(self.db); self._core()
        await self.pv('6', username='new_name')
        self.assertEqual(self.client.text_messages(), [brand.MENU_TEXT])
        self.assertEqual(self.store.list_pv_users()[0].username, 'old_name')
        self.assertEqual(self.store.get_owner().user_id, OWNER)
        self.assertEqual(self.store.get_registered_bot_owner().user_id, REGISTERED)
        self.assertEqual(self.store.count_pv_users(), 1)

    async def test_photo_count_does_not_use_group_ai_daily_quota(self):
        self.store.ai_set_enabled(-11, True)
        await self.complete()
        self.assertEqual(self.rows('SELECT * FROM ai_usage'), [])
        self.assertTrue(self.store.ai_is_enabled(-11))

    async def test_fallback_retains_literal_text(self):
        self.client.reject_blockquote = True
        await self.pv('hi')
        self.assertEqual(self.last(), brand.MENU_TEXT)
        self.assertTrue(all(isinstance(e, ui.B) for e in self.client.last_request().entities))
