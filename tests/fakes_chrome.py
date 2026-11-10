"""Chrome 版の試験用の、にせものの CDP とページ(本物のトークは入れない。文字はすべて作り話)。"""
from __future__ import annotations

import time
from pathlib import Path

BASE_MS = 1_794_295_800_000  # 2026-11-10 ごろ(日付は JST の昼間になるよう十分な余白)


class FakeClose:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def rec(kind="text", ts=BASE_MS, text="こんにちは", direction="", name="トークB", time_s="午後 4:30", **kw):
    """DOM 側の 1 件の記録(page.READ_JS の返り値の形)。"""
    base = {"t": "msg", "kind": kind, "ts": ts, "selectId": f"0{ts}-{ts % 997}", "direction": direction,
            "name": name, "time": time_s, "readCount": "", "mid": str(ts % 997), "saveButton": kind in ("image", "video", "file")}
    if kind in ("text", "reply"):
        base["text"] = text
    base.update(kw)
    return base


class FakePage:
    """LinePage の代わり。recs は新しい順(DOM と同じ)。"""

    def __init__(self, room="一括送信", recs=None, raw="", enter_sends=True, logged_in=True, chats=None):
        self.room = room
        self.recs = list(recs or [])
        self.raw = raw
        self.enter_sends = enter_sends
        self.logged_in = logged_in
        self.chats = chats or [{"name": "一括送信", "mid": "Caaa", "members": 1, "unread": 0, "preview": "x", "time": "午後 1:00"},
                               {"name": "トークB", "mid": "Ubbb", "members": None, "unread": 2, "preview": "y", "time": "午後 2:00"}]
        self.c = FakeClose()
        self.calls = []
        self.clicked = []
        self.history_plan = []
        self.pressed = 0

    def _log(self, name):
        self.calls.append(name)

    def status(self):
        self._log("status")
        return {"logged_in": self.logged_in, "room": self.room, "visibility": "visible", "has_focus": True}

    def require_login(self):
        self._log("require_login")
        if not self.logged_in:
            from line_agent.chrome.page import PageError

            raise PageError("LINE 拡張にログインしていません")

    def list_chats(self):
        self._log("list_chats")
        return self.chats

    def current_room(self):
        return self.room

    def open_chat(self, name):
        self._log("open_chat")
        from line_agent.chrome.page import PageError

        if name not in {c["name"] for c in self.chats}:
            raise PageError(f"トーク一覧に「{name}」がありません")
        self.room = name
        return {"ok": True}

    def read_records(self):
        self._log("read_records")
        return {"chat": self.room, "recs": self.recs}

    def history_step(self):
        self._log("history_step")
        return self.history_plan.pop(0)

    def to_bottom(self):
        self._log("to_bottom")

    def click_save(self, sid):
        self._log("click_save")
        self.clicked.append(sid)
        return {"ok": True}

    def input_state(self):
        return {"raw": self.raw, "empty": self.raw == ""}

    def insert_text(self, text):
        self._log("insert_text")
        self.raw += text

    def press_enter(self):
        self._log("press_enter")
        self.pressed += 1
        if self.enter_sends:
            ts = int(time.time() * 1000)
            self.recs.insert(0, rec("text", ts, self.raw, "reverse", "Me", "午後 4:31", readCount=""))
            self.raw = ""

    def backspace(self, n):
        self._log("backspace")
        self.raw = self.raw[: max(0, len(self.raw) - n)]


class FakeBrowser:
    """Browser.setDownloadBehavior / downloadWillBegin / downloadProgress を流す。"""

    def __init__(self, events=None):
        self.events = list(events or [])
        self.sent = []
        self.closed = False

    def send(self, method, params=None, timeout=None):
        self.sent.append((method, params))
        return {}

    def next_event(self, names, timeout=0.5):
        while self.events:
            ev = self.events.pop(0)
            if ev["method"] in names:
                return ev
        return None

    def close(self):
        self.closed = True


def download_events(folder: Path, blobs: list) -> list:
    """blobs = [bytes, ...] を GUID 名で folder に置いて、始まり・完了のイベントにする。"""
    evs = []
    for i, data in enumerate(blobs):
        guid = f"guid-{i}"
        path = Path(folder) / guid
        path.write_bytes(data)
        evs.append({"method": "Browser.downloadWillBegin", "params": {"guid": guid, "suggestedFilename": "blob"}})
        evs.append({"method": "Browser.downloadProgress",
                    "params": {"guid": guid, "state": "completed", "filePath": str(path), "totalBytes": len(data)}})
    return evs
