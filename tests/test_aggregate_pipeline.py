import shutil
import subprocess
import tempfile
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from aggregate_pipeline import build_aggregate_video
from telegram_utils import parse_aggregate_command


URLS = [
    "https://youtu.be/MZzghRZsrgk?si=Uwa9DJEW7ozxN52",
    "https://youtu.be/sXuMjGfYwIw?si=bvoUSfxa3UD30gl5",
]


def _make_clip(path: Path, with_audio: bool, duration: float = 1, color: str = "blue") -> None:
    command = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d={duration}",
    ]
    if with_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.8"]
    command += ["-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        command += ["-c:a", "aac"]
    command += [str(path)]
    subprocess.run(command, check=True, capture_output=True)


def _duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def test_aggregate_watermark_is_top_right_legible_and_semitransparent():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        videos = root / "videos"
        videos.mkdir()
        _make_clip(videos / "plain.mp4", with_audio=False, duration=0.7, color="white")
        output = build_aggregate_video(
            [{"kind": "local", "value": "plain"}], str(videos), str(root / "work"), None
        )
        frame = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", "0.3", "-i", output, "-frames:v", "1",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True,
        ).stdout
        assert len(frame) == 1280 * 720 * 3

        def pixel(x: int, y: int) -> tuple[int, int, int]:
            offset = (y * 1280 + x) * 3
            return tuple(frame[offset:offset + 3])

        top_right_gray = sum(
            1 for y in range(0, 100) for x in range(1000, 1280)
            if 70 < min(pixel(x, y)) and max(pixel(x, y)) < 210
        )
        assert top_right_gray > 500, "لا تظهر خلفية العلامة شبه الشفافة أعلى اليمين"
        assert min(pixel(100, 100)) > 225, "ظهرت العلامة خارج المنطقة العلوية اليمنى"
        assert min(pixel(1270, 20)) > 225, "لا يحافظ الصندوق على هامش آمن من الحافة اليمنى"
        assert min(pixel(1270, 4)) > 225, "لا يحافظ الصندوق على هامش آمن من الحافة العليا"


def test_build_aggregate_adds_audio_to_silent_clip():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        videos = root / "videos"
        work = root / "work"
        videos.mkdir()
        _make_clip(videos / "1.mp4", with_audio=False, duration=2)
        _make_clip(videos / "2.mp4", with_audio=True, duration=2)
        _make_clip(videos / "3.mp4", with_audio=False, duration=2)
        output = build_aggregate_video(
            [
                {"kind": "local", "value": "1"},
                {"kind": "local", "value": "2"},
                {"kind": "local", "value": "3"},
            ],
            str(videos),
            str(work),
            None,
        )
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", output],
            check=True, capture_output=True, text=True,
        )
        assert probe.stdout.strip()
        assert _duration(output) >= 5.5


def test_builds_two_requested_links_in_order_and_optional_local_clip():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return
    title = "تجميعة سكتشات للأسطورة وسام قطب"
    remote = parse_aggregate_command(f'/agg "{title}" {URLS[0]} {URLS[1]}')
    assert [item["value"] for item in remote["sequence"]] == URLS

    downloaded = []

    def fake_download(url: str, out_path: str, _cookies_env: str | None) -> None:
        downloaded.append(url)
        _make_clip(Path(out_path), with_audio=True, duration=0.7)

    repo_videos = Path(__file__).parents[1] / "videos"
    assert (repo_videos / "2.mp4").is_file()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # The actual extractor is tested separately; this fixture verifies URL
        # ordering, normalization, and FFmpeg assembly without network or upload.
        with patch("aggregate_pipeline.download_clip", side_effect=fake_download):
            remote_only = build_aggregate_video(
                remote["sequence"], str(root / "no_local_files"), str(root / "work_remote"), None
            )
        assert downloaded == URLS
        assert _duration(remote_only) >= 1.3
        subprocess.run(["ffmpeg", "-v", "error", "-i", remote_only, "-f", "null", "-"], check=True)

        downloaded.clear()
        with_local = parse_aggregate_command(f'/agg "{title}" {URLS[0]} 2 {URLS[1]}')
        assert [item["kind"] for item in with_local["sequence"]] == ["remote", "local", "remote"]
        with patch("aggregate_pipeline.download_clip", side_effect=fake_download):
            result = build_aggregate_video(
                with_local["sequence"], str(repo_videos), str(root / "work_with_local"), None
            )
        assert downloaded == URLS
        assert _duration(result) >= 2.8
        subprocess.run(["ffmpeg", "-v", "error", "-i", result, "-f", "null", "-"], check=True)
