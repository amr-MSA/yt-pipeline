import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import telegram_utils
from cookie_utils import temporary_cookie_file


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


def test_urls_with_embedded_credentials_are_rejected():
    assert telegram_utils.extract_urls("https://user:password@example.com/video/1") == []


def test_cookie_file_is_private_and_deleted_after_context():
    with temporary_cookie_file("# Netscape\nsecret-cookie") as path:
        assert path is not None
        assert Path(path).exists()
        assert (Path(path).stat().st_mode & 0o777) == 0o600
    assert not Path(path).exists()


def test_source_history_persists_published_url():
    with tempfile.TemporaryDirectory() as tmp:
        telegram_utils.record_source_success(tmp, "long", "https://example.com/video/1", "yt123")
        history = telegram_utils.load_source_history(tmp, "long")
        assert history["https://example.com/video/1"]["video_id"] == "yt123"


def test_boundary_marker_discards_links_before_it():
    updates = [
        {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 7}, "text": "https://example.com/old"}},
        {"update_id": 2, "message": {"message_id": 2, "chat": {"id": 7}, "text": "."}},
        {"update_id": 3, "message": {"message_id": 3, "chat": {"id": 7}, "text": "https://example.com/new"}},
    ]
    with patch.object(telegram_utils, "_fetch_updates", return_value=updates):
        links, _ = telegram_utils.fetch_all_new_messages("token", "/tmp", "shorts")
    assert [item["url"] for item in links] == ["https://example.com/new"]


def test_numeric_marker_is_boundary_and_marker_with_text_is_not():
    assert telegram_utils.is_boundary_marker("42")
    assert telegram_utils.is_boundary_marker("★")
    assert not telegram_utils.is_boundary_marker("batch 42")


def test_batch_summary_groups_items_by_chat():
    items = [
        {"chat_id": 7},
        {"chat_id": 7},
        {"chat_id": 8},
    ]
    with patch.object(telegram_utils, "send_message") as send:
        telegram_utils.send_batch_summary("token", items, "رابط")
    assert send.call_count == 2
    send.assert_any_call("token", 7, "📊 تم قبول 2 روابط للمعالجة في هذه الدفعة.")
    send.assert_any_call("token", 8, "📊 تم قبول 1 رابط للمعالجة في هذه الدفعة.")


def test_title_commands_update_the_previous_link():
    updates = [
        {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 7}, "text": "https://example.com/a"}},
        {"update_id": 2, "message": {"message_id": 2, "chat": {"id": 7}, "text": "/take"}},
        {"update_id": 3, "message": {"message_id": 3, "chat": {"id": 7}, "text": "https://example.com/b"}},
        {"update_id": 4, "message": {"message_id": 4, "chat": {"id": 7}, "text": "/t \"عنوان مخصص\""}},
        {"update_id": 5, "message": {"message_id": 5, "chat": {"id": 7}, "text": "/d"}},
    ]
    with patch.object(telegram_utils, "_fetch_updates", return_value=updates):
        links = telegram_utils.fetch_new_links("token", "/tmp", "long")
    assert links[0]["title_mode"] == "source"
    assert links[1]["title_mode"] == "default"
    assert links[1].get("title_override") is None


def test_pipeline_report_is_a_boundary_for_old_links():
    updates = [
        {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 7}, "text": "https://example.com/old"}},
        {"update_id": 2, "message": {"message_id": 2, "chat": {"id": 7}, "text": "📊 تقرير الدفعة — تم قبول 1 رابط:\nhttps://youtu.be/old"}},
        {"update_id": 3, "message": {"message_id": 3, "chat": {"id": 7}, "text": "https://example.com/new"}},
    ]
    with patch.object(telegram_utils, "_fetch_updates", return_value=updates):
        links = telegram_utils.fetch_new_links("token", "/tmp", "long")
    assert [item["url"] for item in links] == ["https://example.com/new"]


def test_legacy_success_message_is_a_boundary_too():
    updates = [
        {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 7}, "text": "https://example.com/old"}},
        {"update_id": 2, "message": {"message_id": 2, "chat": {"id": 7}, "text": "✅ تم نشر الفيديو بنجاح:\nhttps://youtu.be/old"}},
        {"update_id": 3, "message": {"message_id": 3, "chat": {"id": 7}, "text": "https://example.com/new"}},
    ]
    with patch.object(telegram_utils, "_fetch_updates", return_value=updates):
        links = telegram_utils.fetch_new_links("token", "/tmp", "long")
    assert [item["url"] for item in links] == ["https://example.com/new"]


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
