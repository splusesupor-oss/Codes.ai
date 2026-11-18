"""Local text checks for PV photo orders; no image inspection or external AI call."""
from __future__ import annotations

import re
import unicodedata
from enum import Enum, auto

QUALITIES = ('2K', '3K', '4K', '6K')

_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_LETTERS = str.maketrans({'ي': 'ی', 'ى': 'ی', 'ك': 'ک', 'أ': 'ا', 'إ': 'ا',
                         'ة': 'ه', 'ۀ': 'ه', 'ـ': '', '\u200c': ' ', '\u200d': ''})


def option_number(text: str):
    """Exactly one digit, not a title, a decimal, a sign, or text containing a digit."""
    # Mobile RTL keyboards and copied options can wrap a digit in directional
    # marks or render it as a keycap. Never extract a digit from a sentence.
    value = ''.join(c for c in text if unicodedata.category(c) != 'Cf' and c not in '\ufe0e\ufe0f')
    value = value.strip().translate(_DIGITS).removesuffix('\u20e3')
    return int(value) if len(value) == 1 and value in '12345678' else None


def normalized(text):
    text = unicodedata.normalize('NFKC', text).casefold().translate(_DIGITS).translate(_LETTERS)
    text = ''.join(c for c in text if unicodedata.category(c) not in ('Mn', 'Cf'))
    return re.sub(r'\s+', ' ', text).strip()


# Reject explicit language/requests, not a mention of "law", "religion", or a
# particular identity. Unknown or ambiguous intent still needs management review.
_EXPLICIT = r'(?:برهن(?:ه|گی)|لخت|مستهجن|پورن(?:وگرافی)?|سکس(?:ی)?|هرزه\s*نگاری|عریان|(?:تصویر|تصاویر|عکس|صحنه|رابطه|محتوای) جنسی|porn(?:ographic|ography)?|nsfw|nud(?:e|ity)|naked|sexually explicit)'
_INSULT = r'(?:احمق|بی\s*شعور|کسخل|کثافت|حرام\s*زاده|فحش|ناسزا|توهین\s*آمیز|جاکش|idiot|asshole|fuck(?:ing)?)'
_ILLEGAL = (r'(?:ضد|خلاف|نقض)\s+(?:قانون|قوانین|شرع|اسلام)|غیر\s*قانونی|'
            r'توهین\s+به\s+(?:مقدسات|ادیان|اسلام)|جعل\s+(?:مدارک|مدرک|اسناد|گذرنامه)|'
            r'propaganda for terrorism|forge a passport')
_PROHIBITED = re.compile(fr'(?<!\w)(?:{_EXPLICIT}|{_INSULT}|{_ILLEGAL})(?!\w)')
# Common explicit exclusions in otherwise valid briefs ("بدون برهنگی", "no nudity").
_POLICY_TERM = fr'(?:{_EXPLICIT}|{_INSULT}|{_ILLEGAL})'
_EXCLUSION = re.compile(fr'(?<!\w)(?:(?:بدون|فاقد|نه|no|without)\s+(?:هیچ\s+)?{_POLICY_TERM}'
                        fr'|{_POLICY_TERM}\s+(?:نباشد|نباشه|نکش|نساز|نذار))(?!\w)')
_AGES = ('صفر', 'یک', 'دو', 'سه', 'چهار', 'پنج', 'شش', 'هفت', 'هشت', 'نه',
         'ده', 'یازده', 'دوازده', 'سیزده', 'چهارده')
_MINOR_NUMBER = r'(?:0?\d|1[0-4]|' + '|'.join(_AGES) + r')'
_AGE = re.compile(fr'(?<!\w){_MINOR_NUMBER}\s*(?:سال(?:ه|مه|م|گی| سن دارم| دارم| سن دارد| دارد)|years?\s+old|[- ]year[- ]old)(?!\w)|'
                  r'(?<!\w)(?:زیر\s+(?:14|15|چهارده|پانزده)\s*سال|نوزاد|خردسال|کودک(?:ان)?|(?:دختر|پسر)\s*بچه|طفل|toddler|infant|child(?:ren)?)(?!\w)|'
                  fr'\b(?:age[d]?\s+|i am\s+){_MINOR_NUMBER}\b')
# An explicitly inanimate subject with an age is not an age declaration about a person.
_OBJECT_AGE = re.compile(fr'(?<!\w)(?:درخت|ساختمان|ماشین|خودرو|قلعه|خانه)\s+{_MINOR_NUMBER}\s*ساله(?!\w)')
_WORD = re.compile(r'[^\W\d_]+', re.UNICODE)
_GENERIC = frozenset('یک یه لطفا لطفاً این از به من را رو برای عکس تصویر جدید ساخت بساز بکش ویرایش کن بده انجام'.split())


