"""
سير عمل بوت "لونق":
1. يفحص بوت تلجرام لأي روابط يوتيوب جديدة أُرسلت إليه.
2. لكل رابط: يحمّل الفيديو كاملًا.
3. ينشره على قناة يوتيوب الخاصة بك كفيديو عام (public).
4. يرسل تأكيد نجاح/فشل في تلجرام، وينظّف الملفات المؤقتة.
"""
import os
import sys
import traceback

import yt_dlp
from googleapiclient.http import MediaFileUpload

from telegram_utils import fetch_new_links, send_message
from youtube_auth import get_youtube_client

STATE_DIR = "state"
DOWNLOAD_DIR = "downloads_long"
BOT_NAME = "long"


def download_video(url: str, out_path: str) -> None:
    ydl_opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "outtmpl": out_path,
        "overwrites": True,
        "quiet": True,
        "noprogress": True,
    }
    cookies_env = os.environ.get("YT_COOKIES")
    if cookies_env:
        cookie_path = os.path.join(STATE_DIR, "cookies.txt")
        with open(cookie_path, "w", encoding="utf-8") as f:
            f.write(cookies_env)
        ydl_opts["cookiefile"] = cookie_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
    return info


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
    return response["id"]


def main():
    bot_token = os.environ["LONG_BOT_TOKEN"]
    youtube_token_json = os.environ["YOUTUBE_TOKEN_JSON"]

    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

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
        print(f"\n{'='*50}\n[{idx}/{len(links)}] معالجة: {url}")
        raw_path = os.path.join(DOWNLOAD_DIR, f"video_{idx}.mp4")

        try:
            print("⬇️ تحميل الفيديو...")
            info = download_video(url, raw_path)
            title = info.get("title", "فيديو جديد")
            desc = info.get("description", "") or ""

            actual_file = raw_path
            if not os.path.exists(actual_file):
                # yt-dlp قد يضيف امتداد مختلف
                candidates = [
                    f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(f"video_{idx}")
                ]
                if candidates:
                    actual_file = os.path.join(DOWNLOAD_DIR, candidates[0])

            print("📤 رفع على يوتيوب (public)...")
            video_id = upload_video(youtube, actual_file, title, desc)
            video_url = f"https://youtu.be/{video_id}"
            print(f"✅ تم النشر: {video_url}")

            send_message(
                bot_token, chat_id, f"✅ تم نشر الفيديو بنجاح:\n{video_url}"
            )

        except Exception as e:
            err = f"{e}"
            print(f"❌ فشل: {err}")
            traceback.print_exc()
            send_message(bot_token, chat_id, f"❌ فشلت معالجة الرابط:\n{url}\n\nالخطأ: {err[:300]}")

        finally:
            # تنظيف فوري لأي ملفات مؤقتة لهذا العنصر
            for f in os.listdir(DOWNLOAD_DIR):
                try:
                    os.remove(os.path.join(DOWNLOAD_DIR, f))
                except OSError:
                    pass

    print("\n🎉 انتهت معالجة سير لونق.")


if __name__ == "__main__":
    sys.exit(main())
