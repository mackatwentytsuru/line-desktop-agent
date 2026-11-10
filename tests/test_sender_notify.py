import json
import sqlite3

from PIL import Image

from line_agent import reader
from line_agent.parse import parse_chat_row, parse_message, resolve_senders
from line_agent.pixels import PixelProbe, classify_image
from line_agent.store import Store, dedupe_keys
from line_agent.uia import Node
from line_agent.watcher import Watcher, decide_notify

T = "テスト相手"


def N(ct, name="", cls="", children=(), rect=(0, 0, 0, 0), hwnd=0):
    return Node(name=name, class_name=cls, control_type=ct, children=list(children), rect=rect, hwnd=hwnd)


# ---- parse_message -------------------------------------------------------
def test_other_with_name_prefix():
    m = parse_message(f"{T}, ごはん, 午後 5:17", "d", T)
    assert (m["sender"], m["sender_name"], m["text"], m["time"], m["read_mark"]) == ("other", T, "ごはん", "午後 5:17", False)


def test_read_mark_gives_time_and_self():
    for tail in (", 既読", ", 既読 3"):
        m = parse_message("やあ, 午後 3:39" + tail, "d", T)
        assert (m["sender"], m["text"], m["time"], m["read_mark"], m["kind"]) == ("self", "やあ", "午後 3:39", True, "text")


def test_unread_own_looks_like_followup():
    m = parse_message("やあ, 午後 3:39", "d", T)
    assert m["sender"] == "unknown" and m["time"] == "午後 3:39" and not m["read_mark"]


def test_empty_body_and_marker_and_date():
    m = parse_message(f"{T}, , 午後 3:42", "d", T)
    assert m["kind"] == "sticker_or_image" and m["sender"] == "other" and m["text"] == ""
    assert parse_message("ここから未読メッセージ")["kind"] == "marker"
    assert parse_message("2026年11月10日")["kind"] == "date"
    assert parse_message(f"{T}, 写真, a.jpg, 午後 1:00", "", T)["kind"] == "photo"


def test_read_mark_beats_name_prefix():
    m = parse_message(f"{T}, と言った, 午後 3:39, 既読", "", T)
    assert m["sender"] == "self" and m["text"].startswith(T)


# ---- chat list rows ------------------------------------------------------
def test_group_row_clean_preview():
    r = parse_chat_row("会議室, 1人, テスト, 2026年11月10日")
    assert (r["kind"], r["members"], r["preview"], r["unread"], r["name"]) == ("group", 1, "テスト", 0, "会議室")
    r = parse_chat_row("会議室, 10人, 2, @だれか 見て, 昨日")
    assert (r["kind"], r["members"], r["unread"], r["preview"]) == ("group", 10, 2, "@だれか 見て")
    r = parse_chat_row("甲, 乙, 丙, 3人, 本文, 昨日")  # unnamed group
    assert r["name"] == "甲, 乙, 丙" and r["members"] == 3
    r = parse_chat_row("空の部屋, 14人")
    assert r["kind"] == "group" and r["preview"] == "" and r["time"] == ""


def test_openchat_row():
    r = parse_chat_row("オプチャ AI, 4,262, 309, 発言です, 2026年11月10日, オプチャ AI")
    assert (r["kind"], r["members"], r["unread"], r["preview"], r["time"], r["name"]) == (
        "openchat", 4262, 309, "発言です", "2026年11月10日", "オプチャ AI")


def test_friend_and_official_rows():
    assert parse_chat_row("甲, 2, hi, 午前 8:00")["kind"] == "friend"
    assert parse_chat_row("LINE, 6, おしらせ, 2026年11月5日")["kind"] == "official"


