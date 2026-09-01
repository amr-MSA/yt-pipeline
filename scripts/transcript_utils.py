"""
الطبقة الأولى من خط long-smart: سحب الملف النصي (الترانسكريبت) مع التوقيت
من يوتيوب باستخدام yt-dlp (الترجمة التلقائية أو المرفوعة، أيها متوفر).
"""
import glob
import json
import os
import re
import xml.etree.ElementTree as ET

import yt_dlp


def _clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw)
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    return text.strip()


def _parse_vtt(path: str) -> list[dict]:
    """يحوّل ملف .vtt إلى قائمة [{start, end, text}, ...] بالثواني."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})[^\n]*\n(.*?)(?=\n\n|\Z)",
        re.DOTALL,
    )

    def to_seconds(ts: str) -> float:
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    segments = []
    last_text = None
    for m in pattern.finditer(content):
        start, end, text = m.group(1), m.group(2), m.group(3)
        clean = _clean_text(text)
        if not clean or clean == last_text:
            continue
        segments.append({"start": round(to_seconds(start), 2), "end": round(to_seconds(end), 2), "text": clean})
        last_text = clean
    return segments


def fetch_transcript(url: str, work_dir: str, cookies_env: str | None = None) -> dict:
    """
    يسحب الترانسكريبت (نص + توقيت) لفيديو يوتيوب.
    يرجع: {"video_id":..., "title":..., "duration":..., "segments":[{start,end,text}, ...]}
    """
    os.makedirs(work_dir, exist_ok=True)
    sub_out = os.path.join(work_dir, "sub")

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["ar", "en", "ar.*", "en.*"],
        "subtitlesformat": "vtt",
        "outtmpl": sub_out,
        "quiet": True,
        "noprogress": True,
    }
    if cookies_env:
        cookie_path = os.path.join(work_dir, "cookies.txt")
        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write(cookies_env)
        ydl_opts["cookiefile"] = cookie_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info.get("id", "")
    title = info.get("title", "")
    duration = info.get("duration", 0)

    vtt_candidates = sorted(glob.glob(f"{sub_out}*.vtt"))
    if not vtt_candidates:
        raise RuntimeError(
            "لا يوجد ترجمة/ترانسكريبت متاح لهذا الفيديو (لا يدوي ولا تلقائي). "
            "لا يمكن متابعة معالجة /long الذكية بدونه."
        )

    segments = _parse_vtt(vtt_candidates[0])
    if not segments:
        raise RuntimeError("تم العثور على ملف ترجمة لكنه فارغ بعد التحليل.")

    return {
        "video_id": video_id,
        "title": title,
        "duration": duration,
        "segments": segments,
    }
