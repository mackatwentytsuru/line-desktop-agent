"""Chrome 版(CDP)の試験。にせものの CDP・ページだけを使う(実機の Chrome にはつながない)。"""
import json

import pytest
from fakes_chrome import BASE_MS, FakeBrowser, FakePage, download_events, rec

from line_agent import sendbook
from line_agent.chrome import chrome_actions as act
from line_agent.chrome import chrome_reader as cr
from line_agent.chrome.cdp import CdpClient, CdpError, CdpRefused
from line_agent.store import Store


# --- CDP クライアント ---
class FakeWs:
    def __init__(self, script):
        self.script, self.sent, self.timeout = list(script), [], 1

    def settimeout(self, t):
        self.timeout = t

    def send(self, data):
        self.sent.append(json.loads(data))

    def recv(self):
        if not self.script:
            import websocket

            raise websocket.WebSocketTimeoutException()
        item = self.script.pop(0)
        return json.dumps(item(self.sent[-1]) if callable(item) else item)

    def close(self):
        pass


def make_client(script):
    ws = FakeWs(script)
    return CdpClient("ws://127.0.0.1:9233/x", ws_factory=lambda url, **kw: ws), ws


def test_cdp_refuses_methods_outside_the_list():
    c, ws = make_client([])
    for bad in ("Page.bringToFront", "Target.activateTarget", "Network.getCookies", "Storage.getCookies",
                "IndexedDB.requestDatabase", "Input.dispatchTouchEvent", "Page.navigate"):
        with pytest.raises(CdpRefused):
            c.send(bad)
    assert ws.sent == []  # 何も送っていない


def test_cdp_refuses_non_local_url():
    with pytest.raises(CdpRefused):
        CdpClient("ws://192.168.1.5:9233/x", ws_factory=lambda *a, **k: None)


def test_cdp_matches_reply_and_buffers_events():
    c, ws = make_client([{"method": "Browser.downloadWillBegin", "params": {"guid": "g"}},
                         lambda sent: {"id": sent["id"], "result": {"ok": 1}}])
    assert c.send("Runtime.enable") == {"ok": 1}
    assert c.wait_event("Browser.downloadWillBegin")["guid"] == "g"


def test_cdp_error_and_page_exception():
    c, _ = make_client([lambda s: {"id": s["id"], "error": {"message": "boom"}}])
    with pytest.raises(CdpError, match="boom"):
        c.send("Runtime.enable")
    c, _ = make_client([lambda s: {"id": s["id"], "result": {"exceptionDetails": {"text": "x", "exception": {"description": "TypeError: y"}}}}])
    with pytest.raises(CdpError, match="TypeError"):
        c.evaluate("1")


# --- 読み取り ---
def test_to_message_shapes():
    ts = BASE_MS
    own = cr.to_message(rec("text", ts, "了解です", "reverse", "Me", readCount="既読 2"))
    assert (own["sender"], own["kind"], own["text"], own["read_mark"]) == ("self", "text", "了解です", True)
    assert own["raw"].endswith("既読 2") and own["date"].endswith("日") and own["time"] == "午後 4:30"
    other = cr.to_message(rec("text", ts, "こんにちは", "", "トークB"))
    assert (other["sender"], other["sender_name"]) == ("other", "トークB") and other["raw"].startswith("トークB, こんにちは")
    f = cr.to_message(rec("file", ts, fileName="資料.pdf", fileSize="サイズ : 1MB", expired=True))
    assert f["kind"] == "file" and f["text"] == "ファイル, 期限切れ, 資料.pdf" and f["expired"] is True
    v = cr.to_message(rec("video", ts, playTime="04:35"))
    assert v["text"] == "動画, 動画の長さ, 275秒"
    p = cr.to_message(rec("image", ts, imageMids=["1", "2", "3"]))
    assert (p["kind"], p["text"], p["n_images"]) == ("photo", "写真", 3)
    assert cr.to_message(rec("sticker", ts))["kind"] == "sticker"
    assert cr.to_message(rec("other:systemNotice", ts, text="だれかが参加しました"))["sender"] == "system"
    assert cr.to_message({"t": "date", "ts": ts}) is None


def test_messages_are_oldest_first_and_dates_dropped():
    recs = [rec("text", BASE_MS + 60_000, "あとの発言"), {"t": "date", "ts": BASE_MS, "label": "x"}, rec("text", BASE_MS, "さきの発言")]
    msgs = cr.messages_from_records(recs)
    assert [m["text"] for m in msgs] == ["さきの発言", "あとの発言"]


