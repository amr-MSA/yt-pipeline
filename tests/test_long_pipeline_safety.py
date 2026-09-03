import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import telegram_utils


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
