"""
أدوات التعامل مع بوت تلجرام: جلب الرسائل، استخراج الروابط، وإدارة رسائل
الروابط التي تمت معالجتها بنجاح.
"""
import json
import os
import re
import subprocess
from urllib.parse import urlsplit
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"

# yt-dlp يدعم آلاف المواقع والمنصات؛ لا نحصر الروابط في YouTube هنا.
GENERIC_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
TRAILING_URL_PUNCTUATION = ".,!?;:'\"،؛؟)]}>"

# نمط للتعرف على أمر /long متبوعًا برابط من أي منصة يدعمها yt-dlp.
LONG_COMMAND_RE = re.compile(
    r"/long(?:@\w+)?\s+"
    r"(https?://[^\s<>]+)",
    re.IGNORECASE,
)
TAKE_TITLE_COMMAND_RE = re.compile(r"^/take(?:@\w+)?$", re.IGNORECASE)
DEFAULT_TITLE_COMMAND_RE = re.compile(r"^/d(?:@\w+)?$", re.IGNORECASE)
CUSTOM_TITLE_COMMAND_RE = re.compile(
    r"^/t(?:@\w+)?\s+[\"“](.+?)[\"”]$", re.IGNORECASE | re.DOTALL
)


def _clean_url(url: str) -> str:
    """يزيل علامات الترقيم التي تلحق بالرابط داخل نص الرسالة."""
    return url.rstrip(TRAILING_URL_PUNCTUATION)


def _is_safe_url(url: str) -> bool:
    """يرفض userinfo حتى لا تُحفظ أو تُرسل بيانات دخول ضمن رابط المصدر."""
    try:
        parsed = urlsplit(url)
        return parsed.scheme.lower() in {"http", "https"} and not parsed.username and not parsed.password
    except ValueError:
        return False


def extract_urls(text: str) -> list[str]:
    """يستخرج روابط HTTP(S) من أي منصة، مع إزالة punctuation المحيط."""
    urls = (_clean_url(match.group(0)) for match in GENERIC_URL_RE.finditer(text))
    return [url for url in urls if _is_safe_url(url)]


def is_boundary_marker(text: str) -> bool:
    """يتعرف على رسالة تحكم مكونة من أرقام/رموز فقط، مثل ``1`` أو ``.``."""
    stripped = text.strip()
    if not stripped or extract_urls(stripped):
        return False
    return all(not char.isalpha() for char in stripped) and any(
        char.isdigit() or not char.isspace() for char in stripped
    )


def _apply_title_command(text: str, item: dict | None) -> bool:
    """يطبق أمر عنوان على آخر رابط، ويرجع True عند التعرف على الأمر."""
    command = text.strip()
    if item is None:
        return False
    if TAKE_TITLE_COMMAND_RE.fullmatch(command):
        item["title_mode"] = "source"
        item.pop("title_override", None)
        return True
    if DEFAULT_TITLE_COMMAND_RE.fullmatch(command):
        item["title_mode"] = "default"
        item.pop("title_override", None)
        return True
    match = CUSTOM_TITLE_COMMAND_RE.fullmatch(command)
    if match:
        item["title_mode"] = "custom"
        item["title_override"] = match.group(1).strip()
        return True
    return False


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
    """يجلب الروابط الجديدة، مع إبقاء كل رسالة كعنصر مستقل حتى عند تكرار الرابط."""
    updates = _fetch_updates(bot_token, state_dir, bot_name)
    links = []
    last_link_by_chat: dict[int, dict] = {}

    for msg in _iter_messages(updates):
        text = msg.get("text") or msg.get("caption") or ""
        if is_boundary_marker(text):
            links = []
            last_link_by_chat = {}
            continue
        chat_id = msg["chat"]["id"]
        if _apply_title_command(text, last_link_by_chat.get(chat_id)):
            continue
        if LONG_COMMAND_RE.search(text):
            continue
        for u in extract_urls(text):
            item = {
                    "url": u,
                    "chat_id": chat_id,
                    "message_id": msg["message_id"],
                    "text": text.strip(),
                    "title_mode": "default",
                }
            links.append(item)
            last_link_by_chat[chat_id] = item

    return links


