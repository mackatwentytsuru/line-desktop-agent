import pytest
from fakes_ui import FakeBackend, FakeElem, FakeSys, FakeWin

import line_agent.opener as op
from line_agent.inputguard import GuardConfig
from line_agent.opener import OpenRefused, open_chat

MAIN = 50
CHAT = 100
LIST_RECT = (0, 0, 700, 900)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(op, "OPEN_WAIT_S", 0.0)
    monkeypatch.setattr(op, "SEARCH_WAIT_S", 0.0)


def row(name, rect=(0, 100, 700, 200)):
    return FakeElem(MAIN, "", "ButtonControl", f"{name}, 1人, テストです！, 2026年11月10日", rect=rect)


def world(rows, chat_appears_on=("invoke",), search_value="", chat_open=False):
    s = FakeSys(hwnd=MAIN, title="LINE", cls="AllInOneWindow")
    lst = FakeElem(MAIN, "LcListView", "ListControl", rect=LIST_RECT, children=rows)
    box = FakeElem(MAIN, "LcTextField", "EditControl", value=search_value)
    main = FakeWin(MAIN, lst=lst, search=box)
    chat = FakeWin(CHAT)
    be = FakeBackend([], main)
    chat_state = {"exists": True, "image": "LINE.exe", "pid": 7, "uia_class": "ChatWindow", "title": "一括送信",
                  "visible": True, "enabled": True, "iconic": False, "rect": (0, 0, 10, 10)}

    def show():
        s.windows[CHAT] = dict(chat_state)
        if chat not in be.chats:
            be.chats.append(chat)

    if chat_open:
        show()

    def on_act(name, args):
        if name in chat_appears_on:
            show()

    s.on_act = on_act
    return s, be, box


def cfg(tmp_path):
    return GuardConfig(tmp_path)


def test_uia_press_opens(tmp_path):
    s, be, _ = world([row("友だちA", (0, 0, 700, 100)), row("一括送信")])
    out = open_chat(be, s, "一括送信", cfg(tmp_path))
    assert out["how"] == "row_press" and out["hwnd"] == CHAT
    assert ("invoke", "一括送信, 1人, テストです！, 2026年11月10日") in s.acts
    assert s.fg == 999


def test_already_shown_does_nothing(tmp_path):
    s, be, _ = world([row("一括送信")], chat_open=True)
    out = open_chat(be, s, "一括送信", cfg(tmp_path))
    assert out["already"] and s.acts == []


def test_duplicate_names_refused(tmp_path):
    s, be, _ = world([row("一括送信"), row("一括送信", (0, 200, 700, 300))])
    with pytest.raises(OpenRefused, match="2 chats"):
        open_chat(be, s, "一括送信", cfg(tmp_path))
    assert s.acts == []


def test_iconic_window_shown_without_activation(tmp_path):
    s, be, _ = world([row("一括送信")], chat_open=True)
    s.windows[CHAT]["iconic"] = True
    out = open_chat(be, s, "一括送信", cfg(tmp_path))
    assert out["how"] == "show_in_background" and s.action_names() == ["show_noactivate"]
    assert s.fg == 999


def test_row_press_that_takes_front_is_given_back(tmp_path):
    s, be, _ = world([row("一括送信")])
    prev = s.on_act

    def on_act(name, args):
        prev(name, args)
        if name == "invoke":
            s.fg = CHAT  # LINE brings the new window to the front by itself

    s.on_act = on_act
    out = open_chat(be, s, "一括送信", cfg(tmp_path))
    assert out["gave_back_front"] and CHAT in out["line_front_seen"] and s.fg == 999


def test_no_window_after_press_refused(tmp_path):
    s, be, _ = world([row("一括送信")], chat_appears_on=())
    with pytest.raises(OpenRefused, match="did not appear"):
        open_chat(be, s, "一括送信", cfg(tmp_path))
    assert s.action_names() == ["invoke"]


def test_offscreen_row_and_nonempty_search_refused(tmp_path):
    s, be, box = world([row("一括送信", (0, 2000, 700, 2100))], search_value="本人の検索")
    with pytest.raises(OpenRefused, match="search box is not empty"):
        open_chat(be, s, "一括送信", cfg(tmp_path))
    assert s.acts == [] and box.value == "本人の検索"


def test_missing_row_uses_search_value_then_clears(tmp_path):
    rows = []
    s, be, box = world(rows)

    def on_act(name, args):
        if name == "set_text" and args[0] == "一括送信":
            rows.append(row("一括送信"))
        if name == "invoke":
            s.windows[CHAT] = {"exists": True, "image": "LINE.exe", "pid": 7, "uia_class": "ChatWindow",
                               "title": "一括送信", "visible": True, "enabled": True, "iconic": False,
                               "rect": (0, 0, 10, 10)}
            be.chats.append(FakeWin(CHAT))

    s.on_act = on_act
    out = open_chat(be, s, "一括送信", cfg(tmp_path))
    assert out["via_search"] and out["how"] == "row_press"
    assert box.value == ""  # our search text taken back
    assert s.action_names() == ["set_text", "invoke", "set_text"]


def test_not_found_anywhere(tmp_path):
    s, be, box = world([])
    with pytest.raises(OpenRefused, match="even after search"):
        open_chat(be, s, "一括送信", cfg(tmp_path))
