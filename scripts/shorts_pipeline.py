"""
سير عمل بوت "شورتس":
1. يفحص بوت تلجرام لأي روابط يوتيوب جديدة.
2. لكل رابط: يحمّل أول 60 ثانية فقط (قص مبدئي بسيط للاختبار - لاحقًا سيُستبدل بذكاء اصطناعي لاختيار أفضل مقطع).
3. يدمج المقطع داخل قالب الشورتس (assets/template.png) عبر ffmpeg.
4. ينشره على قناة يوتيوب كفيديو عام (public) — يوتيوب يتعرف عليه تلقائيًا كـ Shorts
   لأن القالب بأبعاد عمودية 1080x1920.
5. يرسل تأكيد في تلجرام وينظّف الملفات المؤقتة.
"""
import os
import subprocess
import sys
import traceback

import yt_dlp
from googleapiclient.http import MediaFileUpload

from telegram_utils import (
    fetch_all_new_messages,
    load_message_ledger,
    merge_latest_state,
    message_key,
    save_message_ledger,
    send_message,
)
from youtube_auth import get_youtube_client
import long_smart_pipeline

STATE_DIR = "state"
DOWNLOAD_DIR = "downloads_short"
BOT_NAME = "shorts"
TEMPLATE_PATH = "assets/template.png"

# القص المبدئي البسيط للاختبار: أول 60 ثانية
CLIP_START = 0
CLIP_END = 60

# إحداثيات منطقة الفيديو داخل القالب (نفس منطق المشروع الأصلي)
# TN, TH = أبعاد صورة القالب الأصلية | TX, TY = زاوية منطقة الفيديو | TW, THB = عرض/ارتفاع منطقة الفيديو
TEMPLATE_NATIVE_W, TEMPLATE_NATIVE_H = 941, 1672
TEMPLATE_BOX_X, TEMPLATE_BOX_Y = 95, 630
TEMPLATE_BOX_W, TEMPLATE_BOX_H = 750, 435
OUTPUT_W, OUTPUT_H = 1080, 1920


def download_clip(url: str, out_path: str) -> dict:
    ydl_opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "outtmpl": out_path,
        "download_ranges": yt_dlp.utils.download_range_func(None, [(CLIP_START, CLIP_END)]),
        "force_keyframes_at_cuts": True,
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


