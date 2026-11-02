import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from telegram_utils import parse_aggregate_command


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
