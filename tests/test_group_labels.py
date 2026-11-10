"""Group senders, system rows, volatile labels, stale messages (invented text)."""
import json
import sqlite3

from line_agent import reader
from line_agent.parse import learn_members, parse_message
from line_agent.store import Store, dedupe_keys
from line_agent.uia import Node
from line_agent.watcher import Watcher, decide_notify

G = "テスト会議"


def N(ct, name="", cls="", children=(), rect=(0, 0, 0, 0)):
    return Node(name=name, class_name=cls, control_type=ct, children=list(children), rect=rect)


def chat_tree(rows, title=G, header=None):
    lst = N("List", cls="LcListView", children=[N("Text", r) for r in rows])
    kids = [N("Group", cls="ChatMessageView", children=[lst])]
    if header:
        kids.insert(0, N("Button", header))
    return N("Window", title, "ChatWindow", kids)


ROWS = [
    "2026年11月10日",
    "甲, やあ, 午前 1:08",
    "乙 丙, 了解, 午後 4:02",
    "乙 丙, 続きだよ, 午後 4:03",
    "丁, ありがとう！, 午後 4:09",
    "甲のメッセージへの乙 丙のリプライ。甲の元のメッセージ：やあ。リプライ：はい。, 午後 4:04",
    "Meのメッセージへの丁のリプライ。Meの元のメッセージ：ok。リプライ：うん。, 午後 4:05",
    "Meのメッセージへ見た目のリプライ。Meの元のメッセージ：ok。リプライ：まる。, 既読 9",
    "自分の発言です, 午後 4:06, 既読 2",
    "戊がグループを退会しました。",
    "甲が己をグループに追加しました。",
    "グループ通話が開始されました, 参加",
    "グループ通話が終了しました。",
    ", ノートを見る",
]


def test_group_sender_rules():
    ms = reader.messages_from_tree(chat_tree(ROWS, header=f"{G}, 5人"), G)
    by = [(m["sender"], m["sender_name"], m["kind"]) for m in ms]
    assert by[0] == ("other", "甲", "text") and ms[0]["text"] == "やあ"
    assert by[1][:2] == ("other", "乙 丙") and by[2][:2] == ("other", "乙 丙")
    assert by[3][:2] == ("other", "丁")
    assert by[4][:2] == ("other", "乙 丙")  # reply: sender is the second name
    assert by[5][:2] == ("other", "丁")
    assert by[6][0] == "self"  # 既読 only
    assert by[7][0] == "self" and ms[7]["read_mark"]
    assert [b[0] for b in by[8:]] == ["system"] * 5 and {b[2] for b in by[8:]} == {"system"}
    assert not any(m["sender"] == "unknown" for m in ms)


def test_owner_reply_is_self_and_names_learned():
    m = parse_message("乙のメッセージへのMeのリプライ。乙の元のメッセージ：a。リプライ：b。, 午後 1:00", "", G, group=True)
    assert m["sender"] == "self" and m["names"] == ("乙", "Me")
    ms = [parse_message(r, "", G, group=True) for r in ("甲, a, 午後 1:00", "戊がグループを退会しました。")]
    assert learn_members(ms) == {"戊"}  # a single speaker is not yet confirmed


def test_unconfirmed_name_with_self_pixel_is_own_text():
    row = "ええと, まあ, いいか, 午後 1:00"  # own text with commas; name-like first field
    ms = reader.messages_from_tree(chat_tree([row], header=f"{G}, 3人"), G)
    assert ms[0]["sender"] == "other" and ms[0]["sender_name"] == "ええと"  # no pixels: guessed other

    class Probe:
        def sender(self, rect):
            return "self"

    ms = reader.messages_from_tree(chat_tree([row], header=f"{G}, 3人"), G, lambda root, lst: Probe())
    assert ms[0]["sender"] == "self" and ms[0]["text"] == "ええと, まあ, いいか"


def test_system_never_notifies():
    assert decide_notify("group", 1, 0, [{"kind": "system", "sender": "system"}])[0] is False
    assert decide_notify("group", 1, 0, [{"kind": "text", "sender": "other", "stale": True}])[0] is False
    assert decide_notify("group", 1, 0, [{"kind": "text", "sender": "other", "stale": False}])[0] is True


# ---- volatile labels ------------------------------------------------------
VID = "甲, 動画, 動画の長さ, 215秒, 午前 1:07"
VID2 = "甲, 動画, 動画の長さ, 215秒, 期限切れ, 午前 1:07"


def gm(raw, date="2026年11月10日"):
    return parse_message(raw, date, G, group=True)


