"""CLI の chrome-* コマンドの配線。ページを触る部品は chrome_reader / chrome_actions にある。"""
from __future__ import annotations

import json

from ..store import Store, home_dir
from . import chrome_actions as act
from . import chrome_reader as cr
from .cdp import CdpError
from .page import PageError, open_session


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def add_parsers(sub) -> None:
    sub.add_parser("chrome-list", help="Chrome 版: トーク一覧")
    o = sub.add_parser("chrome-open", help="Chrome 版: トークを開く(名前は完全一致)")
    o.add_argument("name")
    r = sub.add_parser("chrome-read", help="Chrome 版: 開いて読み、DB に保存")
    r.add_argument("name")
    r.add_argument("--last", type=int, default=20)
    h = sub.add_parser("chrome-history", help="Chrome 版: 上へ送って古い履歴を読み込み、保存")
    h.add_argument("name")
    h.add_argument("--until-date", help="YYYY-MM-DD")
    s = sub.add_parser("chrome-save", help="Chrome 版: 添付を保存(ダウンロード\LINE保存)")
    s.add_argument("name")
    s.add_argument("--match", required=True)
    s.add_argument("--at", help="YYYY-MM-DD / HH:MM / YYYY-MM-DD HH:MM(同じ名前が複数のとき)")
    sd = sub.add_parser("chrome-send", help="Chrome 版: 下見(--confirm なし)/本番(--confirm 合言葉)")
    sd.add_argument("name")
    sd.add_argument("--text", required=True)
    sd.add_argument("--confirm")


def run(a) -> int:
    home = home_dir()
    page = None
    try:
        page = open_session()
        if a.cmd == "chrome-list":
            out = cr.list_chats(page)
        elif a.cmd == "chrome-open":
            out = act.ensure_open(page, a.name)
        elif a.cmd in ("chrome-read", "chrome-history"):
            out = {}
            if a.cmd == "chrome-history":
                out = act.load_history(page, a.name, a.until_date)
            msgs = cr.read_chat(page, a.name, 0)
            st = Store(home)
            try:
                out["read"], out["new_saved"] = len(msgs), len(st.add_messages(a.name, msgs))
            finally:
                st.close()
            if a.cmd == "chrome-read":
                out["messages"] = [{k: m[k] for k in ("date", "time", "sender", "sender_name", "kind", "text")}
                                   for m in (msgs[-a.last:] if a.last else msgs)]
        elif a.cmd == "chrome-save":
            out = act.save_attachment(page, a.name, a.match, a.at)
        elif a.confirm is None:
            out = act.prepare(page, home, a.name, a.text)
            out["next"] = f"chrome-send {a.name} --text <同じ文面> --confirm {out['confirm']}"
        else:
            out = act.send(page, home, a.name, a.text, a.confirm)
    except (act.ChromeRefused, PageError, cr.ChromeChatNotOpen, CdpError) as e:
        _print({"ok": False, "refused": str(e)})
        return 3
    finally:
        if page is not None:
            page.c.close()
    _print(out)
    return 0 if out.get("sent", True) else 4
