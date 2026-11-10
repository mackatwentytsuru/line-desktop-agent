"""小さな CDP クライアント(websocket-client だけ)。

守り:
- 使えるメソッドは ALLOWED_METHODS の名簿だけ。名簿に無いもの(前に出す・Network・Storage・Cookie など)は送る前に断る。
- つなぐのは 127.0.0.1 だけ。
- 単一スレッド: send() の中で返事を待ち、その間に来たイベントは events に貯める。
"""
from __future__ import annotations

import json
import time
import urllib.request
from collections import deque
from typing import Callable, Optional

import websocket

DEFAULT_PORT = 9233
EXT_ID = "ophjlpahpchlmihnnnihgmmeilfjmjjc"
EXT_PREFIX = f"chrome-extension://{EXT_ID}/index.html"

ALLOWED_METHODS = frozenset({
    "Runtime.evaluate", "Runtime.enable",
    "Page.enable", "Page.getLayoutMetrics", "Page.captureScreenshot",
    "DOM.enable",
    "Input.dispatchKeyEvent", "Input.insertText",
    "Emulation.setFocusEmulationEnabled",
    "Browser.setDownloadBehavior", "Browser.getVersion",
    "Browser.getWindowForTarget", "Browser.getWindowBounds",
})


class CdpError(Exception):
    pass


class CdpRefused(CdpError):
    pass


def _local(url: str) -> bool:
    return url.startswith("ws://127.0.0.1:") or url.startswith("http://127.0.0.1:")


def http_json(port: int, path: str, timeout: float = 3.0):
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - 127.0.0.1 だけ
        return json.loads(r.read().decode("utf-8"))


def port_answers(port: int = DEFAULT_PORT) -> bool:
    try:
        http_json(port, "/json/version", 1.0)
        return True
    except Exception:  # noqa: BLE001
        return False


def find_line_page(port: int = DEFAULT_PORT) -> Optional[dict]:
    for t in http_json(port, "/json"):
        if t.get("type") == "page" and str(t.get("url", "")).startswith(EXT_PREFIX):
            return t
    return None


class CdpClient:
    def __init__(self, ws_url: str, timeout: float = 15.0, ws_factory: Callable = websocket.create_connection):
        if not _local(ws_url):
            raise CdpRefused("127.0.0.1 以外へはつなぎません")
        self.ws = ws_factory(ws_url, timeout=timeout, suppress_origin=True)
        self.timeout = timeout
        self._id = 0
        self.events: deque = deque(maxlen=2000)

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass

    def _recv(self, wait: float) -> Optional[dict]:
        self.ws.settimeout(max(wait, 0.01))
        try:
            return json.loads(self.ws.recv())
        except websocket.WebSocketTimeoutException:
            return None

    def send(self, method: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> dict:
        if method not in ALLOWED_METHODS:
            raise CdpRefused(f"CDP の {method} は名簿にありません")
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.time() + (timeout or self.timeout)
        while True:
            msg = self._recv(end - time.time())
            if msg is None:
                if time.time() >= end:
                    raise CdpError(f"{method} の返事が来ません")
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CdpError(f"{method}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def wait_event(self, name: str, pred: Callable[[dict], bool] = lambda p: True, timeout: float = 30.0) -> Optional[dict]:
        end = time.time() + timeout
        while True:
            for i, ev in enumerate(list(self.events)):
                if ev.get("method") == name and pred(ev.get("params", {})):
                    del self.events[i]
                    return ev["params"]
            if time.time() >= end:
                return None
            msg = self._recv(min(0.5, end - time.time()))
            if msg and "method" in msg:
                self.events.append(msg)

    def next_event(self, names: tuple, timeout: float = 0.5) -> Optional[dict]:
        """名前が names のどれかに当たる最初のイベントを返す(なければ None)。"""
        end = time.time() + timeout
        while True:
            for i, ev in enumerate(list(self.events)):
                if ev.get("method") in names:
                    del self.events[i]
                    return ev
            left = end - time.time()
            if left <= 0:
                return None
            msg = self._recv(min(0.5, left))
            if msg and "method" in msg:
                self.events.append(msg)

    def evaluate(self, expression: str, await_promise: bool = True, timeout: Optional[float] = None):
        """ページの中で式を評価し、値(JSON にできるもの)を返す。"""
        r = self.send("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                           "awaitPromise": await_promise}, timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise CdpError("ページの中のエラー: " + str(d.get("exception", {}).get("description", d.get("text")))[:300])
        return r.get("result", {}).get("value")


def connect_page(port: int = DEFAULT_PORT) -> CdpClient:
    t = find_line_page(port)
    if not t:
        raise CdpError("LINE 拡張のページが見つかりません(専用 Chrome は scripts/start_line_chrome.ps1 で起動)")
    return CdpClient(t["webSocketDebuggerUrl"])


def connect_browser(port: int = DEFAULT_PORT) -> CdpClient:
    return CdpClient(http_json(port, "/json/version")["webSocketDebuggerUrl"])
