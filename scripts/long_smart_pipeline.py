"""
خط إنتاج "long-smart" — يُفعَّل عبر أمر /long <رابط> في بوت الشورتس.

مختلف تمامًا عن خط اللونق الفوري (long_pipeline.py) وخط الشورتس القديم
(القص الثابت 60 ثانية). هذا الخط:

المرحلة أ) عند وصول أمر /long <رابط> جديد:
    1) يسحب الترانسكريبت (نص + توقيت) من يوتيوب (transcript_utils).
    2) يرسله لجيمناي ليقترح بالضبط 8 مقاطع (نوع long/shorts، توقيت، عنوان، وصف)
       بصيغة JSON (gemini_utils).
    3) يحفظ الـ 8 مقاطع في ملف طابور خاص بهذا الفيديو (queue_utils)، كلها
       بحالة "pending".
    4) لا يُنشر أي شيء فورًا في هذه المرحلة — النشر يتم في المرحلة ب فقط.

المرحلة ب) عند كل تشغيل يومي مجدول (run_daily_batch):
    - يمر على كل الطوابير النشطة (فيها مقطع pending واحد على الأقل).
    - يأخذ **مقطعًا واحدًا (أول pending) من كل طابور نشط** وينشره،
      طالما لم نصل لسقف النشر اليومي المشترك لخط long-smart بالكامل
      (عداد منفصل تمامًا عن خط اللونق الفوري وخط الشورتس القديم).
    - عند اكتشاف خطأ كوتا من يوتيوب في أي لحظة، يتوقف فورًا عن أي محاولات
      نشر أخرى في هذا التشغيل ويسجّل ذلك.
    - العنوان والوصف النهائيان = عنوان/وصف جيمناي + توقيع ثابت من
      assets/fixed_title.txt و assets/fixed_description.txt.
"""
import os
import subprocess
import sys
import traceback

from googleapiclient.http import MediaFileUpload

from cookie_utils import temporary_cookie_file
import fixed_meta
import publish_quota
import queue_utils
from gemini_utils import suggest_clips
from telegram_utils import send_message
from transcript_utils import fetch_transcript
from youtube_auth import get_youtube_client

STATE_DIR = "state"
DOWNLOAD_DIR = "downloads_long_smart"
WORK_DIR = "work_long_smart"
DAILY_LIMIT = 8  # حد نشر يومي منفصل خاص بخط long-smart فقط

TEMPLATE_PATH = "assets/template.png"
TEMPLATE_NATIVE_W, TEMPLATE_NATIVE_H = 941, 1672
TEMPLATE_BOX_X, TEMPLATE_BOX_Y = 95, 630
TEMPLATE_BOX_W, TEMPLATE_BOX_H = 750, 435
SHORTS_OUTPUT_W, SHORTS_OUTPUT_H = 1080, 1920


# ---------------------------------------------------------------------------
# المرحلة أ: استقبال أوامر /long جديدة → ترانسكريبت → جيمناي → حفظ طابور
# ---------------------------------------------------------------------------

