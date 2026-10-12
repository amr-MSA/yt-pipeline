"""Idempotency guard backed by the channel's latest YouTube uploads."""
from __future__ import annotations

import re

REQUEST_MARKER_PREFIX = "yt-pipeline-request:"
MARKER_RE = re.compile(r"yt-pipeline-request:[^\s]+")


def request_marker(pipeline: str, request_key: str) -> str:
    """Return a stable marker shared by retries of the same Telegram request."""
    return f"{REQUEST_MARKER_PREFIX}{pipeline}:{request_key}"


def recent_request_markers(youtube, limit: int = 10) -> dict[str, str]:
    """Read markers from the channel's latest uploads, including Long and Shorts.

    This intentionally fails closed: callers should abort before uploading if the
    check cannot be completed. YouTube has no idempotency key for videos.insert,
    so this is the external source-of-truth safety net when local state is lost.
    """
    if limit != 10:
        raise ValueError("The duplicate guard is deliberately fixed to the latest 10 uploads")

    channels = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = channels.get("items") or []
    if not items:
        raise RuntimeError("لم يتم العثور على قناة YouTube للحساب الموثق")
    uploads_playlist = (
        items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    )
    if not uploads_playlist:
        raise RuntimeError("تعذر تحديد قائمة فيديوهات القناة")

    playlist = youtube.playlistItems().list(
        part="contentDetails", playlistId=uploads_playlist, maxResults=10
    ).execute()
    video_ids = [
        item.get("contentDetails", {}).get("videoId")
        for item in playlist.get("items", [])
    ]
    video_ids = [video_id for video_id in video_ids if video_id]
    if not video_ids:
        return {}

    videos = youtube.videos().list(
        part="snippet", id=",".join(video_ids)
    ).execute()
    markers: dict[str, str] = {}
    for item in videos.get("items", []):
        description = item.get("snippet", {}).get("description") or ""
        video_id = item.get("id")
        if not video_id:
            continue
        for marker in MARKER_RE.findall(description):
            markers[marker] = video_id
    return markers


def append_request_marker(description: str, marker: str) -> str:
    """Append a searchable, stable idempotency marker to a YouTube description."""
    description = (description or "").rstrip()
    suffix = f"\n\n{marker}"
    available = max(0, 4900 - len(suffix))
    return description[:available].rstrip() + suffix
