"""
سير عمل بوت "لونق":
1. يفحص بوت تلجرام لأي روابط يوتيوب جديدة أُرسلت إليه.
2. لكل رسالة رابط: يحمّل الفيديو كاملًا.
3. ينشره على قناة يوتيوب الخاصة بك كفيديو عام (public).
4. بعد تأكيد videoId فقط، يحذف رسالة الرابط من Telegram.
5. يحتفظ بنجاح الرفع إذا تعذر الحذف حتى لا يعاد رفع الفيديو.
"""
import os
import sys
import traceback

import yt_dlp
from googleapiclient.http import MediaFileUpload
from cookie_utils import temporary_cookie_file

from telegram_utils import (
    delete_message,
    fetch_new_links,
    load_uploaded_messages,
    load_source_history,
    merge_latest_state,
    message_key,
    save_uploaded_messages,
    record_source_success,
    send_message,
)
from youtube_auth import get_youtube_client

STATE_DIR = "state"
DOWNLOAD_DIR = "downloads_long"
BOT_NAME = "long"


def download_video(url: str, out_path: str) -> dict:
    ydl_opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/best",
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "overwrites": True,
        "quiet": True,
        "noprogress": True,
        # Deno هو runtime الموصى به لـ EJS، وتثبيت yt-dlp[default] يوفر yt-dlp-ejs.
        "js_runtimes": {"deno": {}},
    }
    with temporary_cookie_file(os.environ.get("YT_COOKIES")) as cookie_path:
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)


def upload_video(youtube, file_path: str, title: str, description: str) -> str:
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public"},
    }
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(file_path, resumable=True),
    )
    response = request.execute()
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError("نجح طلب YouTube دون إرجاع videoId؛ لن تُحذف رسالة Telegram")
    return video_id


def reconcile_uploaded_messages(bot_token: str, uploaded: dict) -> dict:
    """يحاول حذف الرسائل التي نجح رفعها في تشغيل سابق ولم يُحذف أصلها بعد."""
    pending = dict(uploaded)
    for key, item in list(pending.items()):
        if delete_message(bot_token, item["chat_id"], item["message_id"]):
            print(f"🗑️ تم حذف رسالة Telegram المؤكدة سابقًا: {key}")
            pending.pop(key, None)
        else:
            print(f"⚠️ تعذر حذف رسالة Telegram بعد رفع مؤكد، ستتم إعادة المحاولة: {key}")
    return pending


def main():
    bot_token = os.environ["LONG_BOT_TOKEN"]
    youtube_token_json = os.environ["YOUTUBE_TOKEN_JSON"]

    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    uploaded = load_uploaded_messages(STATE_DIR, BOT_NAME)
    source_history = load_source_history(STATE_DIR, BOT_NAME)
    reconciled = reconcile_uploaded_messages(bot_token, uploaded)
    if reconciled != uploaded:
        save_uploaded_messages(STATE_DIR, BOT_NAME, reconciled)
    uploaded = reconciled

    print("🔎 جاري فحص رسائل بوت لونق الجديدة...")
    links = fetch_new_links(bot_token, STATE_DIR, BOT_NAME)

    if not links:
        print("📭 لا توجد روابط جديدة. إنهاء.")
        return

    print(f"📦 تم العثور على {len(links)} رابط جديد.")
    youtube = get_youtube_client(youtube_token_json)

    for idx, item in enumerate(links, 1):
        url = item["url"]
        chat_id = item["chat_id"]
        message_id = item["message_id"]
        key = message_key(item)
        print(f"\n{'='*50}\n[{idx}/{len(links)}] معالجة: {url} (message={message_id})")
        raw_path = os.path.join(DOWNLOAD_DIR, f"video_{idx}.mp4")

        try:
            # قد تكون الرسالة عادت من دفعة قديمة؛ لا نعيد الرفع إذا كان النجاح مسجلاً.
            if key in uploaded:
                print(f"ℹ️ الرفع مسجل مسبقًا لهذه الرسالة؛ لن يعاد رفعها: {key}")
                if delete_message(bot_token, chat_id, message_id):
                    uploaded.pop(key, None)
                    save_uploaded_messages(STATE_DIR, BOT_NAME, uploaded)
                continue

            if url in source_history:
                previous_video = source_history[url].get("video_id")
                print(f"ℹ️ المصدر منشور مسبقًا؛ تم تجاوز إعادة الرفع: {url}")
                if previous_video:
                    send_message(
                        bot_token,
                        chat_id,
                        f"ℹ️ تم نشر هذا الرابط مسبقًا ولن يُرفع مرة أخرى:\nhttps://youtu.be/{previous_video}",
                    )
                continue

            print("⬇️ تحميل الفيديو...")
            info = download_video(url, raw_path)
            title = info.get("title", "فيديو جديد")
            desc = info.get("description", "") or ""

            actual_file = raw_path
            if not os.path.exists(actual_file):
                candidates = [
                    f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(f"video_{idx}")
                ]
                if candidates:
                    actual_file = os.path.join(DOWNLOAD_DIR, candidates[0])

            if not os.path.isfile(actual_file):
                raise FileNotFoundError("yt-dlp لم ينتج ملف الفيديو المتوقع.")

            print("📤 رفع على يوتيوب (public)...")
            video_id = upload_video(youtube, actual_file, title, desc)
            video_url = f"https://youtu.be/{video_id}"
            print(f"✅ تم النشر وتأكيد videoId: {video_url}")

            # يُحفظ النجاح قبل الحذف. إذا فشل الحذف فلن يؤدي التشغيل التالي إلى إعادة الرفع.
            uploaded[key] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "video_id": video_id,
                "url": url,
            }
            save_uploaded_messages(STATE_DIR, BOT_NAME, uploaded)
            record_source_success(STATE_DIR, BOT_NAME, url, video_id)
            source_history[url] = {"video_id": video_id}

            if delete_message(bot_token, chat_id, message_id):
                uploaded.pop(key, None)
                save_uploaded_messages(STATE_DIR, BOT_NAME, uploaded)
                print(f"🗑️ حُذفت رسالة الرابط بعد نجاح الرفع: {message_id}")
            else:
                print(f"⚠️ نجح الرفع لكن تعذر حذف الرسالة؛ لن يعاد الرفع: {message_id}")

            send_message(bot_token, chat_id, f"✅ تم نشر الفيديو بنجاح:\n{video_url}")

        except Exception as e:
            err = f"{e}"
            print(f"❌ فشل: {err}")
            traceback.print_exc()
            send_message(bot_token, chat_id, f"❌ فشلت معالجة الرابط:\n{url}\n\nالخطأ: {err[:300]}")

        finally:
            for f in os.listdir(DOWNLOAD_DIR):
                try:
                    os.remove(os.path.join(DOWNLOAD_DIR, f))
                except OSError:
                    pass

    # دمج احتياطي أخير مع أحدث state/ من origin/main، حتى لو كانت هناك
    # تشغيلة موازية دفعت (push) تسجيلات نجاح جديدة أثناء تنفيذ هذا التشغيل.
    # هذا يمنع أن يعيد push الـ workflow اللاحق (rebase) الكتابة فوق
    # تسجيل نجاح حقيقي، وهو ما كان يؤدي لإعادة رفع نفس الفيديو.
    try:
        merge_latest_state(STATE_DIR, BOT_NAME)
    except Exception as e:
        print(f"⚠️ تحذير: فشل الدمج الاحتياطي النهائي للحالة: {e}")

    print("\n🎉 انتهت معالجة سير لونق.")


if __name__ == "__main__":
    sys.exit(main())