def handle_new_long_commands(bot_token: str, long_commands: list[dict]) -> dict[str, str]:
    results = {}
    gemini_api_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_api_key:
        for cmd in long_commands:
            results[f"{cmd['chat_id']}:{cmd['message_id']}"] = "failed_retryable"
            send_message(
                bot_token,
                cmd["chat_id"],
                "❌ لم يتم إعداد GEMINI_API_KEY على الخادم — لا يمكن معالجة أمر /long.",
            )
        print("❌ GEMINI_API_KEY غير موجود في متغيرات البيئة.")
        return results

    cookies_env = os.environ.get("YT_COOKIES")
    os.makedirs(WORK_DIR, exist_ok=True)

    for idx, cmd in enumerate(long_commands, 1):
        url = cmd["url"]
        chat_id = cmd["chat_id"]
        key = f"{chat_id}:{cmd['message_id']}"
        print(f"\n{'='*50}\n[/long {idx}/{len(long_commands)}] معالجة: {url}")

        try:
            print("📝 سحب الترانسكريبت من يوتيوب...")
            transcript = fetch_transcript(url, os.path.join(WORK_DIR, f"t{idx}"), cookies_env)
            video_id = transcript["video_id"]

            if queue_utils.queue_exists(STATE_DIR, video_id):
                results[key] = "succeeded"
                send_message(
                    bot_token, chat_id,
                    f"ℹ️ هذا الفيديو سبق معالجته بأمر /long، الطابور موجود مسبقًا:\n{url}",
                )
                continue

            print(f"✅ تم سحب {len(transcript['segments'])} جزء نصي. جاري إرسالها لجيمناي...")
            clips = suggest_clips(gemini_api_key, transcript["title"], transcript["segments"])
            print(f"✅ جيمناي اقترح {len(clips)} مقطع.")

            queue_utils.save_new_batch(
                state_dir=STATE_DIR,
                video_id=video_id,
                source_url=url,
                source_title=transcript["title"],
                chat_id=chat_id,
                clips=clips,
            )
            results[key] = "succeeded"

            send_message(
                bot_token, chat_id,
                f"✅ تم تحليل الفيديو بنجاح عبر جيمناي، وحُفظت {len(clips)} مقاطع مقترحة "
                f"في طابور النشر.\n\n"
                f"📌 سيُنشر مقطع واحد يوميًا من هذا الفيديو تلقائيًا (ضمن الحد اليومي)، "
                f"حتى تنتهي كل المقاطع.\n\nالفيديو المصدر: {transcript['title']}",
            )

        except Exception as e:
            err = f"{e}"
            print(f"❌ فشل تحليل /long: {err}")
            traceback.print_exc()
            results[key] = "failed_retryable"
            send_message(
                bot_token, chat_id,
                f"❌ فشلت معالجة أمر /long للرابط:\n{url}\n\nالخطأ: {err[:300]}",
            )

    return results


# ---------------------------------------------------------------------------
# المرحلة ب: التشغيل اليومي — نشر مقطع واحد من كل طابور نشط
# ---------------------------------------------------------------------------

def _download_clip_range(url: str, start: float, end: float, out_path: str, cookies_env: str | None) -> None:
    import yt_dlp

    ydl_opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "outtmpl": out_path,
        "download_ranges": yt_dlp.utils.download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
        "overwrites": True,
        "quiet": True,
        "noprogress": True,
    }
    with temporary_cookie_file(cookies_env) as cookie_path:
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)


