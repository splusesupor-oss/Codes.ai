"""Local text checks for PV photo orders; no image inspection or external AI call."""
from __future__ import annotations

import re
import unicodedata

QUALITIES = ('2K', '3K', '4K', '6K')

_DIGITS = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
_LETTERS = str.maketrans({'ي': 'ی', 'ى': 'ی', 'ك': 'ک', 'أ': 'ا', 'إ': 'ا',
                         'ة': 'ه', 'ۀ': 'ه', 'ـ': '', '\u200c': ' ', '\u200d': ''})


def option_number(text: str):
    """Exactly one digit, not a title, a decimal, a sign, or text containing a digit."""
    value = text.strip().translate(_DIGITS)
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


def allowed_text(text: str, *, require_description: bool = True) -> bool:
    """A short/non-description or explicit violation cannot consume quota.

    The reference says «در حد 500 خط», not «حداقل 500 خط». A local minimum of
    three different words rejects empty/trivial input without requiring padding.
    This is not a guarantee of detecting every semantic or legal violation.
    """
    value = normalized(text)
    if require_description:
        words = set(_WORD.findall(value))
        if len(words) < 3 or words.issubset(_GENERIC):
            return False
    return not (_PROHIBITED.search(_EXCLUSION.sub(' ', value)) or _AGE.search(_OBJECT_AGE.sub(' ', value)))


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
