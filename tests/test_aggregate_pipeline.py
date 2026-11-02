import shutil
import subprocess
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from aggregate_pipeline import build_aggregate_video


def _make_clip(path: Path, with_audio: bool, duration: float = 1) -> None:
    command = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=320x180:d={duration}",
    ]
    if with_audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.8"]
    command += ["-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        command += ["-c:a", "aac"]
    command += [str(path)]
    subprocess.run(command, check=True, capture_output=True)


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
        duration_probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", output],
            check=True, capture_output=True, text=True,
        )
        assert float(duration_probe.stdout.strip()) >= 5.5
