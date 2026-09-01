"""
أدوات التعامل مع بوت تلجرام: جلب الرسائل الجديدة، استخراج الروابط،
وحفظ/قراءة آخر update_id تمت معالجته حتى لا نعالج نفس الرسالة مرتين.
"""
import json
import os
import re
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# نمط للتعرف على روابط يوتيوب (فيديو عادي أو shorts)
YOUTUBE_URL_RE = re.compile(
    r"(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)[\w-]+|youtu\.be/[\w-]+)[^\s]*)",
    re.IGNORECASE,
)

# نمط للتعرف على أمر /long متبوعًا برابط يوتيوب (خط الإنتاج الذكي المنفصل)
LONG_COMMAND_RE = re.compile(
    r"/long(?:@\w+)?\s+"
    r"(https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/)[\w-]+|youtu\.be/[\w-]+)[^\s]*)",
    re.IGNORECASE,
)


def _offset_file(state_dir: str, bot_name: str) -> str:
    return os.path.join(state_dir, f"{bot_name}_offset.json")


def load_offset(state_dir: str, bot_name: str) -> int:
    """يقرأ آخر update_id تمت معالجته لهذا البوت. 0 إذا لا يوجد."""
    path = _offset_file(state_dir, bot_name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return int(data.get("last_update_id", 0))
    return 0


def save_offset(state_dir: str, bot_name: str, update_id: int) -> None:
    os.makedirs(state_dir, exist_ok=True)
    path = _offset_file(state_dir, bot_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"last_update_id": update_id}, f, ensure_ascii=False, indent=2)


def fetch_new_links(bot_token: str, state_dir: str, bot_name: str) -> list[dict]:
    """
    يجلب الرسائل الجديدة المرسلة للبوت منذ آخر مرة، ويستخرج منها روابط يوتيوب
    العادية (بدون أمر /long — تلك تُعالَج بخط منفصل عبر fetch_new_long_commands).
    يرجع قائمة: [{"url": ..., "chat_id": ..., "message_id": ..., "text": ...}, ...]
    ويحدّث ملف الـ offset تلقائيًا بعد الجلب (حتى لو ما فيه روابط) حتى لا نعيد قراءة نفس الرسائل.
    """
    updates = _fetch_updates(bot_token, state_dir, bot_name)
    links = []

    for msg in _iter_messages(updates):
        text = msg.get("text") or msg.get("caption") or ""
        if LONG_COMMAND_RE.search(text):
            # هذه رسالة أمر /long، تُعالَج في مكان آخر — نتجاهلها هنا
            continue
        found = YOUTUBE_URL_RE.findall(text)
        for u in found:
            links.append(
                {
                    "url": u,
                    "chat_id": msg["chat"]["id"],
                    "message_id": msg["message_id"],
                    "text": text.strip(),
                }
            )

    return links


def fetch_all_new_messages(bot_token: str, state_dir: str, bot_name: str) -> tuple[list[dict], list[dict]]:
    """
    يجلب دفعة واحدة من التحديثات الجديدة ويفرزها إلى (روابط_عادية، أوامر_long)
    في استدعاء واحد فقط، حتى لا يتضارب استهلاك offset بين دالتين منفصلتين.

    استخدم هذه الدالة (وليس fetch_new_links) في أي بوت يحتاج التقاط أمر /long
    بالإضافة إلى الروابط العادية في نفس التشغيل.
    """
    updates = _fetch_updates(bot_token, state_dir, bot_name)
    links = []
    long_commands = []

    for msg in _iter_messages(updates):
        text = msg.get("text") or msg.get("caption") or ""
        m = LONG_COMMAND_RE.search(text)
        if m:
            long_commands.append(
                {
                    "url": m.group(1),
                    "chat_id": msg["chat"]["id"],
                    "message_id": msg["message_id"],
                    "text": text.strip(),
                }
            )
            continue
        for u in YOUTUBE_URL_RE.findall(text):
            links.append(
                {
                    "url": u,
                    "chat_id": msg["chat"]["id"],
                    "message_id": msg["message_id"],
                    "text": text.strip(),
                }
            )

    return links, long_commands


def _iter_messages(updates: list[dict]):
    for upd in updates:
        msg = upd.get("message") or upd.get("channel_post")
        if msg:
            yield msg


def _fetch_updates(bot_token: str, state_dir: str, bot_name: str) -> list[dict]:
    """يجلب التحديثات الخام من تلجرام ويحدّث الـ offset. يُستخدم داخليًا فقط."""
    last_offset = load_offset(state_dir, bot_name)

    url = TELEGRAM_API.format(token=bot_token, method="getUpdates")
    params = {"offset": last_offset + 1, "timeout": 0, "limit": 100}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"فشل استدعاء Telegram API: {data}")

    updates = data.get("result", [])
    if updates:
        max_update_id = max(upd["update_id"] for upd in updates)
        max_update_id = max(max_update_id, last_offset)
        save_offset(state_dir, bot_name, max_update_id)

    return updates


def send_message(bot_token: str, chat_id: int, text: str) -> None:
    """يرسل رسالة نصية للمستخدم (تأكيد نجاح/فشل مثلاً)."""
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    try:
        requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception:
        # لا نوقف السير إذا فشل إرسال رسالة التأكيد فقط
        pass