def fetch_all_new_messages(bot_token: str, state_dir: str, bot_name: str) -> tuple[list[dict], list[dict]]:
    """يجلب دفعة واحدة ويفرزها إلى روابط عادية وأوامر /long."""
    updates = _fetch_updates(bot_token, state_dir, bot_name)
    links = []
    long_commands = []

    for msg in _iter_messages(updates):
        text = msg.get("text") or msg.get("caption") or ""
        if is_boundary_marker(text):
            links = []
            long_commands = []
            continue
        m = LONG_COMMAND_RE.search(text)
        if m:
            url = _clean_url(m.group(1))
            if not _is_safe_url(url):
                continue
            long_commands.append(
                {
                    "url": url,
                    "chat_id": msg["chat"]["id"],
                    "message_id": msg["message_id"],
                    "text": text.strip(),
                }
            )
            continue
        for u in extract_urls(text):
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
    """يجلب التحديثات الخام ويحدّث offset بعد الجلب."""
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
        save_offset(state_dir, bot_name, max(max_update_id, last_offset))

    return updates


def send_message(bot_token: str, chat_id: int, text: str) -> None:
    """يرسل رسالة نصية ولا يوقف السير إذا فشل الإشعار."""
    url = TELEGRAM_API.format(token=bot_token, method="sendMessage")
    try:
        requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception:
        pass


def send_batch_summary(bot_token: str, items: list[dict], label: str = "الرابط") -> None:
    """يرسل ملخصًا واحدًا لكل قناة/محادثة عن العناصر المقبولة في الدفعة."""
    counts: dict[int, int] = {}
    for item in items:
        counts[item["chat_id"]] = counts.get(item["chat_id"], 0) + 1
    for chat_id, count in counts.items():
        unit = label if count == 1 else ("روابط" if label == "رابط" else label)
        send_message(
            bot_token,
            chat_id,
            f"📊 تم قبول {count} {unit} للمعالجة في هذه الدفعة.",
        )


def delete_message(bot_token: str, chat_id: int, message_id: int) -> bool:
    """يحذف رسالة Telegram ويعيد True فقط إذا أكد API نجاح الحذف."""
    url = TELEGRAM_API.format(token=bot_token, method="deleteMessage")
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "message_id": message_id},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return bool(data.get("ok"))
    except (requests.RequestException, ValueError):
        return False


def upload_state_path(state_dir: str, bot_name: str) -> str:
    return os.path.join(state_dir, f"{bot_name}_uploaded_messages.json")


