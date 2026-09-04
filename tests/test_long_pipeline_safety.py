import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import telegram_utils


def test_extract_urls_accepts_multiple_platforms_and_cleans_punctuation():
    text = (
        "TikTok https://www.tiktok.com/@demo/video/123, "
        "Instagram https://www.instagram.com/reel/abc/?x=1. "
        "Vimeo https://vimeo.com/123456"
    )
    assert telegram_utils.extract_urls(text) == [
        "https://www.tiktok.com/@demo/video/123",
        "https://www.instagram.com/reel/abc/?x=1",
        "https://vimeo.com/123456",
    ]


def test_fetch_all_new_messages_accepts_non_youtube_url():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "chat": {"id": 7},
            "text": "https://www.tiktok.com/@demo/video/123",
        },
    }
    with patch.object(telegram_utils, "_fetch_updates", return_value=[update]):
        links, long_commands = telegram_utils.fetch_all_new_messages("token", "/tmp", "shorts")
    assert links[0]["url"] == "https://www.tiktok.com/@demo/video/123"
    assert long_commands == []


def test_fetch_all_new_messages_accepts_non_youtube_long_command():
    update = {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "chat": {"id": 7},
            "text": "/long https://vimeo.com/123456",
        },
    }
    with patch.object(telegram_utils, "_fetch_updates", return_value=[update]):
        links, long_commands = telegram_utils.fetch_all_new_messages("token", "/tmp", "shorts")
    assert links == []
    assert long_commands[0]["url"] == "https://vimeo.com/123456"


def test_duplicate_links_have_independent_message_keys():
    first = {"chat_id": 7, "message_id": 101, "url": "https://youtu.be/same"}
    second = {"chat_id": 7, "message_id": 102, "url": "https://youtu.be/same"}
    assert telegram_utils.message_key(first) != telegram_utils.message_key(second)


def test_upload_success_state_survives_delete_failure():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        state = {
            telegram_utils.message_key({"chat_id": 7, "message_id": 101}): {
                "chat_id": 7,
                "message_id": 101,
                "video_id": "abc123",
                "url": "https://youtu.be/abc123",
            }
        }
        telegram_utils.save_uploaded_messages(str(state_dir), "long", state)
        assert telegram_utils.load_uploaded_messages(str(state_dir), "long") == state

        with patch.object(telegram_utils.requests, "post", side_effect=telegram_utils.requests.RequestException):
            assert not telegram_utils.delete_message("token", 7, 101)
        assert telegram_utils.load_uploaded_messages(str(state_dir), "long") == state


def test_delete_requires_telegram_ok():
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": False}

    with patch.object(telegram_utils.requests, "post", return_value=Response()):
        assert not telegram_utils.delete_message("token", 7, 101)
