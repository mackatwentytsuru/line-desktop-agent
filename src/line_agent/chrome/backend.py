"""どちらの道(pc = デスクトップ版 / chrome = Chrome 拡張)を使うか。

設定ファイル config.json の "backend": "pc" | "chrome" | "auto"(既定 auto)。環境変数 LINE_AGENT_BACKEND が優先。
auto は、9233 番が答え、LINE 拡張のページがあり、ログイン済みなら chrome、それ以外は pc。
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import sendbook
from .cdp import DEFAULT_PORT, CdpError, find_line_page, port_answers

PORT_ENV = "LINE_AGENT_CHROME_PORT"


def chrome_port() -> int:
    return int(os.environ.get(PORT_ENV, DEFAULT_PORT))


def chrome_state(port: int | None = None) -> dict:
    """読むだけ。{port, port_answers, page, logged_in, room, visibility, error}"""
    port = port or chrome_port()
    st = {"port": port, "port_answers": False, "page": False, "logged_in": False}
    if not port_answers(port):
        return st
    st["port_answers"] = True
    try:
        if not find_line_page(port):
            return st
        st["page"] = True
        from .page import open_session

        page = open_session(port, focus_emulation=False)
        try:
            ps = page.status()
        finally:
            page.c.close()
        st.update(logged_in=bool(ps.get("logged_in")), room=ps.get("room"), visibility=ps.get("visibility"),
                  has_focus=ps.get("has_focus"))
    except (CdpError, OSError, ValueError) as e:
        st["error"] = f"{type(e).__name__}: {str(e)[:150]}"
    return st


def choose_backend(home: Path, port: int | None = None) -> tuple:
    """(backend, why)"""
    forced = os.environ.get("LINE_AGENT_BACKEND") or (sendbook.load_config(home).get("backend") if Path(home).exists() else None)
    if forced in ("pc", "chrome"):
        return forced, "設定で固定"
    st = chrome_state(port)
    if st["logged_in"]:
        return "chrome", "9233 番が答え、LINE 拡張にログイン済み"
    return "pc", "Chrome 版が使えない(" + ("ログインしていない" if st["page"] else "ページなし" if st["port_answers"] else "9233 番が答えない") + ")"