def test_store_dedupes_a_second_read(tmp_path):
    msgs = cr.messages_from_records([rec("text", BASE_MS + 5, "二つめ"), rec("text", BASE_MS, "ひとつめ")])
    st = Store(tmp_path)
    try:
        assert len(st.add_messages("トークB", msgs)) == 2
        assert len(st.add_messages("トークB", msgs)) == 0
    finally:
        st.close()


def test_read_open_chat_refuses_other_room():
    pg = FakePage(room="別のトーク", recs=[rec()])
    with pytest.raises(cr.ChromeChatNotOpen):
        cr.read_open_chat(pg, "トークB")


def test_list_chats_kinds_and_unread():
    out = cr.list_chats(FakePage())
    assert [c["kind"] for c in out["chats"]] == ["group", "friend"] and out["total_unread"] == 2


def test_read_chat_opens_when_needed():
    pg = FakePage(room="一括送信", recs=[rec(name="トークB")])
    pg.room = "一括送信"
    pg.recs = [rec(text="トークBの話")]
    msgs = cr.read_chat(pg, "トークB")
    assert "open_chat" in pg.calls and len(msgs) == 1


# --- 添付の選び方 ---
def media_msgs():
    recs = [
        rec("file", BASE_MS + 100_000, fileName="音源 2.wav", fileSize="サイズ : 75MB", expired=False),
        rec("file", BASE_MS, fileName="音源 2.wav", fileSize="サイズ : 75MB", expired=True),
        rec("image", BASE_MS + 50_000, imageMids=["a", "b"]),
        rec("video", BASE_MS + 10_000, playTime="01:00"),
    ]
    return cr.messages_from_records(recs)


def test_pick_needs_exactly_one_and_at_narrows():
    msgs = media_msgs()
    with pytest.raises(act.ChromeRefused, match="2 件"):
        act.pick_attachment(msgs, "音源 2.wav")
    from datetime import datetime

    at = datetime.fromtimestamp((BASE_MS + 100_000) / 1000).strftime("%Y-%m-%d %H:%M")
    assert act.pick_attachment(msgs, "音源 2.wav", at)["expired"] is False


def test_pick_refuses_expired_and_missing():
    msgs = media_msgs()
    from datetime import datetime

    at = datetime.fromtimestamp(BASE_MS / 1000).strftime("%Y-%m-%d %H:%M")
    # 時刻が分まで同じ 2 件のうち期限切れだけを指す: 期限切れは断る
    only_expired = [m for m in msgs if m.get("expired")]
    with pytest.raises(act.ChromeRefused, match="期限切れ"):
        act.pick_attachment(only_expired, "音源", at)
    with pytest.raises(act.ChromeRefused, match="ありません"):
        act.pick_attachment(msgs, "存在しない.pdf")


# --- 保存 ---
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20


def test_save_file_renames_and_hashes(tmp_path):
    pg = FakePage(room="トークB", recs=[rec("file", BASE_MS, fileName="a:b.pdf", fileSize="1MB", expired=False)])
    dest = tmp_path / "dl"
    dest.mkdir()
    br = FakeBrowser(download_events(dest, [b"%PDF-1.3 data"]))
    out = act.save_attachment(pg, "トークB", "a:b.pdf", dest=dest, browser_factory=lambda: br)
    saved = out["saved"][0]
    assert saved["path"].endswith("a_b.pdf") and saved["bytes"] == 13 and len(saved["sha256"]) == 64
    assert br.closed and br.sent[0][0] == "Browser.setDownloadBehavior"
    assert br.sent[0][1]["behavior"] == "allowAndName" and len(pg.clicked) == 1


