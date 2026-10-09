"""
پروفایل‌های سه‌گانه‌ی مدل‌های هوش مصنوعی کلادفلر (Cloudflare Workers AI).

مدل‌های منتخب:
  ۱) مدل ۱ — دستیار سریع و عمومی (Fast General Assistant):
     @cf/meta/llama-3.1-8b-instruct-fp8-fast
     بسیار سریع و سبک، مناسب برای پاسخ‌گویی فوری به سؤالات عمومی روزمره.

  ۲) مدل ۲ — دستیار استدلالی متعادل (Balanced Reasoning Assistant):
     @cf/zai-org/glm-4.7-flash
     پنجره زمینه بسیار گسترده (۱۳۱ هزار توکن)، قابلیت تفکر و درک عمیق زبان فارسی و تحلیل چندمرحله‌ای.

  ۳) مدل ۳ — دستیار پیشرفته استدلال و کدنویسی (Advanced Reasoning and Coding Assistant):
     @cf/deepseek-ai/deepseek-r1-distill-qwen-32b
     مدل پیشرفته استدلالی بر پایه Qwen2.5 و تقطیرشده از DeepSeek-R1؛ عالی در حل مسائل ریاضی، منطقی و کدنویسی.
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
    model_id: str          # شناسه رسمی در Workers AI
    max_output_tokens: int
    timeout: float         # مهلت زمانی اجرای درخواست (ثانیه)
    description: str


MODEL_PROFILES: dict[int, ModelProfile] = {
    1: ModelProfile(
        id=1,
        name="دستیار سریع و عمومی",
        english_name="Fast General Assistant",
        model_id="@cf/meta/llama-3.1-8b-instruct-fp8-fast",
        max_output_tokens=1024,
        timeout=25.0,
        description="پاسخ‌دهی آنی و سبک، مصرف حداقل سهمیه، عالی برای مکالمات و سوالات روزمره.",
    ),
    2: ModelProfile(
        id=2,
        name="دستیار استدلالی متعادل",
        english_name="Balanced Reasoning Assistant",
        model_id="@cf/zai-org/glm-4.7-flash",
        max_output_tokens=1536,
        timeout=35.0,
        description="پنجره زمینه بزرگ (131k)، درک عالی فارسی و استدلال متوازن برای پاسخ‌های کامل‌تر.",
    ),
    3: ModelProfile(
        id=3,
        name="دستیار پیشرفته استدلال و کدنویسی",
        english_name="Advanced Reasoning and Coding Assistant",
        model_id="@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
        max_output_tokens=2048,
        timeout=45.0,
        description="قدرت استدلال بالا و توانایی تخصصی در حل مسائل برنامه‌نویسی و تحلیل عمیق.",
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
        f"🆔 شناسه: {profile.model_id}",
        f"📝 توضیحات: {profile.description}",
        "",
        "📋 لیست تمام مدل‌های قابل انتخاب (فقط برای مالک سراسری):",
    ]
    for pid, p in sorted(MODEL_PROFILES.items()):
        mark = " (فعال)" if pid == profile.id else ""
        lines.append(f"• ai model {pid} : {p.name} [{p.english_name}]{mark}")

    return "\n".join(lines)
