"""
عنوان ووصف ثابت يُدمج تلقائيًا في كل نشر.

يُضاف إلى الوصف أيضًا سطر مصدر ديناميكي يذكر اسم مستخدم الحساب الأصلي
المستخرج من بيانات yt-dlp، مع استخدام رابط المصدر عند عدم توفر اسم مستخدم.
"""
import os
import re
from urllib.parse import unquote, urlparse

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXED_TITLE_PATH = os.path.join(PROJECT_DIR, "assets", "fixed_title.txt")
FIXED_DESCRIPTION_PATH = os.path.join(PROJECT_DIR, "assets", "fixed_description.txt")

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


def _clean_handle(value: str | None) -> str:
    value = unquote(str(value or "")).strip().strip("/")
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.path.strip("/").split("/", 1)[0] if parsed.path else ""
    if value.startswith("@"):
        value = value[1:]
    return value.strip().strip("/")


def _is_instagram_url(url: str) -> bool:
    host = (urlparse(str(url or "")).hostname or "").lower().removeprefix("www.")
    return host == "instagram.com" or host.endswith(".instagram.com")


def _instagram_handle_from_url(url: str) -> str:
    """استخرج اسم المستخدم من مسارات Instagram مثل /name/reel/id أو /name/p/id."""
    if not _is_instagram_url(url):
        return ""
    parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
    if not parts:
        return ""
    reserved = {
        "accounts", "about", "direct", "directory", "emails", "explore",
        "legal", "press", "privacy", "session", "stories", "tags", "tv",
        "web", "challenge", "reel", "reels", "p",
    }
    candidate = parts[0].lstrip("@")
    if (
        candidate.lower() in reserved
        or candidate.isdigit()
        or not re.fullmatch(r"[A-Za-z0-9._]{1,30}", candidate)
    ):
        return ""
    return candidate


def _looks_like_instagram_username(value: str | None) -> bool:
    value = _clean_handle(value)
    return bool(
        value
        and not value.isdigit()
        and re.fullmatch(r"[A-Za-z0-9._]{1,30}", value)
    )


def source_handle_from_info(info: dict | None) -> str:
    """استخرج handle الحساب من yt-dlp، مع دعم بنية روابط Instagram."""
    info = info or {}
    source_urls = tuple(
        str(info.get(key) or "")
        for key in ("uploader_url", "channel_url", "webpage_url", "original_url")
    )
    if any(_is_instagram_url(url) for url in source_urls):
        # روابط الحساب هي المصدر الموثوق لاسم المستخدم؛ uploader_id قد يكون
        # Instagram numeric account ID وليس username.
        for key in ("uploader_url", "channel_url", "webpage_url", "original_url"):
            value = _instagram_handle_from_url(str(info.get(key) or ""))
            if value:
                return value
        for key in ("uploader", "channel"):
            value = _clean_handle(info.get(key))
            if _looks_like_instagram_username(value):
                return value
        value = _clean_handle(info.get("uploader_id"))
        if _looks_like_instagram_username(value):
            return value
        return ""

    for key in ("channel_handle", "uploader_id"):
        value = _clean_handle(info.get(key))
        if value and not value.startswith("UC"):
            return value

    for key in ("channel_url", "uploader_url", "webpage_url"):
        url = str(info.get(key) or "")
        match = re.search(r"/(?:@|channel/|user/|c/)([^/?#]+)", url)
        if match:
            value = _clean_handle(match.group(1))
            if value and not value.startswith("UC"):
                return value
    return ""


def source_attribution(source_handle: str | None = None, source_url: str | None = None) -> str:
    """أنشئ سطر الإسناد المطلوب، مع رابط المصدر إذا تعذر استخراج الـ handle."""
    handle = _clean_handle(source_handle)
    account_label = "حساب" if _is_instagram_url(source_url or "") else "قناة"
    if handle:
        return f"📌 المصدر: هذا المقطع مأخوذ من {account_label} @{handle}."
    if source_url:
        return f"📌 المصدر: هذا المقطع مأخوذ من الحساب الأصلي: {source_url}"
    return "📌 المصدر: هذا المقطع مأخوذ من الحساب الأصلي، والحقوق محفوظة لأصحابه."


def build_final_description(
    gemini_description: str,
    source_handle: str | None = None,
    source_url: str | None = None,
) -> str:
    fixed = _read_fixed(FIXED_DESCRIPTION_PATH)
    attribution = source_attribution(source_handle, source_url)
    parts = [part for part in (gemini_description.strip(), attribution, fixed) if part]
    return "\n\n".join(parts)[:YOUTUBE_DESCRIPTION_MAX]


def build_aggregate_source_list(sequence: list[dict], source_infos: list[dict] | None = None) -> str:
    """أنشئ قائمة مصادر مرتبة تحافظ على موضع كل رابط داخل التجميعة."""
    source_infos = source_infos or []
    lines = ["📌 مصادر المقاطع:"]
    for index, token in enumerate(sequence, 1):
        if token.get("kind") == "local":
            label = f"مقطع محلي ({token.get('value', '')}.mp4)"
        else:
            info = dict(source_infos[index - 1]) if index - 1 < len(source_infos) else {}
            source_url = str(
                info.get("webpage_url")
                or info.get("original_url")
                or token.get("value")
                or ""
            )
            handle = source_handle_from_info({**info, "webpage_url": source_url})
            label = f"@{handle}" if handle else source_url
        lines.append(f"المقطع {index}: {label}")
    return "\n".join(lines)
