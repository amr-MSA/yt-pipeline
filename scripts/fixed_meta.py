"""
عنوان ووصف ثابت (توقيع/CTA) يُدمج تلقائيًا في نهاية كل عنوان ووصف
قادم من جيمناي، لضمان وجود توقيع ثابت على كل مقطع يُنشر عبر خط long-smart.

الملفان فارغان افتراضيًا — ضع فيهما ما تريد إضافته (مثلاً: اسم القناة،
هاشتاقات ثابتة، رابط سوشيال ميديا...) وسيُدمَج تلقائيًا.
"""
import os

FIXED_TITLE_PATH = "assets/fixed_title.txt"
FIXED_DESCRIPTION_PATH = "assets/fixed_description.txt"

YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESCRIPTION_MAX = 4900


def _read_fixed(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def build_final_title(gemini_title: str) -> str:
    fixed = _read_fixed(FIXED_TITLE_PATH)
    if not fixed:
        return gemini_title[:YOUTUBE_TITLE_MAX]
    combined = f"{gemini_title} {fixed}".strip()
    if len(combined) <= YOUTUBE_TITLE_MAX:
        return combined
    # لو تجاوز الحد، نقلّم عنوان جيمناي ونحافظ على التوقيع الثابت كاملاً
    room = YOUTUBE_TITLE_MAX - len(fixed) - 1
    if room <= 0:
        return fixed[:YOUTUBE_TITLE_MAX]
    return f"{gemini_title[:room].rstrip()} {fixed}"


def build_final_description(gemini_description: str) -> str:
    fixed = _read_fixed(FIXED_DESCRIPTION_PATH)
    if not fixed:
        return gemini_description[:YOUTUBE_DESCRIPTION_MAX]
    combined = f"{gemini_description}\n\n{fixed}".strip()
    return combined[:YOUTUBE_DESCRIPTION_MAX]