_HOME = {'منو', 'منوی اصلی', 'منو اصلی', 'شروع', 'شروع مجدد', 'از اول', 'بازگشت', 'برگشت',
         'بازگشت به منو', 'بازگشت به منوی اصلی', 'لغو سفارش', 'لغو', 'start', 'menu'}
# Strip only whole conversational openings, and only for the description check.
# A brief can start with «سلام»; the rest must qualify on its own. Its original
# spelling (including the greeting) is still used verbatim in the owner report.
_SOCIAL_PREFIX = re.compile(
    r'(?:سلام(?: علیکم| دوباره| مجدد)?|درود(?: بر شما)?|عرض ادب|دوست عزیز|خوبی(?:د|ن)?|'
    r'حال(?: شما|ت|تون)? چطوره|چه خبر|خسته نباشی(?:د|ن)?|خدا ?قوت|'
    r'(?:وقت|صبح|روز|عصر|شب)(?: ?تون| ?تان)? (?:بخیر|به خیر)|'
    r'(?:خیلی )?(?:مرسی|ممنون(?:م)?|سپاس(?: ?گزارم)?|متشکرم|تشکر)(?: از (?:شما|لطفتون))?|'
    r'ببخشید|hi(?: there)?|hello|thank you|thanks)(?= |$)(?: (?:و )?)?')
_GUIDANCE = {'راهنما', 'کمک', 'راهنمایی کنید', 'لطفا راهنمایی کنید', 'چطور سفارش بدم', 'از کجا شروع کنم',
             'چیکار کنم', 'چه کار کنم', 'چه کار باید بکنم', 'میشه کمکم کنی', 'میشه کمکم کنید',
             'یه سوال دارم', 'یک سوال دارم', 'من یه سوال دارم', 'باشه', 'بله', 'خیر', 'نه', 'اوکی', 'ok', 'help'}
_OPTION_QUERY = re.compile(r'(?:شماره|گزینه) [0-9]+(?: (?:کدام|کدوم) (?:است|بود))?')


def _plain_phrase(normalized_text: str) -> str:
    return re.sub(r'[^\w]+', ' ', normalized_text).strip()


def wants_main_menu(text: str) -> bool:
    # A stand-alone command (including /start), never a substring of a brief.
    return _plain_phrase(normalized(text)) in _HOME


class TextStatus(Enum):
    READY = auto()
    NOT_A_BRIEF = auto()
    INVALID = auto()


def check_text(text: str, *, require_description: bool = True) -> TextStatus:
    """Distinguish chat from an attempted brief that fails the displayed rules."""
    value = normalized(text)
    if _PROHIBITED.search(_EXCLUSION.sub(' ', value)) or _AGE.search(_OBJECT_AGE.sub(' ', value)):
        return TextStatus.INVALID
    if require_description:
        phrase = _plain_phrase(value)
        pos = 0
        while opening := _SOCIAL_PREFIX.match(phrase, pos):
            pos = opening.end()
        body = phrase[pos:]
        words = set(_WORD.findall(body))
        if not words or body in _GUIDANCE or body in _HOME or _OPTION_QUERY.fullmatch(body):
            return TextStatus.NOT_A_BRIEF
        if len(words) < 3 or words.issubset(_GENERIC):
            return TextStatus.INVALID
    return TextStatus.READY


def allowed_text(text: str, *, require_description: bool = True) -> bool:
    return check_text(text, require_description=require_description) is TextStatus.READY


def media_of(event):
    msg = getattr(event, 'message', None)
    return getattr(msg, 'media', None) or getattr(event, 'media', None)


def is_attached_photo(media) -> bool:
    from splusthon import types
    # The shortcut Message.photo can ALSO be a link-preview thumbnail. Never
    # use it, get_file(), file bytes, filenames, or event.raw_text as evidence.
    if isinstance(media, types.MessageMediaPhoto):
        return True   # even when the server omits photo/file_reference
    if not isinstance(media, types.MessageMediaDocument):
        return False
    doc = media.document
    if not isinstance(doc, types.Document):
        return False
    if (doc.mime_type or '').lower() not in {
        'image/jpeg', 'image/png', 'image/webp', 'image/avif', 'image/heic', 'image/heif', 'image/tiff', 'image/bmp'
    }:
        return False
    return not any(isinstance(a, (types.DocumentAttributeSticker, types.DocumentAttributeCustomEmoji,
                                  types.DocumentAttributeAnimated, types.DocumentAttributeVideo))
                   for a in (doc.attributes or ()))


def is_text_message(media):
    from splusthon import types
    return media is None or isinstance(media, (types.MessageMediaEmpty, types.MessageMediaWebPage))