# ---- dedupe: gaining the read mark ---------------------------------------
def test_gaining_read_mark_not_stored_twice(tmp_path):
    st = Store(tmp_path)
    a = [parse_message("やあ, 午後 3:39", "d", T)]
    assert len(st.add_messages(T, a)) == 1
    b = [parse_message("やあ, 午後 3:39, 既読", "d", T)]
    assert dedupe_keys(T, a) == dedupe_keys(T, b)
    assert st.add_messages(T, b) == []
    rows = st.recent(T)
    assert len(rows) == 1 and rows[0]["sender"] == "self" and rows[0]["read_mark"] == 1


def test_real_repeat_still_kept(tmp_path):
    st = Store(tmp_path)
    ms = [parse_message("おけ, 午後 3:39", "d", T), parse_message("おけ, 午後 3:39", "d", T)]
    assert len(st.add_messages(T, ms)) == 2


# ---- migration -----------------------------------------------------------
def test_migration_merges_and_backs_up(tmp_path):
    db = sqlite3.connect(str(tmp_path / "line.db"))
    db.executescript(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat TEXT NOT NULL, date TEXT, date_iso TEXT,"
        " time TEXT, kind TEXT, text TEXT, raw TEXT, seen_at TEXT, dedupe_key TEXT NOT NULL UNIQUE);"
        "CREATE TABLE chats (name TEXT PRIMARY KEY, last_unread INTEGER, last_preview TEXT, last_time TEXT, updated_at TEXT);")
    old = [
        (T, "d", "", "text", "やあ, 午後 3:39", "やあ, 午後 3:39", "k1"),  # stored before the mark
        (T, "d", "", "text", "やあ, 午後 3:39, 既読", "やあ, 午後 3:39, 既読", "k2"),  # stored again after
        (T, "d", "午後 4:00", "text", f"{T}, へい", f"{T}, へい, 午後 4:00", "k3"),
        (T, "d", "午後 4:00", "text", "続き", "続き, 午後 4:00", "k4"),
    ]
    for r in old:
        db.execute("INSERT INTO messages(chat,date,time,kind,text,raw,dedupe_key) VALUES(?,?,?,?,?,?,?)", r)
    db.execute("INSERT INTO chats VALUES('x',0,'p','t','u')")
    db.commit()
    db.close()
    st = Store(tmp_path)
    assert (tmp_path / "line.db.bak_before_v2").exists()
    rows = st.recent(T, 10)
    assert len(rows) == 3
    assert rows[0]["sender"] == "self" and rows[0]["time"] == "午後 3:39" and rows[0]["text"] == "やあ"
    assert [r["sender"] for r in rows[1:]] == ["other", "other"]  # follow-up inherits, same minute
    assert st.chat_kinds() == {"x": None}
    # re-reading the same screen after migration adds nothing
    again = [parse_message("やあ, 午後 3:39, 既読", "d", T), parse_message(f"{T}, へい, 午後 4:00", "d", T),
             parse_message("続き, 午後 4:00", "d", T)]
    assert st.add_messages(T, again) == []
    Store(tmp_path)  # second open: no second migration, still 3 rows
    assert len(st.recent(T, 10)) == 3


# ---- resolve_senders -----------------------------------------------------
def msgs_of(*raws):
    return [parse_message(r, "d", T) for r in raws]


def test_owner_only_is_self():
    ms = resolve_senders(msgs_of("a, 午後 1:00", "b, 午後 1:01"), owner_only=True)
    assert [m["sender"] for m in ms] == ["self", "self"]


def test_inherit_same_minute_only():
    ms = resolve_senders(msgs_of(f"{T}, a, 午後 1:00", "b, 午後 1:00", "c, 午後 1:01"))
    assert [m["sender"] for m in ms] == ["other", "other", "unknown"]


def test_pixel_decides_and_beats_inherit():
    ms = msgs_of(f"{T}, a, 午後 1:00", "b, 午後 1:00", "c, 午後 1:05")
    resolve_senders(ms, pixel=lambda i: {1: "self", 2: "other"}[i])
    assert [m["sender"] for m in ms] == ["other", "self", "other"]
    ms = msgs_of("x, 午後 1:00")
    resolve_senders(ms, pixel=lambda i: "unknown")
    assert ms[0]["sender"] == "unknown"


