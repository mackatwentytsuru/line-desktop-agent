import json

from line_agent.store import Store, dedupe_keys, iso_date
from line_agent.uia import Node
from line_agent.watcher import Watcher, detect_changes, stop_requested, with_keys


def N(ct, name="", cls="", children=()):
    return Node(name=name, class_name=cls, control_type=ct, children=list(children))


def msg(raw, date="2026年3月14日", time="午後 1:00"):
    return {"date": date, "time": time, "raw": raw, "text": raw, "kind": "text"}


def test_dedupe_repeat_and_reread(tmp_path):
    st = Store(tmp_path)
    a = [msg("同じ"), msg("同じ"), msg("別")]
    assert len(st.add_messages("c", a)) == 3  # real repeat kept
    assert st.add_messages("c", a) == []  # same screen again
    b = a + [msg("同じ"), msg("新")]
    assert [m["raw"] for m in st.add_messages("c", b)] == ["同じ", "新"]
    assert len(dedupe_keys("c", a)) == len(set(dedupe_keys("c", a)))


def test_old_rows_kept_when_window_shifts(tmp_path):
    st = Store(tmp_path)
    st.add_messages("c", [msg("一"), msg("二", time="午後 2:00")])
    st.add_messages("c", [msg("二", time="午後 2:00"), msg("三", time="午後 3:00")])
    assert [r["raw"] for r in st.recent("c", 10)] == ["一", "二", "三"]


def test_search_and_since(tmp_path):
    st = Store(tmp_path)
    st.add_messages("c", [msg("りんごを買う", date="2026年3月14日"), msg("みかん", date="2026年4月1日")])
    st.add_messages("d", [msg("りんご園", date="2026年4月2日")])
    assert len(st.search(["りんご"])) == 2
    assert len(st.search(["りんご"], chat="d")) == 1
    assert [r["raw"] for r in st.search(["りんご"], since="2026-04-01")] == ["りんご園"]
    assert st.search(["りんご", "買"])[0]["chat"] == "c"
    assert iso_date("2026年4月1日 (水)") == "2026-04-01"


def test_detect_changes():
    prev = {"甲": (1, "a", "午前 8:00"), "乙": (0, "b", "昨日")}
    chats = [
        {"name": "甲", "unread": 1, "preview": "a", "time": "午前 8:00"},
        {"name": "乙", "unread": 2, "preview": "b", "time": "昨日"},
        {"name": "丙", "unread": 0, "preview": "n", "time": "昨日"},
    ]
    prev = {"甲": prev["甲"], "乙": prev["乙"]}
    assert [c["name"] for c in detect_changes(prev, with_keys(chats))] == ["乙", "丙"]


class FakeBackend:
    def __init__(self):
        self.rows = []
        self.chat_msgs = None
        self.gone = False

    def find_windows(self, p, classes):
        if "AllInOneWindow" in classes:
            if self.gone:
                return []
            lst = N("List", cls="LcListView", children=[N("Button", r) for r in self.rows])
            return [N("Window", "LINE", "AllInOneWindow", [N("Group", children=[lst])])]
        if self.chat_msgs is None:
            return []
        lst = N("List", cls="LcListView", children=[N("Text", t) for t in self.chat_msgs])
        return [N("Window", "一括送信", "ChatWindow", [N("Group", cls="ChatMessageView", children=[lst])])]

    def snapshot(self, ref, d, n, t):
        return ref


def make(tmp_path, be):
    return Watcher(be, home=tmp_path, interval=0, fg=lambda: 7, sleep=lambda s: None,
                   title_of=lambda r: r.name)


def test_watcher_baseline_then_event_with_messages(tmp_path):
    be = FakeBackend()
    be.rows = ["一括送信, 0, hi, 午前 8:00", "他, 0, yo, 昨日"]
    w = make(tmp_path, be)
    be.chat_msgs = ["2026年3月14日", "前の発言, 午前 8:00"]  # window already open at the start
    assert w.tick() == []  # baseline: no events
    be.rows[0] = "一括送信, 1, 新着です, 午前 9:00"
    be.chat_msgs = ["2026年3月14日", "前の発言, 午前 8:00", "新着です, 午前 9:00"]
    ev = w.tick()
    assert len(ev) == 1 and ev[0]["chat"] == "一括送信" and ev[0]["unread"] == 1
    assert [m["text"] for m in ev[0]["new_messages"]] == ["新着です"]
    assert w.tick() == []  # no change
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["preview"] == "新着です"
    assert "fg=7->7" in (tmp_path / "watcher.log").read_text(encoding="utf-8")


def test_watcher_unopened_chat_still_event(tmp_path):
    be = FakeBackend()
    be.rows = ["他, 0, yo, 昨日"]
    w = make(tmp_path, be)
    w.tick()
    be.rows = ["他, 3, hey, 午前 9:00"]
    ev = w.tick()
    assert ev[0]["new_messages"] == [] and Store(tmp_path).recent() == []


def test_stop_file_and_line_missing(tmp_path):
    be = FakeBackend()
    be.gone = True
    w = make(tmp_path, be)
    assert w.run(max_ticks=2) == 0  # survives LINE not found
    assert "LINE not found" in (tmp_path / "watcher.log").read_text(encoding="utf-8")
    (tmp_path / "STOP").write_text("x")
    assert stop_requested(tmp_path)
    be.gone = False
    be.rows = ["a, 0, b, 昨日"]
    make(tmp_path, be).run()  # returns at once, no tick
    assert not (tmp_path / "events.jsonl").exists()
    assert Store(tmp_path).chat_states() == {}


def test_duplicate_names_do_not_flap(tmp_path):
    be = FakeBackend()
    be.rows = ["同名, 0, a, 昨日", "同名, 1, b, 昨日"]
    w = make(tmp_path, be)
    w.tick()
    assert w.tick() == [] and w.tick() == []
    be.rows[1] = "同名, 2, c, 午前 9:00"
    ev = w.tick()
    assert len(ev) == 1 and ev[0]["preview"] == "c"