def apply_template(raw_path: str, final_path: str) -> bool:
    """يدمج المقطع داخل القالب. يرجع True إذا نجح."""
    if not os.path.exists(TEMPLATE_PATH):
        print("⚠️ لا يوجد قالب في assets/template.png — سيُرفع المقطع بدون قالب.")
        return False

    sx = OUTPUT_W / TEMPLATE_NATIVE_W
    sy = OUTPUT_H / TEMPLATE_NATIVE_H
    box_x = int(TEMPLATE_BOX_X * sx)
    box_y = int(TEMPLATE_BOX_Y * sy)
    box_w = int(TEMPLATE_BOX_W * sx)
    box_h = int(TEMPLATE_BOX_H * sy)

    cmd = [
        "ffmpeg", "-y",
        "-i", TEMPLATE_PATH,
        "-i", raw_path,
        "-filter_complex",
        f"[0:v]scale={OUTPUT_W}:{OUTPUT_H}[bg];"
        f"[1:v]scale={box_w}:{box_h}:force_original_aspect_ratio=increase,"
        f"crop={box_w}:{box_h}[vid];"
        f"[bg][vid]overlay={box_x}:{box_y}:format=auto",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "copy", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        final_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"⚠️ فشل دمج القالب: {result.stderr[-500:]}")
        return False
    return True


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
    bot_token = os.environ["SHORTS_BOT_TOKEN"]
    youtube_token_json = os.environ["YOUTUBE_TOKEN_JSON"]

    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    print("🔎 جاري فحص رسائل بوت شورتس الجديدة...")
    links, long_commands = fetch_all_new_messages(bot_token, STATE_DIR, BOT_NAME)

    ledger = load_message_ledger(STATE_DIR, BOT_NAME)
    unique_long_commands = []
    seen_command_keys = set()
    for item in long_commands:
        key = message_key(item)
        if key in seen_command_keys:
            continue
        seen_command_keys.add(key)
        record = ledger.get(key, {})
        if record.get("status") in {"succeeded", "upload_unknown", "processing"}:
            print(f"ℹ️ أمر /long مسجل مسبقًا ({record.get('status')})؛ تم تجاوزه: {key}")
            continue
        unique_long_commands.append(item)
    for key, record in ledger.items():
        if record.get("status") != "failed_retryable" or record.get("stage") != "analysis_failed" or key in seen_command_keys:
            continue
        if not record.get("source_url") or "chat_id" not in record or "message_id" not in record:
            continue
        seen_command_keys.add(key)
        unique_long_commands.append({
            "url": record["source_url"],
            "chat_id": record["chat_id"],
            "message_id": record["message_id"],
            "text": f"/long {record['source_url']}",
        })
    long_commands = unique_long_commands

    # نسجل أوامر /long قبل التحليل أيضًا؛ تكرار الرابط لا يسبب سحب transcript جديدًا.
    for item in long_commands:
        key = message_key(item)
        ledger[key] = {
            "status": "processing",
            "stage": "analyzing_long",
            "chat_id": item["chat_id"],
            "message_id": item["message_id"],
            "source_url": item["url"],
        }
    if long_commands:
        save_message_ledger(STATE_DIR, BOT_NAME, ledger)
    # لا نعالج نفس رسالة Telegram مرتين حتى لو عاد offset إلى الخلف أو تكرر الرابط.
    unique_links = []
    seen_keys = set()
    for item in links:
        key = message_key(item)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        record = ledger.get(key, {})
        if record.get("status") in {"succeeded", "upload_unknown"}:
            print(f"ℹ️ الرسالة مسجلة مسبقًا ({record['status']})؛ لن يعاد رفعها: {key}")
            continue
        if record.get("status") == "processing":
            print(f"⚠️ حالة الرفع غير محسومة لرسالة سابقة؛ تم تجاوزها بأمان: {key}")
            continue
        unique_links.append(item)
    # الـ offset يمنع Telegram من إعادة الرسالة؛ نعيد فقط ما فشل قبل الرفع.
    for key, record in ledger.items():
        if record.get("status") != "failed_retryable" or key in seen_keys:
            continue
        if not record.get("source_url") or "chat_id" not in record or "message_id" not in record:
            continue
        seen_keys.add(key)
        unique_links.append({
            "url": record["source_url"],
            "chat_id": record["chat_id"],
            "message_id": record["message_id"],
            "text": record.get("source_url", ""),
        })
    links = unique_links

    # أوامر /long <رابط>: تذهب لخط إنتاج منفصل تمامًا (ترانسكريبت → جيمناي → طابور 8 مقاطع)
    if long_commands:
        print(f"🧠 تم العثور على {len(long_commands)} أمر /long جديد.")
        long_results = long_smart_pipeline.handle_new_long_commands(
            bot_token=bot_token,
            long_commands=long_commands,
        )
        for item in long_commands:
            key = message_key(item)
            result = long_results.get(key, "failed_retryable")
            ledger[key]["status"] = result
            ledger[key]["stage"] = "queue_created" if result == "succeeded" else "analysis_failed"
        save_message_ledger(STATE_DIR, BOT_NAME, ledger)

    if not links:
        print("📭 لا توجد روابط شورتس عادية جديدة. إنهاء.")
        return

    print(f"📦 تم العثور على {len(links)} رابط جديد.")
    youtube = get_youtube_client(youtube_token_json)

    for idx, item in enumerate(links, 1):
        url = item["url"]
        chat_id = item["chat_id"]
        key = message_key(item)
        print(f"\n{'='*50}\n[{idx}/{len(links)}] معالجة: {url}")

        raw_path = os.path.join(DOWNLOAD_DIR, f"raw_{idx}.mp4")
        final_path = os.path.join(DOWNLOAD_DIR, f"final_{idx}.mp4")

        # الحجز قبل أي تنزيل يمنع تشغيلًا لاحقًا من إعادة معالجة الرسالة.
        ledger[key] = {
            "status": "processing",
            "stage": "downloading",
            "chat_id": chat_id,
            "message_id": item["message_id"],
            "source_url": url,
        }
        save_message_ledger(STATE_DIR, BOT_NAME, ledger)

        try:
            print(f"⬇️ تحميل أول {CLIP_END} ثانية...")
            info = download_clip(url, raw_path)
            title = info.get("title", "شورت جديد")
            desc = info.get("description", "") or ""

            actual_raw = raw_path
            if not os.path.exists(actual_raw):
                candidates = [
                    f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(f"raw_{idx}")
                ]
                if candidates:
                    actual_raw = os.path.join(DOWNLOAD_DIR, candidates[0])

            print("🖼️ دمج القالب...")
            merged = apply_template(actual_raw, final_path)
            upload_file = final_path if merged else actual_raw

            ledger[key]["stage"] = "uploading"
            save_message_ledger(STATE_DIR, BOT_NAME, ledger)
            print("📤 رفع على يوتيوب (public)...")
            video_id = upload_video(youtube, upload_file, f"{title} #Shorts", desc)
            video_url = f"https://youtu.be/{video_id}"
            print(f"✅ تم النشر: {video_url}")

            # نثبت نجاح الرفع قبل الإشعار؛ لن يعاد الرفع حتى لو فشل ما بعده.
            ledger[key] = {
                "status": "succeeded",
                "stage": "uploaded",
                "chat_id": chat_id,
                "message_id": item["message_id"],
                "source_url": url,
                "video_id": video_id,
                "video_url": video_url,
            }
            save_message_ledger(STATE_DIR, BOT_NAME, ledger)

            send_message(
                bot_token, chat_id, f"✅ تم نشر الشورت بنجاح:\n{video_url}"
            )

        except Exception as e:
            err = f"{e}"
            print(f"❌ فشل: {err}")
            traceback.print_exc()
            # قد يعني الخطأ أثناء الطلب أن YouTube استلم الفيديو دون رد واضح.
            # لا نعيد المحاولة تلقائيًا في هذه الحالة لتجنب فيديو مكرر.
            stage = ledger.get(key, {}).get("stage")
            ledger[key]["status"] = "upload_unknown" if stage == "uploading" else "failed_retryable"
            ledger[key]["error"] = err[:500]
            save_message_ledger(STATE_DIR, BOT_NAME, ledger)
            send_message(bot_token, chat_id, f"❌ فشلت معالجة الرابط:\n{url}\n\nالخطأ: {err[:300]}")

        finally:
            for f in os.listdir(DOWNLOAD_DIR):
                try:
                    os.remove(os.path.join(DOWNLOAD_DIR, f))
                except OSError:
                    pass

    # دمج احتياطي أخير مع أحدث state/ من origin/main (انظر نفس المنطق في
    # long_pipeline.py) لمنع فقدان تسجيلات نجاح بسبب تعارض push موازٍ.
    try:
        merge_latest_state(STATE_DIR, BOT_NAME)
    except Exception as e:
        print(f"⚠️ تحذير: فشل الدمج الاحتياطي النهائي للحالة: {e}")

    print("\n🎉 انتهت معالجة سير شورتس.")


if __name__ == "__main__":
    sys.exit(main())