def load_uploaded_messages(state_dir: str, bot_name: str) -> dict:
    path = upload_state_path(state_dir, bot_name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_uploaded_messages(state_dir: str, bot_name: str, state: dict) -> None:
    """يحفظ سجل النجاح قبل محاولة الحذف لمنع إعادة الرفع بعد فشل الحذف.

    قبل الكتابة، يُعاد دمج الحالة مع أحدث نسخة موجودة فعليًا على القرص
    (والتي قد تكون تحدّثت بعد load_uploaded_messages بسبب checkout جديد
    في خطوة merge_latest_state). هذا يمنع فقدان مفاتيح نجاح سجّلها push
    من تشغيلة أخرى وصل بعد أن قرأنا نسختنا المحلية.
    """
    os.makedirs(state_dir, exist_ok=True)
    path = upload_state_path(state_dir, bot_name)
    on_disk = load_uploaded_messages(state_dir, bot_name)
    merged = dict(on_disk)
    merged.update(state)
    # أي مفتاح كان بالقرص لكن أُزيل عمدًا من `state` (بعد حذف رسالة تلجرام
    # الناجح) يجب أن يبقى محذوفًا، لا أن يعود بسبب الدمج.
    removed_keys = set(on_disk) - set(state)
    for k in removed_keys:
        merged.pop(k, None)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def message_key(item: dict) -> str:
    return f"{item['chat_id']}:{item['message_id']}"


def source_history_path(state_dir: str, bot_name: str) -> str:
    return os.path.join(state_dir, f"{bot_name}_source_history.json")


def load_source_history(state_dir: str, bot_name: str) -> dict:
    """يقرأ سجل الروابط المنشورة حتى لا يؤدي تكرار رسالة الرابط إلى إعادة الرفع."""
    path = source_history_path(state_dir, bot_name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_source_history(state_dir: str, bot_name: str, history: dict) -> None:
    """يحفظ سجل المصادر باستبدال ذري ودمج آمن مع أحدث نسخة محلية."""
    os.makedirs(state_dir, exist_ok=True)
    path = source_history_path(state_dir, bot_name)
    on_disk = load_source_history(state_dir, bot_name)
    merged = dict(on_disk)
    merged.update(history)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def record_source_success(state_dir: str, bot_name: str, source_url: str, video_id: str) -> None:
    history = load_source_history(state_dir, bot_name)
    history[source_url] = {"video_id": video_id}
    save_source_history(state_dir, bot_name, history)


def message_ledger_path(state_dir: str, bot_name: str) -> str:
    return os.path.join(state_dir, f"{bot_name}_message_ledger.json")


def load_message_ledger(state_dir: str, bot_name: str) -> dict:
    """يقرأ سجل الرسائل؛ السجل طبقة حماية إضافية بجانب Telegram offset."""
    path = message_ledger_path(state_dir, bot_name)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_message_ledger(state_dir: str, bot_name: str, ledger: dict) -> None:
    """يحفظ السجل باستبدال ذري حتى لا ينتج ملف JSON جزئي.

    يُدمج مع أحدث نسخة موجودة على القرص أولًا (نفس منطق save_uploaded_messages)
    حتى لا تُفقد تسجيلات نجاح كتبتها تشغيلة أخرى ودُمجت عبر merge_latest_state
    بعد أن قرأنا نسختنا المحلية في بداية هذا التشغيل.
    """
    os.makedirs(state_dir, exist_ok=True)
    path = message_ledger_path(state_dir, bot_name)
    on_disk = load_message_ledger(state_dir, bot_name)
    merged = dict(on_disk)
    merged.update(ledger)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def merge_latest_state(state_dir: str, bot_name: str) -> None:
    """يسحب أحدث state/ من origin/main ويدمجه مع الملفات المحلية قبل الحفظ الأخير.

    يُستدعى في نهاية كل سير عمل (finally)، قبل أن يحفظ السكربت آخر نسخة من
    offset/uploaded_messages/ledger. هذا يحمي من فقدان تسجيلات نجاح كتبتها
    تشغيلة موازية دفعت (push) للريبو أثناء تنفيذ هذه التشغيلة الحالية —
    وهو ما كان يسبب إعادة نشر نفس الفيديو مرتين عند فشل/تعارض git rebase
    في خطوة الـ workflow.

    الدمج يتم على مستوى الحقول (key-by-key) لا على مستوى النص، لذلك لا
    يحتاج git ولا يمكن أن يفشل بتعارض نصي كما يحصل مع `git rebase` على JSON.
    """
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            check=True, timeout=30, capture_output=True,
        )
        remote_files = subprocess.run(
            ["git", "show", f"origin/main:state/{bot_name}_uploaded_messages.json"],
            capture_output=True, text=True, timeout=15,
        )
        if remote_files.returncode == 0 and remote_files.stdout.strip():
            remote_uploaded = json.loads(remote_files.stdout)
            if isinstance(remote_uploaded, dict):
                local = load_uploaded_messages(state_dir, bot_name)
                combined = dict(remote_uploaded)
                combined.update(local)
                save_uploaded_messages(state_dir, bot_name, combined)

        remote_ledger = subprocess.run(
            ["git", "show", f"origin/main:state/{bot_name}_message_ledger.json"],
            capture_output=True, text=True, timeout=15,
        )
        if remote_ledger.returncode == 0 and remote_ledger.stdout.strip():
            remote_data = json.loads(remote_ledger.stdout)
            if isinstance(remote_data, dict):
                local = load_message_ledger(state_dir, bot_name)
                combined = dict(remote_data)
                combined.update(local)
                save_message_ledger(state_dir, bot_name, combined)

        remote_sources = subprocess.run(
            ["git", "show", f"origin/main:state/{bot_name}_source_history.json"],
            capture_output=True, text=True, timeout=15,
        )
        if remote_sources.returncode == 0 and remote_sources.stdout.strip():
            remote_data = json.loads(remote_sources.stdout)
            if isinstance(remote_data, dict):
                local = load_source_history(state_dir, bot_name)
                combined = dict(remote_data)
                combined.update(local)
                save_source_history(state_dir, bot_name, combined)
    except Exception as e:
        # لا نوقف السير بسبب فشل الدمج الاحتياطي؛ نكتفي بتسجيل تحذير.
        # آلية retry/merge بخطوة الـ workflow تبقى خط الدفاع الثاني.
        print(f"⚠️ تعذر دمج أحدث state من origin/main (سيُعتمد على retry بالـ workflow): {e}")
