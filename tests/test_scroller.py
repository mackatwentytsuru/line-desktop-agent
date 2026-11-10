import pytest
from fakes_ui import FakeBackend, FakeElem, FakeSys, FakeWin

import line_agent.scroller as sc
from line_agent.inputguard import GuardConfig
from line_agent.scroller import ScrollRefused, load_history, scroll, to_bottom

H = 100
TITLE = "グループA"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(sc, "SETTLE_S", 0.0)


class View(FakeElem):
    def __init__(self, lst):
        super().__init__(H, "ChatMessageView", "GroupControl")
        self.lst = lst

    def ListControl(self, ClassName="", searchDepth=0):
        return self.lst


def world(older_batches):
    """older_batches: list of row-name lists, loaded one per wheel-up post."""
    s = FakeSys(hwnd=H, title=TITLE)
    rows = [FakeElem(H, name="2026年11月10日", rect=(0, 0, 10, 10)), FakeElem(H, name="こんにちは, 午後 1:00")]
    lst = FakeElem(H, "LcListView", "ListControl", rect=(0, 0, 500, 900), children=rows)
    win = FakeWin(H, panel=View(lst))
    be = FakeBackend([win])
    batches = list(older_batches)

    def on_act(name, args):
        if name == "wheel" and args[1] > 0 and batches:
            rows[0:0] = [FakeElem(H, name=n, rect=(0, -100, 10, -90)) for n in batches.pop(0)]

    s.on_act = on_act
    return s, be, rows


def cfg(tmp_path):
    return GuardConfig(tmp_path)


def test_scroll_up_posts_wheel_in_steps_of_ten(tmp_path):
    s, be, _ = world([])
    out = scroll(be, s, TITLE, cfg(tmp_path), 13)
    assert [a for a in s.acts if a[0] == "wheel"] == [("wheel", H, 10), ("wheel", H, 3)]
    assert out["line_front_seen"] == [] and s.fg == 999


def test_scroll_down(tmp_path):
    s, be, _ = world([])
    scroll(be, s, TITLE, cfg(tmp_path), -2)
    assert ("wheel", H, -2) in s.acts


def test_history_stops_at_date(tmp_path):
    s, be, rows = world([["2026年11月9日", "a"], ["2026年11月1日", "b"], ["2026年10月1日", "c"]])
    out = load_history(be, s, TITLE, cfg(tmp_path), "2026-11-05")
    assert out["stopped"] == "reached_date" and out["oldest_date"] == "2026-11-01"
    assert len([a for a in s.acts if a[0] == "wheel"]) == 2


def test_history_stops_at_top(tmp_path):
    s, be, rows = world([["2026年11月9日", "a"]])
    out = load_history(be, s, TITLE, cfg(tmp_path), None)
    assert out["stopped"] == "top"


def test_to_bottom_stops_when_last_row_settles(tmp_path):
    s, be, rows = world([])
    out = to_bottom(be, s, TITLE, cfg(tmp_path))
    assert out["steps"] <= 2


def test_no_window_refused(tmp_path):
    s, be, _ = world([])
    with pytest.raises(ScrollRefused, match="exactly one chat window"):
        scroll(be, s, "友だちA", cfg(tmp_path), 3)
    assert s.acts == []
