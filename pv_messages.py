"""متن‌های مرجع پیوی. نوار نام مدیر، ساعت و رنگ حباب، جزو متن پیام نیستند."""
from __future__ import annotations

from dataclasses import dataclass

from splusthon import types

import brand
from pv_rules import QUALITIES


@dataclass(frozen=True)
class Card:
    text: str
    # Python character spans until the MTProto boundary (never slice a surrogate).
    spans: tuple = ()

    def entities(self):
        return [cls(offset=brand.utf16_offset(self.text, start),
                    length=brand.utf16_len(self.text[start:end]))
                for cls, start, end in sorted(self.spans, key=lambda span: span[1])]

    def parts(self, unit_limit: int):
        """Split only for transport; concatenation reproduces the exact report."""
        limit = max(2, min(unit_limit, 4096))
        start = size = 0
        for i, char in enumerate(self.text):
            units = 1 + (ord(char) > 0xffff)
            if size + units > limit:
                yield self._slice(start, i)
                start, size = i, 0
            size += units
        if start < len(self.text):
            yield self._slice(start, len(self.text))

    def _slice(self, a, b):
        return Card(self.text[a:b], tuple((cls, max(s, a)-a, min(e, b)-a)
                    for cls, s, e in self.spans if s < b and a < e))


B = types.MessageEntityBold
Q = types.MessageEntityBlockquote
URL = types.MessageEntityUrl
MENTION = types.MessageEntityMention


def compose(*segments):
    """Parts are literal strings or (string, entity class, ...). No markup parsing."""
    pieces, spans, pos = [], [], 0
    for segment in segments:
        text, *styles = (segment,) if isinstance(segment, str) else segment
        if text:
            spans.extend((style, pos, pos + len(text)) for style in styles)
            pieces.append(text)
            pos += len(text)
    return Card(''.join(pieces), tuple(spans))


def _menu_card():
    sections = [(brand.MENU_TITLE, Q, B), '\n\n\n']
    for line in brand.MENU_LINES[:-1]:
        sections.extend([(line, B), '\n'])
    # In the reference the last numeral is regular weight.
    sections.extend(['8_ ', (brand.MENU_OPTIONS[-1], B)])
    return compose(*sections)


MENU = _menu_card()
KIND = compose(('🌁 : سفارش عکس 4 سفارش رایگان\nانتخاب کنید', B), '\n',
               ('1- ویرایش عکس', Q), '\n', ('2- ساخت عکس جدید', Q))


def _quality_card():
    sections = [('📸 : وضوح و کیفیت تصویر را انتخاب کنید', B)]
    for i, quality in enumerate(QUALITIES, 1):
        sections += ['\n', (f'{i}ـ {quality}', Q)]
    card = compose(*sections)
    spans = tuple((B, card.text.index(q), card.text.index(q) + len(q)) for q in QUALITIES)
    return Card(card.text, card.spans + spans)


QUALITY = _quality_card()
RULE_HEADING = '⚠️ : قوانین قبل نوشتن پرامپت خود'
RULES = '\n'.join((
    '⛔ توضیحات ضد قوانین رد میشود',
    '⛔ توضیحات کوتاه رد میشود',
    '⛔ توضیحات مستهجن رد میشود',
    '⛔ ویرایش و ساخت عکس بالای 14 سال',
    '⛔ رد شدن عکس های ضد قوانین اسلامی',
    '⛔ توضیحات دارای الفاظ توهین آمیز رد میشود',
))


def prompt_card(first_lines):
    return compose((first_lines + '\nدر حد 500 خط', B), '\n\n', (RULE_HEADING, Q, B),
                   '\n\n', RULES)


EDIT = prompt_card('یک پرامپت و توضیح در مورد ویرایش تصویر خودتون\nبنویسید چه تغییری انجام بشه')
CREATE = prompt_card('یک پرامپت و توضیح در مورد ساخت خودتون بنویسید\nچه تغییری انجام بشه')
PHOTO = compose(('🔍📸 عکس خودتون رو ارسال کنید', B))
VIDEO_URL = 'https://splus.ir/vidofox'
VIDEO_CHANNEL = '🎥 کانال ویدیو : ' + VIDEO_URL
_VIDEO = compose(('سفارش ساخت ویدیو\nقبل از سفارش میتوانید در کانال زیر نمونه ویدیو ساخته\nشده توسط روباه رو ببنید', B),
                 '\n\n', (VIDEO_CHANNEL, Q), '\n\n',
                 ('و چون این مدل پولی هست برای سفارش به\nآیدی زیر پیام ارسال کنید', B),
                 '\n🏕️ به ایشون ', ('@osine2', MENTION))
_start = _VIDEO.text.index(VIDEO_URL)
VIDEO = Card(_VIDEO.text, _VIDEO.spans + ((URL, _start, _start + len(VIDEO_URL)),))
CHAT_TEST = compose('برای تست وارد گروه زیر شوید\n\n',
                    ('https://splus.ir/joingroup/AI_hfuzaN9GGKPWF0MsDJg', URL))
SUCCESS = compose(('✅ : سفارش شما ثبت شد پس از تایید مدیریت براتون ارسال میکنیم', B))
INVALID = Card('درخواست نامعتبر')
# The video guide alone completes the handoff; do not acknowledge a fictitious order.
CARDS = dict(menu=MENU, kind=KIND, quality=QUALITY, edit=EDIT, create=CREATE,
             photo=PHOTO, video=VIDEO, chat_test=CHAT_TEST, success=SUCCESS, invalid=INVALID)


def owner_report(order):
    # User-supplied values remain literal (no Markdown/HTML/entity inference).
    name = 'ویرایش تصویر' if order.kind == 'edit' else 'ساخت عکس جدید'
    username = (order.username or '').lstrip('@')
    return compose((f'سفارش شماره #{order.id}', Q), '\n⚙️ نوع سفارش : ', (name, B),
                   '\n⚙️ یوزنیم کاربر : ', (f'@{username}', MENTION) if username else '—',
                   '\n⚙️ اسم نمایشی کاربر : ', order.display_name or '—',
                   '\n\n🔍 متن نوشته شده توسط کاربر\n', order.prompt,
                   f'\n\n⚙️ شناسه کاربر : {order.user_id}\n⚙️ کیفیت تصویر : {order.quality}')
