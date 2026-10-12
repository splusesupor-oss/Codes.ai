"""
پروفایل‌های سه‌گانه‌ی مدل‌های هوش مصنوعی (Groq و Cloudflare Workers AI).

مدل‌های منتخب:
  ۱) مدل ۱ — دستیار چت و مکالمه سریع (Groq API):
     openai/gpt-oss-20b
     بسیار سریع، روان و مناسب برای احوال‌پرسی، مکالمات کوتاه و گپ روزمره.

  ۲) مدل ۲ — دستیار استدلالی و دانش عمومی (Cloudflare Workers AI):
     @cf/meta/llama-3.3-70b-instruct-fp8-fast
     بدون کوچک‌ترین تغییر؛ مدل پایدار ۷۰ میلیاردی کلودفلر برای تحلیل عمیق و پاسخ به پرسش‌های عمومی.

  ۳) مدل ۳ — دستیار تخصصی کدنویسی و دیباگ (Groq API):
     openai/gpt-oss-120b
     مدل پیشرفته ۱۲۰ میلیاردی منحصراً برای برنامه‌نویسی، حل الگوریتم و رفع اشکال کد.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelProfile:
    """مشخصات یک پروفایل مدل هوش مصنوعی."""

    id: int
    name: str              # نام فارسی برای نمایش به مدیران و کاربران
    english_name: str      # نام انگلیسی طبق مشخصات فنی
    model_id: str          # شناسه رسمی در Workers AI یا Groq
    max_output_tokens: int
    timeout: float         # مهلت زمانی اجرای درخواست (ثانیه)
    description: str
    provider: str = "cloudflare"  # "cloudflare" یا "groq"


MODEL_PROFILES: dict[int, ModelProfile] = {
    1: ModelProfile(
        id=1,
        name="دستیار سریع و چت روزمره",
        english_name="Fast Conversational Chat",
        model_id="openai/gpt-oss-20b",
        max_output_tokens=2048,
        timeout=25.0,
        description="پاسخ‌دهی آنی به احوال‌پرسی و چت‌های عمومی و سبک با دقت زبانی استاندارد فارسی (Groq).",
        provider="groq",
    ),
    2: ModelProfile(
        id=2,
        name="دستیار استدلالی متعادل",
        english_name="Balanced Reasoning Assistant",
        model_id="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        max_output_tokens=4096,
        timeout=40.0,
        description="مدل قدرتمند Llama 3.3 70B کلودفلر با پردازش سریع (FP8)، درک عمیق زبان فارسی و تحلیل منطقی.",
        provider="cloudflare",
    ),
    3: ModelProfile(
        id=3,
        name="دستیار تخصصی کدنویسی و الگوریتم",
        english_name="Advanced Coding and Algorithms",
        model_id="openai/gpt-oss-120b",
        max_output_tokens=4096,
        timeout=45.0,
        description="استدلال فنی، کدنویسی، رفع باگ و طراحی الگوریتم‌های پیچیده نرم‌افزاری (Groq).",
        provider="groq",
    ),
}

DEFAULT_MODEL_PROFILE_ID = 1


def get_model_profile(profile_id: int) -> Optional[ModelProfile]:
    """دریافت پروفایل مدل بر اساس شماره شناسه (1، 2 یا 3)."""
    try:
        return MODEL_PROFILES.get(int(profile_id))
    except (ValueError, TypeError):
        return None


def format_model_status(current_profile_id: int) -> str:
    """متن راهنما و وضعیت مدل فعلی گروه."""
    profile = get_model_profile(current_profile_id) or MODEL_PROFILES[DEFAULT_MODEL_PROFILE_ID]
    lines = [
        f"🤖 مدل فعال هوش مصنوعی این گروه: مدل {profile.id}",
        f"🏷 عنوان: {profile.name} ({profile.english_name})",
        f"🌐 سرویس‌دهنده: {profile.provider.upper()}",
        f"🆔 شناسه مدل: {profile.model_id}",
        f"📝 توضیحات: {profile.description}",
        "",
        "📋 لیست تمام مدل‌های قابل انتخاب (فقط برای مالک سراسری):",
    ]
    for pid, p in sorted(MODEL_PROFILES.items()):
        mark = " (فعال)" if pid == profile.id else ""
        lines.append(f"• ai model {pid} : {p.name} [{p.english_name}]{mark}")

    return "\n".join(lines)
