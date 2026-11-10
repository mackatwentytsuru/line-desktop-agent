"""MCP 道具の試験。本物の LINE は使わない(にせもの + 一時フォルダ)。"""
import pytest
from fakes_ui import FakeBackend, FakeSys, chat_with_input

from line_agent import mcp_server as m
from line_agent import reader, sendbook
from line_agent.store import Store

MSGS = [{"chat": "一括送信", "sender": "self", "sender_name": "", "date": "2026年11月10日(火)", "time": "午後 1:45",
         "kind": "text", "text": f"試験{i}", "raw": f"試験{i}, 午後 1:4{i}"} for i in range(5)]


def tools(tmp_path, backends=None, procs=lambda: []):
    return m.LineTools(home=tmp_path, backends=backends or (lambda: (FakeBackend([]), FakeSys())), procs=procs,
                       chooser=lambda home: ("pc", "テスト: PC 版に固定(実機の Chrome にはつながない)"))


def test_status_without_watcher(tmp_path):
    out = tools(tmp_path).status()
    assert out["ok"] and out["watcher_running"] is False and out["stop_file"] is False
    assert out["send_allowlist"] == ["一括送信"] and "添付" in out["not_supported"]


def test_status_finds_watcher_and_last_tick(tmp_path):
    (tmp_path / "watcher.log").write_text("t1 start\nt2 tick chats=3\nt3 note\n", encoding="utf-8")
    (tmp_path / "STOP").write_text("")
    procs = lambda: ["123|python.exe -m line_agent watch --interval 30", "9|python.exe other.py"]
    out = tools(tmp_path, procs=procs).status()
    assert out["watcher_pids"] == [123] and out["last_tick"] == "t2 tick chats=3" and out["stop_file"]


def test_recent_and_search_from_db(tmp_path):
    st = Store(tmp_path)
    st.add_messages("一括送信", MSGS)
    st.close()
    t = tools(tmp_path)
    r = t.recent("一括送信", 3)
    assert r["count"] == 3 and r["messages"][-1]["text"] == "試験4"
    s = t.search(["試験2"], chat="一括送信")
    assert s["count"] == 1 and set(s["messages"][0]) == set(m.ROW_KEYS)
    assert t.search("試験", since="2026-11-10")["count"] == 5


def test_validation_errors_are_plain_messages(tmp_path):
    t = tools(tmp_path)
    for fn in (lambda: t.search([]), lambda: t.search(["a"], since="11/10"), lambda: t.recent(None, 0),
               lambda: t.open_chat(" "), lambda: t.history("x", "bad")):
        out = m.guarded(fn)
        assert out["ok"] is False and "Traceback" not in out["error"]


def test_read_chat_saves_and_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "read_chat", lambda be, name, last, window_title: list(MSGS))
    out = tools(tmp_path).read_chat("一括送信", 3)
    assert out["read"] == 5 and out["new_saved"] == 5 and len(out["messages"]) == 3
    assert tools(tmp_path).read_chat("一括送信", 3)["new_saved"] == 0


def test_closed_chat_is_a_clear_error(tmp_path):
    out = m.guarded(lambda: tools(tmp_path).read_chat("友だちA", 3))
    assert out["ok"] is False and "line_open_chat" in out["error"]


def test_list_chats_limit(tmp_path, monkeypatch):
    chats = [{"name": f"c{i}", "kind": "friend", "unread": 0, "preview": "p", "time": "1:00", "extra": 1} for i in range(8)]
    monkeypatch.setattr(reader, "list_chats", lambda be: {"total_unread": 2, "chats": chats})
    out = tools(tmp_path).list_chats(5)
    assert out["count"] == 8 and len(out["chats"]) == 5 and "extra" not in out["chats"][0]


def fake_chat(title):
    s = FakeSys(title=title)
    win, inp = chat_with_input(100)
    return (lambda: (FakeBackend([win]), s)), s, inp


def test_draft_gives_token_and_does_not_act(tmp_path):
    backends, s, inp = fake_chat("一括送信")
    out = tools(tmp_path, backends).draft_send("一括送信", "テスト")
    assert out["ok"] and out["sent"] is False and out["token"] and out["target"] == "一括送信"
    assert s.acts == [] and inp.value == ""


def test_draft_to_other_chat_refused(tmp_path):
    backends, s, _ = fake_chat("友だちA")
    out = m.guarded(lambda: tools(tmp_path, backends).draft_send("友だちA", "x"))
    assert out["ok"] is False and out["refused"] and "許可" in out["error"]
    assert s.acts == []


def test_send_with_wrong_token_refused(tmp_path):
    backends, s, _ = fake_chat("一括送信")
    out = m.guarded(lambda: tools(tmp_path, backends).send("一括送信", "テスト", "bad"))
    assert out["ok"] is False and out["refused"] and s.action_names() == []


def test_send_happy_path_reads_back(tmp_path, monkeypatch):
    backends, s, inp = fake_chat("一括送信")
    rows = []

    def on_act(name, args):
        if name == "post_enter":
            rows.append({"sender": "self", "text": inp.value, "raw": inp.value})
            inp.value = ""

    s.on_act = on_act
    monkeypatch.setattr(reader, "messages_from_tree", lambda tree, title, probe_factory=None: list(rows))
    monkeypatch.setattr(FakeBackend, "snapshot", lambda self, *a, **k: None, raising=False)
    import line_agent.sender as snd
    monkeypatch.setattr(snd, "SENT_WAIT_S", 0.05)
    monkeypatch.setattr(snd.time, "sleep", lambda _: None)
    t = tools(tmp_path, backends)
    tok = t.draft_send("一括送信", "テスト")["token"]
    out = t.send("一括送信", "テスト", tok)
    assert out["ok"] and out["state"] == "sent" and s.action_names().count("post_enter") == 1
    assert [e["state"] for e in sendbook.Ledger(tmp_path).entries()] == ["sending", "sent"]


def test_no_forbidden_tools_registered(tmp_path):
    import asyncio

    names = {x.name for x in asyncio.run(m.build_server(tools(tmp_path)).list_tools())}
    assert names == {"line_list_chats", "line_read_chat", "line_recent", "line_search", "line_open_chat",
                     "line_history", "line_draft_send", "line_send", "line_status", "line_save_attachment"}