def test_save_photos_get_numbered_names_with_extension(tmp_path):
    pg = FakePage(room="トークB", recs=[rec("image", BASE_MS, imageMids=["1", "2"])])
    dest = tmp_path / "dl"
    dest.mkdir()
    br = FakeBrowser(download_events(dest, [JPEG, JPEG + b"1"]))
    out = act.save_attachment(pg, "トークB", "写真", dest=dest, browser_factory=lambda: br)
    names = [s["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for s in out["saved"]]
    assert len(names) == 2 and names[0].endswith("_1.jpg") and names[1].endswith("_2.jpg")


def test_save_refuses_expired_before_clicking(tmp_path):
    pg = FakePage(room="トークB", recs=[rec("file", BASE_MS, fileName="x.pdf", expired=True)])
    br = FakeBrowser()
    with pytest.raises(act.ChromeRefused, match="期限切れ"):
        act.save_attachment(pg, "トークB", "x.pdf", dest=tmp_path, browser_factory=lambda: br)
    assert pg.clicked == []


def test_save_does_not_overwrite(tmp_path):
    (tmp_path / "x.pdf").write_bytes(b"old")
    pg = FakePage(room="トークB", recs=[rec("file", BASE_MS, fileName="x.pdf", expired=False)])
    br = FakeBrowser(download_events(tmp_path, [b"new"]))
    out = act.save_attachment(pg, "トークB", "x.pdf", dest=tmp_path, browser_factory=lambda: br)
    assert out["saved"][0]["path"].endswith("x (1).pdf") and (tmp_path / "x.pdf").read_bytes() == b"old"


def test_save_times_out_when_no_download_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(act, "DOWNLOAD_START_S", 0.0)
    pg = FakePage(room="トークB", recs=[rec("video", BASE_MS, playTime="00:10")])
    with pytest.raises(act.ChromeRefused, match="始まりません"):
        act.save_attachment(pg, "トークB", "動画", dest=tmp_path, browser_factory=lambda: FakeBrowser())


# --- 古い履歴 ---
def test_history_stops_at_date_or_top():
    pg = FakePage(room="トークB")
    old = int(__import__("time").time() * 1000) - 40 * 86_400_000
    pg.history_plan = [{"before": 10, "after": 20, "oldest_ts": BASE_MS, "visibility": "visible"},
                       {"before": 20, "after": 30, "oldest_ts": old, "visibility": "visible"}]
    from line_agent.store import iso_date

    out = act.load_history(pg, "トークB", iso_date(cr.jp_date(old)) )
    assert out["steps"] == 2 and "to_bottom" in pg.calls
    pg2 = FakePage(room="トークB")
    pg2.history_plan = [{"before": 5, "after": 5, "oldest_ts": BASE_MS, "visibility": "visible"}] * 3
    out2 = act.load_history(pg2, "トークB", None)
    assert out2["reached_top"] is True and out2["steps"] == 3


# --- 送信 ---
@pytest.fixture
def home(tmp_path):
    return tmp_path


def test_send_to_other_chat_refused_before_page_is_touched(home):
    pg = FakePage(room="トークB")
    with pytest.raises(act.ChromeRefused, match="送ってよい相手"):
        act.prepare(pg, home, "トークB", "こんにちは")
    with pytest.raises(act.ChromeRefused, match="送ってよい相手"):
        act.send(pg, home, "トークB", "こんにちは", "abcd")
    assert pg.calls == []


def test_stop_file_and_empty_text_and_unresolved_ledger(home):
    pg = FakePage()
    (home / "STOP").write_text("x")
    with pytest.raises(act.ChromeRefused, match="STOP"):
        act.prepare(pg, home, "一括送信", "あ")
    (home / "STOP").unlink()
    with pytest.raises(act.ChromeRefused, match="空"):
        act.prepare(pg, home, "一括送信", "  ")
    sendbook.Ledger(home).write("id1", "unknown", payload="x")
    with pytest.raises(act.ChromeRefused, match="結果の分からない"):
        act.prepare(pg, home, "一括送信", "あ")
    assert pg.calls == []


def test_draft_refuses_when_input_has_text(home):
    pg = FakePage(room="一括送信", raw="書きかけ")
    with pytest.raises(act.ChromeRefused, match="空ではありません"):
        act.prepare(pg, home, "一括送信", "あ")
    assert pg.raw == "書きかけ"  # 触っていない


def test_send_happy_path_ledger_sent(home):
    pg = FakePage(room="一括送信")
    d = act.prepare(pg, home, "一括送信", "テスト送信(作り話)")
    out = act.send(pg, home, "一括送信", "テスト送信(作り話)", d["confirm"], sleep=lambda s: None)
    assert out["sent"] is True and out["state"] == "sent" and pg.pressed == 1
    assert sendbook.Ledger(home).latest()[out["id"]]["state"] == "sent"


def test_send_token_is_single_use_and_bound_to_text(home):
    pg = FakePage(room="一括送信")
    d = act.prepare(pg, home, "一括送信", "あ")
    with pytest.raises(act.ChromeRefused):
        act.send(pg, home, "一括送信", "ちがう文面", d["confirm"], sleep=lambda s: None)
    assert pg.pressed == 0


def test_send_failed_when_enter_does_not_send_and_draft_is_cleared(home):
    pg = FakePage(room="一括送信", enter_sends=False)
    d = act.prepare(pg, home, "一括送信", "あ")
    out = act.send(pg, home, "一括送信", "あ", d["confirm"], sleep=lambda s: None)
    assert out["sent"] is False and out["state"] == "failed" and out["cleaned"] is True and pg.raw == ""
    # failed は次の送信を止めない(unknown だけが止める)
    assert sendbook.Ledger(home).any_unresolved() == []


def test_send_unknown_when_row_not_seen(home):
    pg = FakePage(room="一括送信")
    d = act.prepare(pg, home, "一括送信", "あ")

    def press_and_vanish():
        pg.pressed += 1
        pg.raw = ""  # 欄は空になったが、行は増えない

    pg.press_enter = press_and_vanish
    out = act.send(pg, home, "一括送信", "あ", d["confirm"], sleep=lambda s: None)
    assert out["state"] == "unknown" and len(sendbook.Ledger(home).any_unresolved()) == 1
    with pytest.raises(act.ChromeRefused, match="結果の分からない"):
        act.prepare(pg, home, "一括送信", "あ")


def test_send_stops_if_room_changes_before_typing(home):
    pg = FakePage(room="一括送信")
    d = act.prepare(pg, home, "一括送信", "あ")
    pg.room = "トークB"
    pg.open_chat = lambda name: pg.__setattr__("room", "トークB")  # 開き直しても違う
    with pytest.raises(act.ChromeRefused):
        act.send(pg, home, "一括送信", "あ", d["confirm"], sleep=lambda s: None)
    assert pg.raw == "" and pg.pressed == 0


def test_allowlist_is_only_what_config_says(home):
    (home / "config.json").write_text(json.dumps({"send_allowlist": ["トークB"]}), encoding="utf-8")
    act.prepare(FakePage(room="トークB"), home, "トークB", "あ")
    with pytest.raises(act.ChromeRefused, match="送ってよい相手"):
        act.prepare(FakePage(), home, "一括送信", "あ")


# --- 道の選び方 ---
def test_choose_backend_config_and_auto(tmp_path, monkeypatch):
    from line_agent.chrome import backend as be

    monkeypatch.delenv("LINE_AGENT_BACKEND", raising=False)
    monkeypatch.setattr(be, "chrome_state", lambda port=None: {"logged_in": True, "page": True, "port_answers": True})
    assert be.choose_backend(tmp_path)[0] == "chrome"
    monkeypatch.setattr(be, "chrome_state", lambda port=None: {"logged_in": False, "page": True, "port_answers": True})
    assert be.choose_backend(tmp_path)[0] == "pc"
    monkeypatch.setattr(be, "chrome_state", lambda port=None: {"logged_in": False, "page": False, "port_answers": False})
    assert "9233" in be.choose_backend(tmp_path)[1]
    (tmp_path / "config.json").write_text(json.dumps({"backend": "chrome"}), encoding="utf-8")
    assert be.choose_backend(tmp_path) == ("chrome", "設定で固定")
    monkeypatch.setenv("LINE_AGENT_BACKEND", "pc")
    assert be.choose_backend(tmp_path)[0] == "pc"


# --- MCP の Chrome 経路 ---
def tools(tmp_path, page):
    from line_agent import mcp_server as m

    return m.LineTools(home=tmp_path, chrome=lambda: page, chooser=lambda h: ("chrome", "テスト"),
                       procs=lambda: [])


def test_mcp_chrome_read_list_open_status(tmp_path):
    pg = FakePage(room="トークB", recs=[rec(text="作り話A"), rec(text="作り話B", ts=BASE_MS - 1000)])
    t = tools(tmp_path, pg)
    r = t.read_chat("トークB", 5)
    assert r["backend"] == "chrome" and r["read"] == 2 and r["new_saved"] == 2 and pg.c.closed
    assert t.read_chat("トークB", 5)["new_saved"] == 0
    assert t.list_chats(10)["backend"] == "chrome"
    assert t.open_chat("一括送信")["backend"] == "chrome"
    st = t.status()
    assert st["backend"] == "chrome" and st["attachment_save"] is True and st["not_supported"] == ""


def test_mcp_chrome_draft_send_and_save(tmp_path, monkeypatch):
    pg = FakePage(room="一括送信")
    t = tools(tmp_path, pg)
    d = t.draft_send("一括送信", "作り話の送信")
    assert d["backend"] == "chrome" and d["sent"] is False and d["token"]
    out = t.send("一括送信", "作り話の送信", d["token"])
    assert out["ok"] is True and out["state"] == "sent"
    with pytest.raises(act.ChromeRefused):
        t.draft_send("トークB", "x")
    pg2 = FakePage(room="トークB", recs=[rec("file", BASE_MS, fileName="x.pdf", expired=False)])
    dest = tmp_path / "dl"
    dest.mkdir()
    br = FakeBrowser(download_events(dest, [b"abc"]))
    monkeypatch.setattr(act, "connect_browser", lambda: br)
    monkeypatch.setattr(act, "downloads_dir", lambda: dest)
    s = tools(tmp_path, pg2).save_attachment("トークB", "x.pdf")
    assert s["ok"] is True and s["saved"][0]["bytes"] == 3
