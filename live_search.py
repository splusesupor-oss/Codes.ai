"""
موتور دانش زنده و جستجوی وب تا سال ۲۰۲۶ میلادی / ۱۴۰۵ خورشیدی (`live_search.py`).

هدف این ماژول برطرف کردن کامل محدودیت تاریخ دانش پایه (Knowledge Cutoff) در مدل‌های
Cloudflare Workers AI است؛ بدون نیاز به هیچ کلید API پولی یا کتابخانه‌ی بیرونی جدید
(فقط با کتابخانه‌ی استاندارد پایتون + `aiohttp` موجود در پروژه و کاملاً سازگار با Termux).

منابع زنده‌ی موازی (همگی سریع و قابل‌دسترس):
  ۱) Bing News RSS (فارسی و جهانی — بدون فیلتر در ایران، پوشش اخبار روز ۱۴۰۵ / ۲۰۲۶)
  ۲) ویکی‌پدیای فارسی (`fa.wikipedia.org` — مقالات و رخدادهای به‌روز تا ۲۰۲۶ / ۱۴۰۵)
  ۳) ویکی‌پدیای انگلیسی (`en.wikipedia.org` — پوشش کامل فناوری، نسخه‌ها، علم و جهان تا ۲۰۲۶)
  ۴) Google News RSS (`news.google.com` — تیترهای خبری لحظه‌ای سال ۲۰۲۶)
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import html
import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

from brand import gregorian_to_jalali

log = logging.getLogger("acod.live_search")

TEHRAN_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30), name="Asia/Tehran")

PERSIAN_WEEKDAYS = (
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنجشنبه",
    "جمعه",
    "شنبه",
    "یکشنبه",
)

PERSIAN_MONTHS = (
    "",
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r"\s+")

_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Termux) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)

# عبارات کوتاه محاوره‌ای که نیازی به جستجوی وب ندارند (برای حفظ سرعت آنی چت روزمره)
_CHITCHAT_ONLY = {
    "سلام", "درود", "خوبی", "چطوری", "چه خبر", "مرسی", "ممنون", "تشکر",
    "دمت گرم", "قربانت", "فدات", "خداحافظ", "فعلا", "شب بخیر", "صبح بخیر",
    "عصر بخیر", "باشه", "اوکی", "حله", "اره", "آره", "نه", "خیر", "جانم",
}

# کلیدواژه‌هایی که صراحتاً نیازمند دانش روز و سال جاری (۲۰۲۵/۲۰۲۶ - ۱۴۰۴/۱۴۰۵) هستند
_RECENCY_KEYWORDS = (
    "امروز", "امسال", "الان", "اکنون", "فعلی", "جدید", "جدیدترین", "آخرین",
    "اخیر", "بروز", "به‌روز", "آپدیت", "خبر", "اخبار", "قیمت", "نرخ",
    "رئیس جمهور", "رئیس‌جمهور", "وزیر", "قهرمان", "برنده", "جام جهانی",
    "نسخه", "ورژن", "مدل جدید", "سال چند", "چه سالی", "تاریخ امروز",
    "2024", "2025", "2026", "۲۰۲۴", "۲۰۲۵", "۲۰۲۶",
    "1403", "1404", "1405", "۱۴۰۳", "۱۴۰۴", "۱۴۰۵",
)

# سوالات فقط‌متنی/تبدیل فونت/ترجمه/کدنویسی خالص که نیازی به جستجوی وب ندارند
_PURE_TASK_PATTERNS = (
    "فونت ", "به انگلیسی ترجمه", "به فارسی ترجمه", "ترجمه کن",
    "پرامپت عکس", "پرامپت تصویر", "این کد رو", "باگ این کد",
    "این کاربر", "پیام ریپلای",
)


@dataclass(frozen=True)
class SearchSnippet:
    """یک نتیجه‌ی تمیز و خلاصه‌شده از وب زنده."""

    source: str
    title: str
    snippet: str
    published: str = ""


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    no_tags = _HTML_TAG_RE.sub(" ", unescaped)
    return _MULTI_SPACE_RE.sub(" ", no_tags).strip()


def build_realtime_date_context(now_dt: Optional[_dt.datetime] = None) -> str:
    """ساخت بلوک دقیق تاریخ و ساعت فعلی (شمسی + میلادی) برای تثبیت زمان در سال ۲۰۲۶ / ۱۴۰۵."""
    dt = now_dt or _dt.datetime.now(TEHRAN_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc).astimezone(TEHRAN_TZ)
    else:
        dt = dt.astimezone(TEHRAN_TZ)

    jy, jm, jd = gregorian_to_jalali(dt.year, dt.month, dt.day)
    weekday_fa = PERSIAN_WEEKDAYS[dt.weekday()]
    month_fa = PERSIAN_MONTHS[jm]
    greg_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%H:%M")

    return (
        "📅 اطلاعات زمانی زنده و قطعی سیستم:\n"
        f"• تاریخ امروز به تقویم خورشیدی (ایران): {weekday_fa} {jd} {month_fa} {jy} ({jy}/{jm:02d}/{jd:02d})\n"
        f"• تاریخ امروز به تقویم میلادی: {greg_str} (سال میلادی جاری: {dt.year})\n"
        f"• ساعت فعلی تهران: {time_str}\n"
        "• دستور قطعی زمانی: دانش و دسترسی اطلاعاتی تو تا همین امروز در سال ۲۰۲۶ میلادی (۱۴۰۵ خورشیدی) "
        "به‌روز است. اکیداً هرگز نگو «اطلاعات من تا سال ۲۰۲۱ یا ۲۰۲۳ محدود است»."
    )


def should_search_web(user_text: str) -> bool:
    """تشخیص هوشمند نیاز پیام به جستجوی وب زنده (حذف چت‌های روزمره و دستورات متنی ساده)."""
    clean = _MULTI_SPACE_RE.sub(" ", (user_text or "").strip())
    if len(clean) < 4:
        return False

    low = clean.lower().rstrip("؟?!.،,")
    if low in _CHITCHAT_ONLY:
        return False

    # اگر صراحتاً درباره‌ی زمان، اخبار، رویدادهای اخیر، سال جاری یا آخرین وضعیت پرسیده شده
    if any(kw in low for kw in _RECENCY_KEYWORDS):
        # اگر فقط پرسیده «امروز چندمه / ساعت چنده / چه سالیه» همان بلوک زمان سیستم کافی است
        only_clock = re.sub(
            r"(امروز|الان|ساعت|چنده|چندمه|چه\s*سالیه|تاریخ|میلادی|شمسی|چند\s*شنبه|است|هست|\s|[؟?!])",
            "",
            low,
        )
        if not only_clock:
            return False
        return True

    if any(p in low for p in _PURE_TASK_PATTERNS):
        return False
    if "```" in clean:
        return False

    # پرسش‌های اطلاعاتی و دانشی («کیست»، «چیست»، «کجاست»، «چند»، علامت سؤال و...)
    question_markers = (
        "؟", "?", "کیه", "کیست", "چیه", "چیست", "کجاست", "کجا", "چه کسی",
        "کی شد", "کی هست", "چند تا", "چقدر", "درباره ", "در مورد ", "معرفی ",
    )
    if any(qm in low for qm in question_markers) and len(clean.split()) >= 2:
        return True

    return False


def clean_search_query(user_text: str) -> str:
    """استخراج عبارت کلیدی خالص برای موتورهای جستجو (حذف حشوهای محاوره‌ای و ادات پرسش فارسی)."""
    q = _MULTI_SPACE_RE.sub(" ", (user_text or "").strip())
    q = re.sub(
        r"^(?:(?:لطفاً?|لطفا|میشه|می‌شه|میتونی|می‌تونی|بگی|بگو|بهم\s*بگو|به\s*من\s*بگو|"
        r"سرچ\s*کن|جستجو\s*کن|ببین|تحقیق\s*کن|درباره|در\s*مورد|به\s*نظرت)\s+)+",
        "",
        q,
        flags=re.IGNORECASE,
    )
    # حذف قیدهای محاوره‌ای که تیتر خبرگزاری‌ها و ویکی‌پدیا را محدود می‌کنند
    q = re.sub(
        r"\b(?:در\s*حال\s*حاضر|همین\s*الان|الان|فعلی|کنونی|میشه|می‌شه|برگزار\s*میشه|"
        r"برگزار\s*می‌شود|کیست|کیه|چیست|چیه|کجاست|کجا|چنده|چند\s*است|چه\s*کسی\s*است|"
        r"چه\s*کسی|هستش|هست|است)\b",
        " ",
        q,
        flags=re.IGNORECASE,
    )
    q = _MULTI_SPACE_RE.sub(" ", q).strip(" ؟?!.،,")
    return q[:140] if q else user_text.strip(" ؟?!.،,")[:140]


class LiveWebSearcher:
    """جستجوگر موازی، بدون کلید و کش‌دار برای تغذیه‌ی لحظه‌ای مدل Cloudflare تا سال ۲۰۲۶."""

    def __init__(
        self,
        *,
        timeout: float = 3.8,
        cache_ttl: float = 1200.0,
        max_cache_entries: int = 128,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.timeout = float(timeout)
        self.cache_ttl = float(cache_ttl)
        self.max_cache_entries = int(max_cache_entries)
        self._external_session = session
        self._cache: "OrderedDict[str, tuple[float, list[SearchSnippet]]]" = OrderedDict()

    def _get_cached(self, key: str) -> Optional[list[SearchSnippet]]:
        item = self._cache.get(key)
        if not item:
            return None
        ts, data = item
        if (time.monotonic() - ts) > self.cache_ttl:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return data

    def _set_cached(self, key: str, data: list[SearchSnippet]) -> None:
        self._cache[key] = (time.monotonic(), data)
        self._cache.move_to_end(key)
        while len(self._cache) > self.max_cache_entries:
            self._cache.popitem(last=False)

    async def _fetch_bing_news(self, session: aiohttp.ClientSession, query: str) -> list[SearchSnippet]:
        url = "https://www.bing.com/news/search?format=rss&q=" + urllib.parse.quote(query)
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                xml_text = await resp.text()
            root = ET.fromstring(xml_text)
            out: list[SearchSnippet] = []
            for item in root.findall(".//item")[:4]:
                title = _strip_html(item.findtext("title") or "")
                desc = _strip_html(item.findtext("description") or "")
                pub = _strip_html(item.findtext("pubDate") or "")
                if title and "Microsoft account" not in title:
                    out.append(
                        SearchSnippet(
                            source="خبر روز (Bing News)",
                            title=title[:160],
                            snippet=desc[:320],
                            published=pub[:32],
                        )
                    )
            return out
        except Exception as exc:
            log.debug("خطای جزئی در Bing News RSS: %s", exc)
            return []

    async def _fetch_google_news(self, session: aiohttp.ClientSession, query: str) -> list[SearchSnippet]:
        has_persian = bool(re.search(r"[\u0600-\u06FF]", query))
        lang_params = "hl=fa&gl=IR&ceid=IR:fa" if has_persian else "hl=en-US&gl=US&ceid=US:en"
        url = f"https://news.google.com/rss/search?{lang_params}&q=" + urllib.parse.quote(query)
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                xml_text = await resp.text()
            root = ET.fromstring(xml_text)
            out: list[SearchSnippet] = []
            for item in root.findall(".//item")[:3]:
                title = _strip_html(item.findtext("title") or "")
                pub = _strip_html(item.findtext("pubDate") or "")
                if title:
                    out.append(
                        SearchSnippet(
                            source="اخبار لحظه‌ای (Google News)",
                            title=title[:180],
                            snippet="",
                            published=pub[:32],
                        )
                    )
            return out
        except Exception as exc:
            log.debug("خطای جزئی در Google News RSS: %s", exc)
            return []

    async def _fetch_wikipedia(
        self, session: aiohttp.ClientSession, query: str, lang: str = "fa", *, limit: int = 3
    ) -> list[SearchSnippet]:
        url = (
            f"https://{lang}.wikipedia.org/w/api.php?action=query&format=json"
            f"&generator=search&gsrsearch={urllib.parse.quote(query)}&gsrlimit={limit}"
            f"&prop=extracts&exintro=1&explaintext=1&exchars=1600"
        )
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                data: dict[str, Any] = await resp.json(content_type=None)
            pages = list((data.get("query") or {}).get("pages", {}).values())
            pages.sort(key=lambda p: int(p.get("index", 99)))
            out: list[SearchSnippet] = []
            for idx, page in enumerate(pages[:limit]):
                max_len = 1500 if idx == 0 else 800
                title = _strip_html(str(page.get("title") or ""))
                extract = _strip_html(str(page.get("extract") or ""))
                if title and extract:
                    out.append(
                        SearchSnippet(
                            source=f"ویکی‌پدیا ({lang.upper()})",
                            title=title[:120],
                            snippet=extract[:max_len],
                        )
                    )
            return out
        except Exception as exc:
            log.debug("خطای جزئی در Wikipedia (%s): %s", lang, exc)
            return []

    async def search(self, user_text: str, now_dt: Optional[_dt.datetime] = None) -> list[SearchSnippet]:
        """اجرای موازی جستجوی زنده با سقف زمانی کوتاه و حافظه نهان (Cache)."""
        base_q = clean_search_query(user_text)
        if not base_q:
            return []

        cache_key = base_q.lower()
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        dt = now_dt or _dt.datetime.now(TEHRAN_TZ)
        jy, _, _ = gregorian_to_jalali(dt.year, dt.month, dt.day)
        has_year = bool(re.search(r"(?:202\d|۲۰۲[۰-۹]|140\d|۱۴۰[۰-۹])", base_q))
        has_latin = bool(re.search(r"[A-Za-z]", base_q))
        news_q = base_q if has_year else f"{base_q} {jy}"

        owns_session = self._external_session is None
        client_timeout = aiohttp.ClientTimeout(total=self.timeout)
        session = self._external_session or aiohttp.ClientSession(
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            timeout=client_timeout,
        )

        try:
            tasks = [
                self._fetch_wikipedia(session, base_q, "fa", limit=3),
                self._fetch_bing_news(session, news_q),
                self._fetch_bing_news(session, base_q),
                self._fetch_google_news(session, base_q),
            ]
            if has_latin:
                tasks.insert(
                    1,
                    self._fetch_wikipedia(
                        session, f"{base_q} {dt.year}" if not has_year else base_q, "en", limit=2
                    ),
                )
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self.timeout + 0.4,
            )
        except Exception as exc:
            log.debug("پایان مهلت جستجوی وب زنده: %s", exc)
            results = []
        finally:
            if owns_session:
                await session.close()

        merged: list[SearchSnippet] = []
        seen_titles: set[str] = set()
        for group in results:
            if not isinstance(group, list):
                continue
            for item in group:
                norm_t = item.title.lower().strip()
                if norm_t in seen_titles:
                    continue
                seen_titles.add(norm_t)
                merged.append(item)

        if merged:
            self._set_cached(cache_key, merged[:8])
        return merged[:8]

    def format_for_prompt(self, snippets: list[SearchSnippet]) -> str:
        """قالب‌بندی نتایج زنده برای تزریق تمیز به پیام سیستم مدل Cloudflare."""
        if not snippets:
            return ""
        lines = [
            "🌐 اطلاعات زنده و به‌روز دریافت‌شده از وب (معتبر تا سال ۲۰۲۶ میلادی / ۱۴۰۵ خورشیدی):",
            "راهنما: برای پاسخ به سوال کاربر، اولویت مطلق با داده‌های به‌روز زیر است (به‌جای حافظه‌ی قدیمی مدل). "
            "پاسخ را کاملاً طبیعی، روان و ۱۰۰٪ به فارسی فصیح بنویس:",
        ]
        for idx, item in enumerate(snippets, start=1):
            pub_part = f" [تاریخ انتشار: {item.published}]" if item.published else ""
            body_part = f" — {item.snippet}" if item.snippet else ""
            lines.append(f"{idx}. [{item.source}]{pub_part} {item.title}{body_part}")
        return "\n".join(lines)