def _apply_shorts_template(raw_path: str, final_path: str) -> bool:
    if not os.path.exists(TEMPLATE_PATH):
        print("⚠️ لا يوجد قالب شورتس — سيُرفع المقطع بدون قالب.")
        return False

    sx = SHORTS_OUTPUT_W / TEMPLATE_NATIVE_W
    sy = SHORTS_OUTPUT_H / TEMPLATE_NATIVE_H
    box_x = int(TEMPLATE_BOX_X * sx)
    box_y = int(TEMPLATE_BOX_Y * sy)
    box_w = int(TEMPLATE_BOX_W * sx)
    box_h = int(TEMPLATE_BOX_H * sy)

    cmd = [
        "ffmpeg", "-y",
        "-i", TEMPLATE_PATH,
        "-i", raw_path,
        "-filter_complex",
        f"[0:v]scale={SHORTS_OUTPUT_W}:{SHORTS_OUTPUT_H}[bg];"
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


def _upload(youtube, file_path: str, title: str, description: str) -> str:
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


def run_daily_batch() -> None:
    bot_token = os.environ.get("SHORTS_BOT_TOKEN")
    youtube_token_json = os.environ["YOUTUBE_TOKEN_JSON"]
    cookies_env = os.environ.get("YT_COOKIES")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    active_batches = queue_utils.list_active_batches(STATE_DIR)
    if not active_batches:
        print("📭 لا توجد طوابير long-smart نشطة اليوم. إنهاء.")
        return

    print(f"📦 عدد الطوابير النشطة: {len(active_batches)}")

    if not publish_quota.can_publish(STATE_DIR, DAILY_LIMIT):
        print(f"🚫 تم الوصول للحد اليومي لخط long-smart ({DAILY_LIMIT}). لا نشر اليوم.")
        return

    youtube = get_youtube_client(youtube_token_json)

    for batch_idx, batch in enumerate(active_batches, 1):
        if not publish_quota.can_publish(STATE_DIR, DAILY_LIMIT):
            print("🚫 تم الوصول للحد اليومي أثناء التشغيل. إيقاف باقي الدفعات.")
            break

        video_id = batch["video_id"]
        chat_id = batch["chat_id"]
        clip_idx = queue_utils.get_next_pending_index(batch)
        if clip_idx is None:
            continue

        clip = batch["clips"][clip_idx]
        done, total = queue_utils.batch_progress(batch)
        print(
            f"\n{'='*50}\n[طابور {batch_idx}/{len(active_batches)}] "
            f"فيديو {video_id} — مقطع {done + 1}/{total} (نوع: {clip['type']})"
        )

        raw_path = os.path.join(DOWNLOAD_DIR, f"raw_{video_id}_{clip_idx}.mp4")
        final_path = os.path.join(DOWNLOAD_DIR, f"final_{video_id}_{clip_idx}.mp4")

        try:
            print(f"⬇️ تحميل المقطع ({clip['start']}-{clip['end']} ثانية)...")
            _download_clip_range(batch["source_url"], clip["start"], clip["end"], raw_path, cookies_env)

            actual_raw = raw_path
            if not os.path.exists(actual_raw):
                candidates = [
                    f for f in os.listdir(DOWNLOAD_DIR)
                    if f.startswith(f"raw_{video_id}_{clip_idx}")
                ]
                if candidates:
                    actual_raw = os.path.join(DOWNLOAD_DIR, candidates[0])

            upload_file = actual_raw
            if clip["type"] == "shorts":
                print("🖼️ دمج قالب الشورتس...")
                merged = _apply_shorts_template(actual_raw, final_path)
                upload_file = final_path if merged else actual_raw

            final_title = fixed_meta.build_final_title(clip["title"])
            final_description = fixed_meta.build_final_description(clip["description"])

            title_for_upload = f"{final_title} #Shorts" if clip["type"] == "shorts" else final_title

            print("📤 رفع على يوتيوب (public)...")
            video_youtube_id = _upload(youtube, upload_file, title_for_upload, final_description)
            video_url = f"https://youtu.be/{video_youtube_id}"
            print(f"✅ تم النشر: {video_url}")

            queue_utils.mark_clip_published(STATE_DIR, video_id, clip_idx, video_url)
            publish_quota.record_publish_success(STATE_DIR)

            if bot_token:
                send_message(
                    bot_token, chat_id,
                    f"✅ نُشر مقطع {done + 1}/{total} من الفيديو المصدر "
                    f"\"{batch['source_title']}\" ({clip['type']}):\n{video_url}",
                )

        except Exception as e:
            err = f"{e}"
            print(f"❌ فشل: {err}")
            traceback.print_exc()

            if publish_quota.is_quota_error(e):
                print("🚫 تم اكتشاف خطأ كوتا يوتيوب — إيقاف كل النشر لهذا اليوم.")
                publish_quota.record_quota_hit(STATE_DIR)
                if bot_token:
                    send_message(
                        bot_token, chat_id,
                        "⚠️ تم الوصول لحد نشر يوتيوب اليومي (رصدناه من رسالة الخطأ). "
                        "سيتم إكمال النشر غدًا تلقائيًا.",
                    )
                queue_utils.mark_clip_failed(STATE_DIR, video_id, clip_idx, err)
                break

            queue_utils.mark_clip_failed(STATE_DIR, video_id, clip_idx, err)
            if bot_token:
                send_message(
                    bot_token, chat_id,
                    f"❌ فشل نشر أحد مقاطع \"{batch['source_title']}\":\n{err[:300]}",
                )

        finally:
            for f in os.listdir(DOWNLOAD_DIR):
                try:
                    os.remove(os.path.join(DOWNLOAD_DIR, f))
                except OSError:
                    pass

    print("\n🎉 انتهى التشغيل اليومي لخط long-smart.")


if __name__ == "__main__":
    sys.exit(run_daily_batch())