def test_date_resets_chain():
    ms = resolve_senders(msgs_of(f"{T}, a, 午後 1:00", "2026年11月11日", "b, 午後 1:00"))
    assert ms[2]["sender"] == "unknown"


def test_header_button_owner_only():
    lst = N("List", cls="LcListView", children=[N("Text", "ひとり, 午後 1:00")])
    tree = N("Window", "部屋", "ChatWindow", [N("Button", "部屋, 1人"), N("Group", cls="ChatMessageView", children=[lst])])
    assert reader.messages_from_tree(tree, "部屋")[0]["sender"] == "self"
    tree2 = N("Window", "部屋", "ChatWindow", [N("Group", cls="ChatMessageView", children=[lst])])
    assert reader.messages_from_tree(tree2, "部屋")[0]["sender"] == "unknown"


def test_markers_and_dates_not_returned():
    lst = N("List", cls="LcListView", children=[N("Text", "2026年11月10日"), N("Text", "ここから未読メッセージ"),
                                                N("Text", f"{T}, やあ, 午後 1:00")])
    tree = N("Window", T, "ChatWindow", [N("Group", cls="ChatMessageView", children=[lst])])
    assert [m["text"] for m in reader.messages_from_tree(tree, T)] == ["やあ"]


# ---- pixels (synthetic images, invented) ---------------------------------
W, H = 400, 60
GREEN, WHITE, GREY = (133, 226, 73), (255, 255, 255), (60, 60, 60)


def bubble(x0, x1, color, ink=GREY):
    img = Image.new("RGB", (W, H), WHITE)
    for x in range(x0, x1):
        for y in range(10, 50):
            img.putpixel((x, y), color)
    for x in range(x0 + 5, x1 - 5):  # dark text
        for y in range(25, 30):
            img.putpixel((x, y), ink)
    return img


def test_classify_image():
    assert classify_image(bubble(260, 390, GREEN)) == "self"
    assert classify_image(bubble(30, 160, (240, 240, 240))) == "other"
    assert classify_image(Image.new("RGB", (W, H), WHITE)) == "unknown"
    assert classify_image(bubble(120, 300, (240, 240, 240))) == "unknown"  # centred: unclear


def test_probe_visibility_and_cover():
    img = Image.new("RGB", (W, 200), WHITE)
    img.paste(bubble(260, 390, GREEN), (0, 0))
    rect = (100, 100, 100 + W, 300)
    p = PixelProbe(7, rect, grab=lambda r: img, top_at=lambda x, y: 7)
    assert p.sender((100, 100, 500, 160)) == "self"
    assert p.sender((100, -500, 500, 110)) == "unknown"  # only 10 px visible
    covered = PixelProbe(7, rect, grab=lambda r: img, top_at=lambda x, y: 9)
    assert covered.sender((100, 100, 500, 160)) == "unknown"
    assert PixelProbe(0, rect, grab=lambda r: img).sender((100, 100, 500, 160)) == "unknown"
    assert PixelProbe(7, (0, 0, 0, 0), grab=lambda r: img, top_at=lambda x, y: 7).sender((0, 0, 0, 0)) == "unknown"


# ---- notify decision and watcher -----------------------------------------
def test_decide_notify():
    other = [{"kind": "text", "sender": "other"}]
    own = [{"kind": "text", "sender": "self"}]
    assert decide_notify("friend", 0, 0, other)[0] is True
    assert decide_notify("friend", 0, 0, [{"kind": "text", "sender": "unknown"}])[0] is True
    assert decide_notify("friend", 0, 0, own) == (False, "only own messages")
    assert decide_notify("openchat", 5, 0, other)[0] is False
    assert decide_notify("official", 5, 0, other)[0] is False
    assert decide_notify("group", 3, 2, [])[0] is True
    assert decide_notify("friend", 2, 2, [])[0] is False
    assert decide_notify("friend", 0, 3, [])[0] is False  # unread went down


