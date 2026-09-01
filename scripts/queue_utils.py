"""
إدارة طابور مقاطع خط long-smart.

لكل رابط تمت معالجته بجيمناي، نحفظ ملف JSON واحد فيه 8 مقاطع مقترحة،
كل مقطع بحالة "pending" أو "published" أو "failed".

كل تشغيل يومي: يمر على كل ملفات الطابور النشطة (فيها مقطع pending واحد
على الأقل)، ويأخذ **أول مقطع pending واحد من كل رابط**، وينشره — بشرط
عدم تجاوز سقف النشر اليومي المشترك لخط long-smart بالكامل (انظر publish_quota.py).
"""
import glob
import json
import os

QUEUE_DIR_NAME = "long_smart_queue"


def _queue_dir(state_dir: str) -> str:
    path = os.path.join(state_dir, QUEUE_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def _queue_file(state_dir: str, video_id: str) -> str:
    return os.path.join(_queue_dir(state_dir), f"{video_id}.json")


def queue_exists(state_dir: str, video_id: str) -> bool:
    return os.path.exists(_queue_file(state_dir, video_id))


def save_new_batch(
    state_dir: str,
    video_id: str,
    source_url: str,
    source_title: str,
    chat_id: int,
    clips: list[dict],
) -> None:
    """يحفظ دفعة جديدة من 8 مقاطع مقترحة لرابط جديد."""
    data = {
        "video_id": video_id,
        "source_url": source_url,
        "source_title": source_title,
        "chat_id": chat_id,
        "clips": [
            {
                **clip,
                "status": "pending",
                "youtube_url": None,
            }
            for clip in clips
        ],
    }
    with open(_queue_file(state_dir, video_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_batch(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_batch(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_active_batches(state_dir: str) -> list[dict]:
    """يرجع كل الدفعات التي فيها مقطع pending واحد على الأقل، مع مسار ملفها."""
    active = []
    for path in sorted(glob.glob(os.path.join(_queue_dir(state_dir), "*.json"))):
        data = _load_batch(path)
        if any(c["status"] == "pending" for c in data["clips"]):
            data["_path"] = path
            active.append(data)
    return active


def get_next_pending_index(batch: dict) -> int | None:
    for i, clip in enumerate(batch["clips"]):
        if clip["status"] == "pending":
            return i
    return None


def mark_clip_published(state_dir: str, video_id: str, clip_index: int, youtube_url: str) -> None:
    path = _queue_file(state_dir, video_id)
    data = _load_batch(path)
    data["clips"][clip_index]["status"] = "published"
    data["clips"][clip_index]["youtube_url"] = youtube_url
    _save_batch(path, data)


def mark_clip_failed(state_dir: str, video_id: str, clip_index: int, error: str) -> None:
    path = _queue_file(state_dir, video_id)
    data = _load_batch(path)
    data["clips"][clip_index]["status"] = "failed"
    data["clips"][clip_index]["error"] = error[:300]
    _save_batch(path, data)


def batch_progress(batch: dict) -> tuple[int, int]:
    """يرجع (المنشور/المعالَج، الإجمالي)."""
    done = sum(1 for c in batch["clips"] if c["status"] in ("published", "failed"))
    return done, len(batch["clips"])
