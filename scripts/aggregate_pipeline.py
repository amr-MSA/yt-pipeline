"""أدوات بناء فيديو تجميعي من ملفات videos/ وروابط الرسالة."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yt_dlp

from cookie_utils import temporary_cookie_file


def download_clip(url: str, out_path: str, cookies_env: str | None) -> None:
    options = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "overwrites": True,
        "quiet": True,
        "noprogress": True,
        "js_runtimes": {"deno": {}},
    }
    with temporary_cookie_file(cookies_env) as cookie_path:
        if cookie_path:
            options["cookiefile"] = cookie_path
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)


def _normalize(input_path: Path, output_path: Path) -> None:
    """يوحّد المقاس والصوت حتى يقبل FFmpeg المقاطع المختلفة في concat."""
    audio_probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(input_path)],
        capture_output=True, text=True,
    )
    has_audio = bool(audio_probe.stdout.strip())
    command = ["ffmpeg", "-y", "-i", str(input_path)]
    video_duration = None
    if not has_audio:
        duration_probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(input_path)],
            capture_output=True, text=True,
        )
        try:
            video_duration = float(duration_probe.stdout.strip())
        except ValueError:
            video_duration = None
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        if video_duration and video_duration > 0:
            command += ["-t", f"{video_duration:.6f}"]
    command += [
        "-map", "0:v:0", "-map", "1:a:0" if not has_audio else "0:a:0",
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
               "pad=1280:720:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p",
        "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k",
        "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"فشل توحيد الفيديو {input_path.name}: {result.stderr[-700:]}")


def build_aggregate_video(
    sequence: list[dict],
    videos_dir: str,
    work_dir: str,
    cookies_env: str | None,
) -> str:
    """يبني ملف mp4 نهائياً ويحافظ على ترتيب sequence كما ورد في الرسالة."""
    videos = Path(videos_dir)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    normalized: list[Path] = []

    for index, token in enumerate(sequence):
        source = token["value"]
        if token["kind"] == "local":
            source_path = videos / f"{source}.mp4"
            if not source_path.is_file():
                raise FileNotFoundError(
                    f"الملف المحلي videos/{source}.mp4 غير موجود. أضفه إلى المستودع ثم أعد المحاولة."
                )
        else:
            source_path = work / f"source_{index}.mp4"
            download_clip(source, str(source_path), cookies_env)
            if not source_path.is_file():
                candidates = sorted(work.glob(f"source_{index}.*"))
                if candidates:
                    source_path = candidates[0]
        if not source_path.is_file():
            raise FileNotFoundError(f"لم ينتج ملف للفيديو رقم {index + 1}.")
        target = work / f"normalized_{index:03d}.mp4"
        _normalize(source_path, target)
        normalized.append(target)

    concat_file = work / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{path.as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for path in normalized)
        + "\n",
        encoding="utf-8",
    )
    output = work / "aggregate_final.mp4"
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-c", "copy", "-movflags", "+faststart", str(output)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"فشل دمج المقاطع: {result.stderr[-900:]}")
    return str(output)
