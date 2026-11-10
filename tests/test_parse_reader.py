from line_agent import reader
from line_agent.parse import parse_chat_row, parse_message, parse_total_unread
from line_agent.uia import Node


def N(ct, name="", cls="", children=()):
    return Node(name=name, class_name=cls, control_type=ct, children=list(children))


def test_chat_row_with_unread():
    r = parse_chat_row("テスト太郎, 3, やあ, 午後 1:45")
    assert (r["name"], r["unread"], r["preview"], r["time"]) == ("テスト太郎", 3, "やあ", "午後 1:45")


def test_chat_row_without_unread():
    r = parse_chat_row("テスト会, ほい！, 20…")
    assert r["unread"] == 0 and r["name"] == "テスト会" and r["preview"] == "ほい！"


def test_chat_row_comma_in_preview():
    r = parse_chat_row("A, 2, 一つ, 二つ, 昨日")
    assert r["unread"] == 2 and r["preview"] == "一つ, 二つ" and r["time"] == "昨日"


def test_chat_row_raw_kept():
    assert parse_chat_row("x, 1, y, 午前 9:00")["raw"] == "x, 1, y, 午前 9:00"


def test_total_unread():
    assert parse_total_unread("トーク, 347") == 347
    assert parse_total_unread("トーク") == 0
    assert parse_total_unread("ホーム") is None


def test_messages():
    assert parse_message("2026年3月14日")["kind"] == "date"
    m = parse_message("こんにちは, 午後 1:45", "d")
    assert (m["text"], m["time"], m["kind"], m["date"]) == ("こんにちは", "午後 1:45", "text", "d")
    assert parse_message("写真, 午後 1:45")["kind"] == "photo"
    f = parse_message("ファイル, 期限切れ, x.pdf, 午後 1:39")
    assert f["kind"] == "file" and f["time"] == "午後 1:39"
    assert parse_message("動画, 動画の長さ, 29秒, 午後 7:39")["kind"] == "video"
    t = parse_message("時刻なしの本文")
    assert t["kind"] == "text" and t["time"] == ""


def _chat_tree():
    lst = N("List", cls="LcListView", children=[
        N("Text", "2026年3月14日"),
        N("Text", "ひとつめ, 午後 1:45"),
        N("Text", "写真, 午後 1:46"),
        N("Text", "2026年3月15日"),
        N("Text", "ふたつめ"),
    ])
    return N("Window", "一括送信", "ChatWindow", [
        N("Custom", cls="LcSplitter", children=[N("Group", cls="ChatMessageView", children=[lst])])
    ])


class FakeBackend:
    def __init__(self, tree):
        self.tree = tree

    def find_windows(self, p, c):
        return [self.tree]

    def snapshot(self, ref, d, n, t):
        return ref


def test_read_chat_last_and_dates():
    msgs = reader.read_chat(FakeBackend(_chat_tree()), "一括送信", 2, lambda r: r.name)
    assert [m["text"] for m in msgs] == ["写真", "ふたつめ"]
    assert msgs[1]["date"] == "2026年3月15日"


def test_read_chat_not_open():
    import pytest

    with pytest.raises(reader.ChatNotOpen):
        reader.read_chat(FakeBackend(_chat_tree()), "別の人", 5, lambda r: r.name)


def test_list_chats_tree():
    lst = N("List", cls="LcListView", children=[N("Button", "甲, 2, hi, 午前 8:00"), N("Button", "乙, yo, 昨日")])
    main = N("Window", "LINE", "AllInOneWindow", [N("TabItem", "トーク, 5"), N("Group", children=[lst])])
    out = reader.chats_from_tree(main)
    assert out["total_unread"] == 5
    assert [c["unread"] for c in out["chats"]] == [2, 0]


def test_chat_row_date_times():
    assert parse_chat_row("甲, 1, hi, 2026年11月9日")["time"] == "2026年11月9日"
    assert parse_chat_row("甲, hi, 11月10日")["time"] == "11月10日"