def test_label_added_updates_row_not_new(tmp_path):
    st = Store(tmp_path)
    assert len(st.add_messages(G, [gm(VID)])) == 1
    assert dedupe_keys(G, [gm(VID)]) == dedupe_keys(G, [gm(VID2)])
    assert st.add_messages(G, [gm(VID2)]) == []
    rows = st.recent(G)
    assert len(rows) == 1 and "期限切れ" in rows[0]["raw"] and rows[0]["labels"] == "期限切れ"


def test_read_count_change_is_not_new(tmp_path):
    st = Store(tmp_path)
    a, b = "本文です, 午後 4:06, 既読 8", "本文です, 午後 4:06, 既読 9"
    assert len(st.add_messages(G, [gm(a)])) == 1
    assert st.add_messages(G, [gm(b)]) == []
    assert len(st.recent(G)) == 1 and st.recent(G)[0]["raw"] == b


def test_stale_flags(tmp_path):
    st = Store(tmp_path)
    st.add_messages(G, [gm("甲, 今, 午後 4:00")])
    new = st.add_messages(G, [gm("乙, 昔, 午後 3:00"), gm("乙, 先月, 午後 5:00", "2026年10月1日"), gm("乙, 次, 午後 4:30")],
                          floor_iso="2026-11-10")
    assert [m["stale"] for m in new] == [True, True, False]


# ---- migration v3 ---------------------------------------------------------
def test_migration_v3_merges_label_variants(tmp_path):
    st = Store(tmp_path)
    st.db.execute("INSERT INTO chats(name,ctype) VALUES(?, 'group')", (G,))
    for i, (raw, k) in enumerate(((VID, "old1"), (VID2, "old2"), ("乙, あ, 午後 1:00", "old3"), ("乙, あ, 午後 1:00", "old4"),
                                  ("戊がグループを退会しました。", "old5"))):
        st.db.execute("INSERT INTO messages(chat,date,date_iso,time,kind,text,raw,seen_at,dedupe_key,sender) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (G, "2026年11月10日", "2026-11-10", "午前 1:07" if i < 2 else "", "text", raw, raw, "t", k, "unknown"))
    st.db.execute("PRAGMA user_version=2")
    st.db.commit()
    st.migrate_v3()
    assert (tmp_path / "line.db.bak_before_v3").exists()
    rows = st.recent(G, 10)
    assert len(rows) == 4  # label pair merged, real repeat kept
    assert rows[0]["raw"] == VID2 and rows[0]["sender"] == "other" and rows[0]["text"].startswith("動画")
    assert rows[-1]["kind"] == "system" and rows[-1]["sender"] == "system"
    assert st.add_messages(G, [gm(VID2)]) == []


# ---- watcher --------------------------------------------------------------
class FB:
    def __init__(self):
        self.rows, self.msgs = [], []

    def find_windows(self, p, classes):
        if "AllInOneWindow" in classes:
            lst = N("List", cls="LcListView", children=[N("Button", r) for r in self.rows])
            return [N("Window", "LINE", "AllInOneWindow", [N("Group", children=[lst])])]
        return [chat_tree(self.msgs, header=f"{G}, 4人")]

    def snapshot(self, ref, d, n, t):
        return ref


def test_watcher_label_change_does_not_notify(tmp_path):
    be = FB()
    be.rows = [f"{G}, 4人, a, 午前 1:07"]
    be.msgs = ["2026年11月10日", "甲, 古い, 午前 1:00", "甲, 動画, 動画の長さ, 215秒, 午前 1:07"]
    w = Watcher(be, home=tmp_path, interval=0, fg=lambda: 1, sleep=lambda s: None, title_of=lambda r: r.name,
                start_date="2026-11-10")
    assert w.tick() == []
    be.rows = [f"{G}, 4人, b, 午前 1:08"]
    be.msgs = ["2026年11月10日", "甲, 古い, 午前 1:00", "甲, 動画, 動画の長さ, 215秒, 期限切れ, 午前 1:07"]
    ev = w.tick()
    assert ev[0]["new_messages"] == [] and ev[0]["notify"] is False
    be.rows = [f"{G}, 4人, c, 午前 1:09"]
    be.msgs.append("甲が乙をグループに追加しました。")
    ev = w.tick()
    assert ev[0]["notify"] is False and ev[0]["new_messages"][0]["sender"] == "system"
    be.rows = [f"{G}, 4人, d, 午前 1:10"]
    be.msgs.append("乙, 新着, 午前 1:10")
    assert w.tick()[0]["notify"] is True
