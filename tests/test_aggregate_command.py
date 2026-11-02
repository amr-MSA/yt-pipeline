import sys
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from telegram_utils import parse_aggregate_command
from youtube_dedupe import recent_request_markers, request_marker


def test_aggregate_preserves_local_and_remote_order():
    result = parse_aggregate_command(
        '/agg "عنوان" 1 https://example.com/a 2 https://example.com/b '
        '2 https://example.com/c 3'
    )
    assert result["title"] == "عنوان"
    assert result["sequence"] == [
        {"kind": "local", "value": "1"},
        {"kind": "remote", "value": "https://example.com/a"},
        {"kind": "local", "value": "2"},
        {"kind": "remote", "value": "https://example.com/b"},
        {"kind": "local", "value": "2"},
        {"kind": "remote", "value": "https://example.com/c"},
        {"kind": "local", "value": "3"},
    ]


def test_aggregate_accepts_unquoted_title_and_mp4_tokens():
    result = parse_aggregate_command(
        "تجميع عنوان بلا تنصيص 1.mp4 https://example.com/a 2.mp4 "
        "https://example.com/b 3.mp4"
    )
    assert result["title"] == "عنوان بلا تنصيص"
    assert [item["value"] for item in result["sequence"]] == [
        "1", "https://example.com/a", "2", "https://example.com/b", "3"
    ]


def test_aggregate_requires_intro_and_outro():
    for command in (
        "/agg https://example.com/a 2 https://example.com/b 3",
        "/agg 1 https://example.com/a 2 https://example.com/b",
    ):
        try:
            parse_aggregate_command(command)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid aggregate command was accepted")


def test_aggregate_requires_separator_two():
    try:
        parse_aggregate_command("/agg 1 https://example.com/a 3")
    except ValueError as error:
        assert "الفاصل 2" in str(error)
    else:
        raise AssertionError("aggregate without separator was accepted")


def test_aggregate_has_stable_supported_dedupe_marker():
    assert request_marker("aggregate", "7:99") == "#3-7-99"


def test_youtube_reader_recognizes_aggregate_marker():
    class FakeRequest:
        def __init__(self, data):
            self.data = data

        def execute(self):
            return self.data

    class FakeYouTube:
        def channels(self):
            return type("Channels", (), {"list": lambda *_args, **_kwargs: FakeRequest({
                "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "up"}}}]
            })})()

        def playlistItems(self):
            return type("Playlist", (), {"list": lambda *_args, **_kwargs: FakeRequest({
                "items": [{"contentDetails": {"videoId": "vid"}}]
            })})()

        def videos(self):
            return type("Videos", (), {"list": lambda *_args, **_kwargs: FakeRequest({
                "items": [{"id": "vid", "snippet": {"description": "#3-7-99"}}]
            })})()

    assert recent_request_markers(FakeYouTube()) == {"#3-7-99": "vid"}


def test_uploading_state_is_a_retry_barrier_and_bad_record_does_not_raise():
    import long_pipeline

    item = {
        "chat_id": 7,
        "message_id": 99,
        "aggregate": {"title": "x", "sequence": []},
        "text": "/agg x",
    }
    uploaded = {"7:99": {"status": "uploading"}}
    results = {}
    unpublished = {}
    with patch.object(long_pipeline, "acknowledge_pending_link") as acknowledge:
        long_pipeline.process_aggregate_item(
            item, "token", None, uploaded, {}, results, unpublished
        )
        long_pipeline.process_aggregate_item(
            {"message_id": 100}, "token", None, uploaded, {}, results, unpublished
        )
    acknowledge.assert_called_once_with(long_pipeline.STATE_DIR, long_pipeline.BOT_NAME, item)
    assert "غير مؤكدة" in results[7][0]
