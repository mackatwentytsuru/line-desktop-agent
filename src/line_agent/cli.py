from __future__ import annotations

import argparse
import json
import sys

from . import reader
from .store import Store
from .uia import UiaBackend, window_info


def _show(rows: list) -> None:
    for r in rows:
        print(json.dumps({k: r.get(k, "") for k in ("chat", "date", "time", "kind", "sender", "text")}, ensure_ascii=False))


def _title_of(ref) -> str:
    return window_info(ref)[1]


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="line_agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("read")
    r.add_argument("title")
    r.add_argument("--last", type=int, default=20)
    ing = sub.add_parser("ingest")
    ing.add_argument("title")
    w = sub.add_parser("watch")
    w.add_argument("--interval", type=float, default=30.0)
    w.add_argument("--max-ticks", type=int, default=None)
    s = sub.add_parser("search")
    s.add_argument("words", nargs="+")
    s.add_argument("--chat")
    s.add_argument("--since", help="YYYY-MM-DD")
    rc = sub.add_parser("recent")
    rc.add_argument("--chat")
    rc.add_argument("--n", type=int, default=20)
    op = sub.add_parser("open", help="open a chat by its exact name")
    op.add_argument("name")
    sd = sub.add_parser("send", help="dry-run without --confirm; real send with --confirm <token>")
    sd.add_argument("title")
    sd.add_argument("--text", required=True)
    sd.add_argument("--confirm")
    sc = sub.add_parser("scroll", help="scroll a chat's messages in the background")
    sc.add_argument("chat")
    g = sc.add_mutually_exclusive_group(required=True)
    g.add_argument("--up", type=int)
    g.add_argument("--down", type=int)
    g.add_argument("--to-bottom", action="store_true")
    sv = sub.add_parser("save", help="check a photo/video/file row (saving itself is not possible yet)")
    sv.add_argument("chat")
    sv.add_argument("--match", required=True, help="the row name, or a part of it (must hit exactly one row)")
    hi = sub.add_parser("history", help="scroll up to load older messages, then save them")
    hi.add_argument("chat")
    hi.add_argument("--until-date", help="YYYY-MM-DD (default: until the top)")
    from .chrome import cli_chrome

    cli_chrome.add_parsers(sub)
    sub.add_parser("ledger")
    lr = sub.add_parser("ledger-resolve", help="after checking LINE by eye")
    lr.add_argument("id")
    lr.add_argument("state", choices=("sent", "failed"))
    a = p.parse_args(argv)

    if a.cmd.startswith("chrome-"):
        return cli_chrome.run(a)

    if a.cmd in ("open", "send", "ledger", "ledger-resolve", "scroll", "history", "save"):
        from .actions_cli import run_action

        return run_action(a)

    if a.cmd in ("search", "recent"):
        st = Store()
        rows = st.search(a.words, a.chat, a.since) if a.cmd == "search" else st.recent(a.chat, a.n)
        _show(rows)
        return 0

    be = UiaBackend()
    try:
        if a.cmd == "list":
            out = reader.list_chats(be)
        elif a.cmd == "read":
            out = reader.read_chat(be, a.title, a.last, window_title=_title_of)
        elif a.cmd == "ingest":
            msgs = reader.read_chat(be, a.title, 0, window_title=_title_of)
            new = Store().add_messages(a.title, msgs)
            out = {"chat": a.title, "read": len(msgs), "new": len(new)}
        else:
            from .watcher import Watcher

            return Watcher(be, interval=a.interval).run(a.max_ticks)
    except (reader.ChatNotOpen, reader.LineNotFound) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0