class FakeBackend:
    def __init__(self):
        self.rows, self.chat_msgs, self.title = [], None, T

    def find_windows(self, p, classes):
        if "AllInOneWindow" in classes:
            lst = N("List", cls="LcListView", children=[N("Button", r) for r in self.rows])
            return [N("Window", "LINE", "AllInOneWindow", [N("Group", children=[lst])])]
        if self.chat_msgs is None:
            return []
        lst = N("List", cls="LcListView", children=[N("Text", t) for t in self.chat_msgs])
        return [N("Window", self.title, "ChatWindow", [N("Group", cls="ChatMessageView", children=[lst])])]

    def snapshot(self, ref, d, n, t):
        return ref


def make(tmp_path, be):
    return Watcher(be, home=tmp_path, interval=0, fg=lambda: 1, sleep=lambda s: None, title_of=lambda r: r.name,
                   start_date="2026-11-10")


def events(tmp_path):
    f = tmp_path / "events.jsonl"
    return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_watcher_notify_flow(tmp_path):
    be = FakeBackend()
    be.rows = [f"{T}, 0, a, 午後 1:00", "友, 0, b, 昨日", "広場, 100, 305, c, 昨日, 広場", "LINE, 0, d, 昨日"]
    be.chat_msgs = ["2026年11月10日", f"{T}, 古い, 午後 1:00"]
    w = make(tmp_path, be)
    assert w.tick() == []
    # a message of the other side arrives in the open window
    be.rows[0] = f"{T}, 0, 新しい, 午後 2:00"
    be.chat_msgs.append(f"{T}, 新しい, 午後 2:00")
    ev = w.tick()
    assert len(ev) == 1 and ev[0]["notify"] is True and ev[0]["reason"].startswith("new message")
    # the owner's own message: no notify
    be.rows[0] = f"{T}, 0, 返事, 午後 2:05"
    be.chat_msgs.append("返事, 午後 2:05, 既読")
    ev = w.tick()
    assert ev[0]["notify"] is False and ev[0]["reason"] == "only own messages"
    # unread rises in a chat whose window is closed
    be.rows[1] = "友, 2, 来たよ, 午後 3:00"
    ev = w.tick()
    assert ev[0]["notify"] is True and ev[0]["reason"] == "unread increased"
    # open chat and official: never
    be.rows[2] = "広場, 100, 306, 新, 午後 3:00, 広場"
    be.rows[3] = "LINE, 3, e, 午後 3:00"
    ev = w.tick()
    assert [e["notify"] for e in ev] == [False, False]
    assert {e["kind"] for e in ev} == {"openchat", "official"}
    # list-only change, unread not up
    be.rows[1] = "友, 2, 来たよ, 午後 3:05"
    assert w.tick()[0]["notify"] is False
    assert all("notify" in e for e in events(tmp_path))
    assert '"notify": true' in (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_watcher_newly_opened_window_is_baseline(tmp_path):
    be = FakeBackend()
    be.rows = [f"{T}, 0, a, 午後 1:00"]
    w = make(tmp_path, be)
    w.tick()
    be.chat_msgs = [f"{T}, 古い1, 午後 1:00", f"{T}, 古い2, 午後 1:01"]  # window opened now
    be.rows = [f"{T}, 0, a, 午後 1:01"]
    ev = w.tick()
    assert ev[0]["notify"] is False and ev[0]["new_messages"] == []
    assert len(Store(tmp_path).recent(T)) == 2  # still stored, silently


def test_watcher_old_rows_without_kind_do_not_fire(tmp_path):
    be = FakeBackend()
    be.rows = ["会議室, 1人, テスト, 昨日"]
    w = make(tmp_path, be)
    w.tick()
    w.store.db.execute("UPDATE chats SET ctype=NULL, last_preview='1人, テスト'")
    w.store.db.commit()
    assert w.tick() == []  # preview shape changed only
    assert w.store.chat_kinds()["会議室"] == "group"
