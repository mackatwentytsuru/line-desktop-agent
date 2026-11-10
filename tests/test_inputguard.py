import pytest
from fakes_ui import FakeElem, FakeSys

from line_agent.inputguard import GuardConfig, GuardRefused, Target, run_guarded, wait_owner_idle

T = Target(100, "ChatWindow", "一括送信")


def cfg(tmp_path, **kw):
    return GuardConfig(tmp_path, **kw)


def nosleep(_):
    pass


def run(s, tmp_path, action, target=T, **kw):
    return run_guarded(s, target, cfg(tmp_path, **kw), action, sleep=nosleep)


def test_stop_file_refuses_before_anything(tmp_path):
    (tmp_path / "STOP").write_text("")
    s = FakeSys()
    with pytest.raises(GuardRefused, match="STOP"):
        run(s, tmp_path, lambda h: h.post_enter())
    assert s.acts == []


def test_no_idle_needed_by_default(tmp_path):
    s = FakeSys()
    s.last_input = s.tick  # the owner is typing in another app right now
    run(s, tmp_path, lambda h: h.post_enter())
    assert s.action_names() == ["post_enter"]


def test_optional_idle_wait_refuses_when_busy(tmp_path):
    s = FakeSys()
    s.last_input = s.tick - 500
    with pytest.raises(GuardRefused, match="using the PC"):
        run(s, tmp_path, lambda h: h.post_enter(), idle_s=3.0, idle_wait_s=1.0)
    assert s.acts == []


def test_wait_owner_idle_returns_when_idle():
    s = FakeSys()
    s.last_input = s.tick - 1000

    def sleep(dt):
        s.tick += int(dt * 1000)

    assert wait_owner_idle(s, 3.0, 10.0, 0.5, sleep) >= 3.0


@pytest.mark.parametrize("field,value,msg", [
    ("image", "explorer.exe", "not LINE.exe"),
    ("uia_class", "AllInOneWindow", "class"),
    ("title", "友だちA", "title"),
    ("enabled", False, "disabled"),
    ("exists", False, "no longer exists"),
])
def test_wrong_window_refuses(tmp_path, field, value, msg):
    s = FakeSys()
    s.windows[100][field] = value
    with pytest.raises(GuardRefused, match=msg):
        run(s, tmp_path, lambda h: h.post_enter())
    assert s.acts == []


def test_enter_only_to_verified_window_even_mid_action(tmp_path):
    s = FakeSys()

    def act(h):
        s.windows[100]["image"] = "explorer.exe"  # the handle now belongs to something else
        h.post_enter()

    with pytest.raises(GuardRefused, match="not LINE.exe"):
        run(s, tmp_path, act)
    assert "post_enter" not in s.action_names()


def test_popup_refuses(tmp_path):
    s = FakeSys()
    s.others = [(555, "LcDialog")]
    with pytest.raises(GuardRefused, match="popup"):
        run(s, tmp_path, lambda h: h.post_enter())
    assert s.acts == []


def test_target_in_front_refuses(tmp_path):
    s = FakeSys()
    s.fg = 100  # the owner is using this chat window
    with pytest.raises(GuardRefused, match="in front"):
        run(s, tmp_path, lambda h: h.post_enter())
    assert s.acts == []


def test_wheel_point_must_be_inside(tmp_path):
    s = FakeSys()
    with pytest.raises(GuardRefused, match="outside"):
        run(s, tmp_path, lambda h: h.wheel(5000, 50, 3))
    run(s, tmp_path, lambda h: h.wheel(50, 50, -3))
    assert ("wheel", 100, -3) in s.acts


@pytest.mark.parametrize("n", [0, 11, -11, 2.5])
def test_wheel_notches_bounded(tmp_path, n):
    s = FakeSys()
    with pytest.raises(GuardRefused, match="notches"):
        run(s, tmp_path, lambda h: h.wheel(50, 50, n))
    assert s.acts == []


def test_element_of_other_window_refused(tmp_path):
    s = FakeSys()
    e = FakeElem(root=777, cls="AutoSuggestTextArea")
    with pytest.raises(GuardRefused, match="not inside"):
        run(s, tmp_path, lambda h: h.set_text(e, "x"))
    assert s.acts == []


def test_take_back_only_our_exact_text(tmp_path):
    s = FakeSys()
    e = FakeElem(root=100, cls="AutoSuggestTextArea", value="本人の下書き")
    run(s, tmp_path, lambda h: h.take_back(e, ("テスト",)))
    assert e.value == "本人の下書き" and s.acts == []
    e.value = "テスト"
    run(s, tmp_path, lambda h: h.take_back(e, ("テスト",)))
    assert e.value == ""


def test_show_noactivate_only_when_iconic(tmp_path):
    s = FakeSys()
    run(s, tmp_path, lambda h: h.show_noactivate())
    assert s.acts == []
    s.windows[100]["iconic"] = True
    run(s, tmp_path, lambda h: h.show_noactivate())
    assert s.action_names() == ["show_noactivate"]


def test_line_taking_front_is_logged_and_given_back(tmp_path):
    s = FakeSys()

    def act(h):
        h.invoke(FakeElem(root=100, name="row"))
        s.fg = 100  # LINE brought its window to the front by itself

    _, log = run(s, tmp_path, act)
    assert 100 in log.line_front_seen and log.gave_back_front
    assert s.fg == 999 and log.fg_after == 999


def test_no_give_back_when_owner_moved_elsewhere(tmp_path):
    s = FakeSys()

    def act(h):
        h.post_enter()
        s.fg = 4242  # the owner switched to another app: leave it

    _, log = run(s, tmp_path, act)
    assert not log.gave_back_front and s.fg == 4242
