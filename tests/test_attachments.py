import pytest
from fakes_ui import FakeElem

from line_agent.attachments import AttachRefused, bar_state, file_name_of, find_in_downloads, media_kind, pick_row

ROWS = [
    "Aさん, ファイル, 期限切れ, 音源.wav, 午後 2:22",
    "Aさん, ファイル, 音源.wav, 午後 4:30",
    "Aさん, ファイル, 構成表.pdf, 午後 4:30",
    "Aさん, 写真, 午後 4:31",
    "Aさん, 写真, 午後 5:27",
    "Aさん, 動画, 動画の長さ, 275秒, 午後 6:39",
    "ありがとうございます！, 午後 4:31, 既読 8",
]


def rows():
    return [FakeElem(1, name=n) for n in ROWS]


def test_kinds_and_file_names():
    assert [media_kind(n) for n in ROWS] == ["ファイル", "ファイル", "ファイル", "写真", "写真", "動画", ""]
    assert file_name_of(ROWS[1]) == "音源.wav"
    assert file_name_of(ROWS[0]) == "音源.wav"
    assert file_name_of(ROWS[2]) == "構成表.pdf"


def test_exact_match_wins_over_partial():
    assert pick_row(rows(), "Aさん, 写真, 午後 4:31").Name == ROWS[3]


def test_partial_must_be_unique():
    with pytest.raises(AttachRefused, match="rows match"):
        pick_row(rows(), "写真")
    assert pick_row(rows(), "構成表").Name == ROWS[2]


def test_expired_refused():
    with pytest.raises(AttachRefused, match="期限切れ"):
        pick_row(rows(), ROWS[0])


def test_text_rows_are_not_media():
    with pytest.raises(AttachRefused, match="no photo"):
        pick_row(rows(), "ありがとうございます")
    with pytest.raises(AttachRefused, match="empty"):
        pick_row(rows(), "  ")


def test_bar_state():
    assert bar_state([(90, 124, 5), (146, 275, 5), (297, 332, 5), (354, 468, 5)]) == "not_saved"
    assert bar_state([(98, 192, 5), (213, 248, 5), (270, 384, 5)]) == "saved"
    assert bar_state([]) == "unknown"


def test_find_in_downloads(tmp_path):
    for n in ("構成表.pdf", "構成表 (1).pdf", "構成表.pdf.txt", "other.pdf"):
        (tmp_path / n).write_text("x")
    got = [p.split("\\")[-1].split("/")[-1] for p in find_in_downloads("構成表.pdf", tmp_path)]
    assert got == ["構成表 (1).pdf", "構成表.pdf"]
    assert find_in_downloads("", tmp_path) == []
