import json

import pytest
from fakes_ui import FakeBackend, FakeSys, chat_with_input

from line_agent import sendbook
from line_agent.inputguard import GuardConfig
from line_agent.sender import SendRefused, prepare, send

TEXT = "テスト送信1(Claude)"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    import line_agent.sender as snd

    monkeypatch.setattr(snd, "SENT_WAIT_S", 0.05)


def setup(tmp_path, title="一括送信", value="", mangle=None):
    s = FakeSys(title=title)
    win, inp = chat_with_input(100, value, mangle)
    be = FakeBackend([win])
    rows = []

    def on_act(name, args):
        if name == "post_enter" and s.enter_empties:
            rows.append({"sender": "self", "text": inp.value, "raw": inp.value})
            inp.value = ""

    s.on_act = on_act
    return s, be, inp, rows


def gcfg(tmp_path, **kw):
    return GuardConfig(tmp_path, **kw)


def do_send(s, be, tmp_path, rows, title="一括送信", text=TEXT, token=None):
    if token is None:
        token = prepare(be, s, tmp_path, title, text)["confirm"]
    return send(be, s, tmp_path, title, text, token, gcfg(tmp_path), lambda w: list(rows),
                sleep=lambda _: None)


def ledger_states(tmp_path):
    return [e["state"] for e in sendbook.Ledger(tmp_path).entries()]


def test_happy_path_one_enter_and_sent(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    out = do_send(s, be, tmp_path, rows)
    assert out["sent"] and out["state"] == "sent"
    assert s.action_names().count("post_enter") == 1
    assert ledger_states(tmp_path) == ["sending", "sent"]
    assert s.fg == 999  # foreground put back


def test_wrong_title_refused_before_any_input(tmp_path):
    s, be, inp, rows = setup(tmp_path, title="友だちA")
    with pytest.raises(SendRefused, match="送ってよい相手ではありません"):
        prepare(be, s, tmp_path, "友だちA", TEXT)
    with pytest.raises(SendRefused, match="送ってよい相手ではありません"):
        send(be, s, tmp_path, "友だちA", TEXT, "whatever", gcfg(tmp_path), lambda w: [])
    assert s.acts == [] and ledger_states(tmp_path) == []


def test_allowlist_comes_only_from_config(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"send_allowlist": ["一括送信", "友だちA"]}), encoding="utf-8")
    assert sendbook.allowlist(sendbook.load_config(tmp_path)) == ("一括送信", "友だちA")
    assert sendbook.allowlist({}) == ()  # no built-in target: missing config = sending disabled
    (tmp_path / "config.json").write_text(json.dumps({"send_allowlist": []}), encoding="utf-8")
    s, be, inp, rows = setup(tmp_path)
    with pytest.raises(SendRefused, match="send_allowlist"):
        prepare(be, s, tmp_path, "一括送信", TEXT)


def test_non_empty_input_refused_and_untouched(tmp_path):
    s, be, inp, rows = setup(tmp_path, value="本人の書きかけ")
    with pytest.raises(SendRefused, match="not empty"):
        prepare(be, s, tmp_path, "一括送信", TEXT)
    assert s.acts == [] and inp.value == "本人の書きかけ"


def test_text_mismatch_refused_no_enter_and_cleaned(tmp_path):
    s, be, inp, rows = setup(tmp_path, mangle=lambda t: t[:-1])
    with pytest.raises(SendRefused, match="not what was confirmed"):
        do_send(s, be, tmp_path, rows)
    assert "post_enter" not in s.action_names()
    assert inp.value == ""
    assert ledger_states(tmp_path) == ["sending", "failed"]


def test_token_must_match_text(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    token = prepare(be, s, tmp_path, "一括送信", "別の文")["confirm"]
    with pytest.raises(SendRefused, match="different target or text"):
        do_send(s, be, tmp_path, rows, token=token)
    assert s.acts == []


def test_token_one_time(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    token = prepare(be, s, tmp_path, "一括送信", TEXT)["confirm"]
    do_send(s, be, tmp_path, rows, token=token)
    with pytest.raises(SendRefused, match="already used"):
        do_send(s, be, tmp_path, rows, token=token)


def test_token_expires(tmp_path):
    t = [1000.0]
    book = sendbook.TokenBook(tmp_path, clock=lambda: t[0])
    tok = book.issue("一括送信", TEXT)
    t[0] += sendbook.TOKEN_TTL_S + 1
    with pytest.raises(ValueError, match="expired"):
        book.consume(tok, "一括送信", TEXT)


def test_unknown_blocks_further_sends(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    out = send(be, s, tmp_path, "一括送信", TEXT, prepare(be, s, tmp_path, "一括送信", TEXT)["confirm"],
               gcfg(tmp_path), lambda w: [], sleep=lambda _: None)
    assert out["state"] == "unknown"
    with pytest.raises(SendRefused, match="unresolved"):
        prepare(be, s, tmp_path, "一括送信", "次の文")
    sendbook.Ledger(tmp_path).resolve(out["id"], "sent")
    assert prepare(be, s, tmp_path, "一括送信", "次の文")["confirm"]


def test_enter_not_sending_is_failed_and_cleaned(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    s.enter_empties = False
    out = do_send(s, be, tmp_path, rows)
    assert out["state"] == "failed" and inp.value == ""
    assert s.action_names().count("post_enter") == 1


def test_chat_in_front_refused_before_anything(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    token = prepare(be, s, tmp_path, "一括送信", TEXT)["confirm"]
    s.fg = 100  # the owner is using this chat window right now
    with pytest.raises(SendRefused, match="in front"):
        do_send(s, be, tmp_path, rows, token=token)
    assert s.acts == [] and ledger_states(tmp_path) == []


def test_never_brings_line_to_front(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    out = do_send(s, be, tmp_path, rows)
    assert out["line_front_seen"] == [] and not out["gave_back_front"]
    assert not {"give_back", "show_noactivate"} & set(s.action_names())


def test_stop_file(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    (tmp_path / "STOP").write_text("")
    with pytest.raises(SendRefused, match="STOP"):
        prepare(be, s, tmp_path, "一括送信", TEXT)
    assert s.acts == []


def test_two_windows_same_title_refused(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    win2, _ = chat_with_input(101)
    s.windows[101] = dict(s.windows[100])
    be.chats.append(win2)
    with pytest.raises(SendRefused, match="exactly one chat window"):
        prepare(be, s, tmp_path, "一括送信", TEXT)


def test_busy_owner_keeps_token_when_idle_wait_is_on(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    token = prepare(be, s, tmp_path, "一括送信", TEXT)["confirm"]
    s.last_input = s.tick  # the owner is typing right now
    with pytest.raises(SendRefused, match="using the PC"):
        send(be, s, tmp_path, "一括送信", TEXT, token, gcfg(tmp_path, idle_s=3.0, idle_wait_s=0.0),
             lambda w: list(rows), sleep=lambda _: None)
    assert s.acts == [] and ledger_states(tmp_path) == []
    assert do_send(s, be, tmp_path, rows, token=token)["sent"]  # default: no idle wait


def test_order_text_then_one_enter_no_mouse(tmp_path):
    s, be, inp, rows = setup(tmp_path)
    do_send(s, be, tmp_path, rows)
    assert s.action_names() == ["set_text", "post_enter"]
