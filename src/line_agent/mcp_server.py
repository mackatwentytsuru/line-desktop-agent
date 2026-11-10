"""LINE for PC を Claude Code から使うための MCP サーバー(stdio、このリポジトリのプロジェクト専用)。

UI の動かし方はここに書かない。既にある部品(reader / store / opener / scroller / sender / sendbook)を
呼ぶだけの薄い包み。削除・転送・通報・設定の道具は作らない。
道(backend)は 2 つ: pc(デスクトップ版 + UIA)と chrome(Chrome 拡張 + CDP。添付の保存ができる)。選び方は chrome/backend.py。
送信は sender.py の決まり(許可リストの相手だけ・下見→合言葉→本番・STOP・台帳)をそのまま通る。

どの道具も dict を返す。失敗は {"ok": False, "error": "..."} で、トレースバックは返さない。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from . import reader, sendbook
from .store import Store, claude_virtual_home, home_dir

MAX_ROWS = 200
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NOT_SUPPORTED = "添付(写真・ファイル)の保存は PC 版ではできません(UIA に保存の部品が出ず、マウスを使わない決まりのため)。Chrome 版(backend=chrome)ならできます。"
ROW_KEYS = ("chat", "sender", "sender_name", "date", "time", "kind", "text")


def resolve_home() -> Path:
    """見張り(scripts/start_watcher.ps1)と同じ置き場所を選ぶ。
    LINE_AGENT_HOME があればそれ。なければ Claude アプリ(MSIX)の実際の置き場所があればそれ、なければ home_dir()。"""
    env = os.environ.get("LINE_AGENT_HOME")
    if env:
        return Path(env)
    virtual = claude_virtual_home()
    if virtual is not None:
        return virtual
    return home_dir()


def _clean(obj):
    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


def _row(r: dict) -> dict:
    return {k: r.get(k, "") for k in ROW_KEYS}


def _int(v, lo: int, hi: int, name: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} は整数で指定してください") from None
    if not lo <= n <= hi:
        raise ValueError(f"{name} は {lo}〜{hi} の範囲で指定してください")
    return n


def _name(v, label: str = "name") -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{label} が空です")
    return v


def _iso(v: Optional[str], label: str) -> Optional[str]:
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _DATE.match(v):
        raise ValueError(f"{label} は YYYY-MM-DD で指定してください")
    return v


def watcher_commands() -> list:
    """python の起動中のコマンドラインの一覧(見張りを探すため)。"""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
          "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else str(r.stdout)
    return [ln.strip() for ln in out.splitlines() if "|" in ln]


def _real_backends():
    from .inputguard import Win32System, set_dpi_aware
    from .uia import UiaBackend

    set_dpi_aware()
    return UiaBackend(), Win32System()


def _real_chrome():
    from .chrome.page import open_session

    return open_session()


def _real_chrome_state() -> dict:
    from .chrome.backend import chrome_state

    return chrome_state()


def _real_choose(home):
    from .chrome.backend import choose_backend

    return choose_backend(home)


class LineTools:
    """道具の本体。backends() と procs はテストで差し替える。"""

    def __init__(self, home: Optional[Path] = None, backends: Callable = _real_backends,
                 procs: Callable = watcher_commands, chrome: Optional[Callable] = None,
                 chooser: Optional[Callable] = None):
        self.home = Path(home) if home else resolve_home()
        if not home:  # 本番: 自分の表示名は、実際に使うデータ置き場の config.json(self_names)から
            try:
                from .parse import set_owner_names
                set_owner_names(sendbook.load_config(self.home).get("self_names") or [])
            except (OSError, ValueError):
                pass
        self.backends = backends
        self.procs = procs
        self.chrome = chrome or _real_chrome  # () -> LinePage(閉じるのは page.c.close())
        self.chooser = chooser or _real_choose  # (home) -> (backend, why)
        self.lock = threading.Lock()  # UI を触るのは 1 度に 1 つ

    def backend(self) -> str:
        return self.chooser(self.home)[0]

    @contextlib.contextmanager
    def _page(self):
        page = self.chrome()
        try:
            yield page
        finally:
            page.c.close()

    # --- 添付 ---
    def save_attachment(self, chat: str, match: str, at: Optional[str] = None) -> dict:
        _name(chat)
        if not isinstance(match, str) or not match.strip() or len(match) > 300:
            raise ValueError("match は 1〜300 文字")
        if self.backend() == "chrome":
            from .chrome import chrome_actions as act

            with self._page() as page:
                return {"ok": True, "backend": "chrome", **act.save_attachment(page, chat, match, at)}
        be, sys_ = self.backends()
        from .attachments import check

        return {"ok": True, **check(be, sys_, chat, match)}

    # --- 読む ---
    def list_chats(self, limit: int = 50) -> dict:
        n = _int(limit, 1, MAX_ROWS, "limit")
        if self.backend() == "chrome":
            from .chrome import chrome_reader as cr

            with self._page() as page:
                out = cr.list_chats(page)
            chats = [{k: c.get(k, "") for k in ("name", "kind", "unread", "preview", "time")} for c in out["chats"]]
            return {"ok": True, "backend": "chrome", "total_unread": out["total_unread"], "count": len(chats),
                    "chats": chats[:n]}
        be, _ = self.backends()
        out = reader.list_chats(be)
        chats = [{k: c.get(k, "") for k in ("name", "kind", "unread", "preview", "time")} for c in out["chats"]]
        return {"ok": True, "total_unread": out["total_unread"], "count": len(chats), "chats": chats[:n]}

    def read_chat(self, name: str, last: int = 30) -> dict:
        _name(name)
        n = _int(last, 1, MAX_ROWS, "last")
        if self.backend() == "chrome":
            from .chrome import chrome_reader as cr

            with self._page() as page:
                msgs = cr.read_chat(page, name, 0)
            return self._store_read(name, msgs, n, "chrome")
        be, sys_ = self.backends()
        from .actions_cli import _win_title

        msgs = reader.read_chat(be, name, 0, window_title=_win_title(sys_))
        return self._store_read(name, msgs, n, "pc")

    def _store_read(self, name: str, msgs: list, n: int, backend: str) -> dict:
        store = Store(self.home)
        try:
            new = len(store.add_messages(name, msgs))
        finally:
            store.close()
        return {"ok": True, "backend": backend, "chat": name, "read": len(msgs), "new_saved": new,
                "messages": [{**_row(m), "chat": name} for m in msgs[-n:]]}

    def recent(self, chat: Optional[str] = None, n: int = 20) -> dict:
        k = _int(n, 1, MAX_ROWS, "n")
        store = Store(self.home)
        try:
            rows = store.recent(chat or None, k)
        finally:
            store.close()
        return {"ok": True, "count": len(rows), "messages": [_row(r) for r in rows]}

    def search(self, words: list, chat: Optional[str] = None, since: Optional[str] = None, limit: int = 50) -> dict:
        if isinstance(words, str):
            words = [words]
        ws = [w for w in (words or []) if isinstance(w, str) and w.strip()]
        if not ws:
            raise ValueError("words に探す言葉を 1 つ以上入れてください")
        k = _int(limit, 1, MAX_ROWS, "limit")
        store = Store(self.home)
        try:
            rows = store.search(ws, chat or None, _iso(since, "since"), k)
        finally:
            store.close()
        return {"ok": True, "count": len(rows), "messages": [_row(r) for r in rows]}

    # --- 動かす ---
    def open_chat(self, name: str) -> dict:
        _name(name)
        if self.backend() == "chrome":
            from .chrome import chrome_actions as act

            with self._page() as page:
                return {"ok": True, "backend": "chrome", **act.ensure_open(page, name)}
        from .actions_cli import _guard_cfg
        from .opener import open_chat

        be, sys_ = self.backends()
        out = open_chat(be, sys_, name, _guard_cfg(self.home))
        return {"ok": True, **out}

    def history(self, name: str, until_date: Optional[str] = None) -> dict:
        _name(name)
        until = _iso(until_date, "until_date")
        if self.backend() == "chrome":
            from .chrome import chrome_actions as act
            from .chrome import chrome_reader as cr

            with self._page() as page:
                out = act.load_history(page, name, until)
                msgs = cr.read_chat(page, name, 0, open_first=False)
            store = Store(self.home)
            try:
                out["saved_new"] = len(store.add_messages(name, msgs))
            finally:
                store.close()
            return {"ok": True, "backend": "chrome", **out}
        from .actions_cli import _guard_cfg, _win_title
        from .scroller import load_history

        be, sys_ = self.backends()
        out = load_history(be, sys_, name, _guard_cfg(self.home), until)
        msgs = reader.read_chat(be, name, 0, window_title=_win_title(sys_))
        store = Store(self.home)
        try:
            out["saved_new"] = len(store.add_messages(name, msgs))
        finally:
            store.close()
        return {"ok": True, **out}

    def draft_send(self, chat: str, text: str) -> dict:
        _name(chat, "chat")
        if self.backend() == "chrome":
            from .chrome import chrome_actions as act

            with self._page() as page:
                out = act.prepare(page, self.home, chat, text)
            return {"ok": True, "backend": "chrome", "sent": False, "target": out["target"], "text": out["text"],
                    "token": out["confirm"], "expires_in_s": out["expires_in_s"],
                    "next": "本人に相手と文面を見せて「はい」をもらってから line_send(chat, text, token) を呼ぶ"}
        from .sender import prepare

        be, sys_ = self.backends()
        out = prepare(be, sys_, self.home, chat, text)
        return {"ok": True, "sent": False, "target": out["target"], "text": out["text"], "token": out["confirm"],
                "expires_in_s": out["expires_in_s"],
                "next": "本人に相手と文面を見せて「はい」をもらってから line_send(chat, text, token) を呼ぶ"}

    def send(self, chat: str, text: str, token: str) -> dict:
        _name(chat, "chat")
        _name(token, "token")
        if self.backend() == "chrome":
            from .chrome import chrome_actions as act

            with self._page() as page:
                out = act.send(page, self.home, chat, text, token)
            return {"ok": bool(out.get("sent")), **out}
        from .actions_cli import _guard_cfg
        from .sender import send

        be, sys_ = self.backends()

        def read_rows(win):
            tree = be.snapshot(win, 40, 8000, 20.0)
            return reader.messages_from_tree(tree, chat, probe_factory=None)

        out = send(be, sys_, self.home, chat, text, token, _guard_cfg(self.home), read_rows)
        return {"ok": bool(out.get("sent")), **out}

    # --- 状態 ---
    def status(self) -> dict:
        home = self.home
        cmds = [c for c in self.procs() if "line_agent" in c and re.search(r"\bwatch\b", c)]
        pids = [int(c.split("|", 1)[0]) for c in cmds if c.split("|", 1)[0].isdigit()]
        log = home / "watcher.log"
        last = ""
        if log.exists():
            with open(log, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 4096))
                lines = f.read().decode("utf-8", "replace").splitlines()
            ticks = [ln for ln in lines if " tick " in ln]
            last = (ticks or lines or [""])[-1]
        unresolved = sendbook.Ledger(home).any_unresolved() if home.exists() else []
        cfg = sendbook.load_config(home) if home.exists() else {}
        backend, why = self.chooser(home)
        chrome_info = _real_chrome_state() if self.chooser is _real_choose else {}
        return {"ok": True, "backend": backend, "backend_reason": why, "chrome": chrome_info,
                "data_dir": str(home), "db_exists": (home / "line.db").exists(),
                "watcher_running": bool(pids), "watcher_pids": pids, "last_tick": last,
                "stop_file": (home / "STOP").exists(), "send_allowlist": list(sendbook.allowlist(cfg)),
                "unresolved_sends": len(unresolved),
                "attachment_save": backend == "chrome",
                "not_supported": "" if backend == "chrome" else NOT_SUPPORTED}


def guarded(fn: Callable) -> dict:
    """失敗を、トレースバックなしのやさしい文にして返す。標準出力に出るものは標準エラーへ逃がす(stdio を汚さない)。"""
    from .inputguard import GuardRefused
    from .opener import OpenRefused
    from .scroller import ScrollRefused
    from .sender import SendRefused

    try:
        with contextlib.redirect_stdout(sys.stderr):
            return _clean(fn())
    except (GuardRefused, OpenRefused, SendRefused, ScrollRefused) as e:
        return {"ok": False, "refused": True, "error": f"断られました: {e}"}
    except reader.ChatNotOpen as e:
        return {"ok": False, "error": f"{e}(line_open_chat で開いてから)"}
    except reader.LineNotFound as e:
        return {"ok": False, "error": f"LINE が見つかりません: {e}"}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - 最後の受け皿。型と短い文だけ返す
        return {"ok": False, "error": f"内部エラー: {type(e).__name__}: {str(e)[:200]}"}


def build_server(tools: Optional[LineTools] = None):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("line")
    t = tools or LineTools()

    def run(fn):
        with t.lock:
            return guarded(fn)

    @mcp.tool(description="LINE のトーク一覧(名前・種類・未読・最後の一言・時刻)を読む。読むだけ。")
    def line_list_chats(limit: int = 50) -> dict:
        return run(lambda: t.list_chats(limit))

    @mcp.tool(description="開いているトークの窓を読む(送った人・日付・時刻・種類・本文)。新しい行は DB にも保存する。窓が閉じていればエラー(line_open_chat で先に開く)。")
    def line_read_chat(name: str, last: int = 30) -> dict:
        return run(lambda: t.read_chat(name, last))

    @mcp.tool(description="この PC の DB にある最近の発言を古い順に並べて返す。chat を省くと全トーク。")
    def line_recent(chat: Optional[str] = None, n: int = 20) -> dict:
        return run(lambda: t.recent(chat, n))

    @mcp.tool(description="DB の発言を言葉で探す(全部の言葉を含むもの)。chat と since(YYYY-MM-DD)で絞れる。")
    def line_search(words: list[str], chat: Optional[str] = None, since: Optional[str] = None, limit: int = 50) -> dict:
        return run(lambda: t.search(words, chat, since, limit))

    @mcp.tool(description="トークを開く(裏で。前に出さない・マウスを使わない)。名前は完全一致。")
    def line_open_chat(name: str) -> dict:
        return run(lambda: t.open_chat(name))

    @mcp.tool(description="開いているトークを上へ送って古い履歴を読み込み、DB に保存する。until_date(YYYY-MM-DD)に届くか先頭で止まる。")
    def line_history(name: str, until_date: Optional[str] = None) -> dict:
        return run(lambda: t.history(name, until_date))

    @mcp.tool(description="送信の下見。送らない。相手の窓・文面を確かめ、1 回だけ使える合言葉(5 分)を返す。送れる相手は設定(send_allowlist)にある相手だけ。")
    def line_draft_send(chat: str, text: str) -> dict:
        return run(lambda: t.draft_send(chat, text))

    @mcp.tool(description="本番の送信。line_draft_send の合言葉と同じ相手・同じ文面が要る。本人が相手と文面を確かめて「はい」と言ったときだけ呼ぶ。")
    def line_send(chat: str, text: str, token: str) -> dict:
        return run(lambda: t.send(chat, text, token))

    @mcp.tool(description="添付(写真・動画・ファイル)を保存する(Chrome 版のとき。ダウンロード\\LINE保存 に置き、パス・大きさ・SHA256 を返す)。match はファイル名か「写真」「動画」、同じ名前が複数なら at(YYYY-MM-DD HH:MM)で絞る。期限切れは断る。PC 版のときは確かめるだけ。")
    def line_save_attachment(chat: str, match: str, at: Optional[str] = None) -> dict:
        return run(lambda: t.save_attachment(chat, match, at))

    @mcp.tool(description="使っている道(backend: chrome / pc)と Chrome 版にログインしているか、見張り・最後の tick・STOP ファイル・データの置き場所・添付の保存ができるか。")
    def line_status() -> dict:
        return run(t.status)

    return mcp


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
