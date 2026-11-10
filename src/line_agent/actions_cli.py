"""CLI glue for open / send / ledger. No UI calls here: it only wires the
guarded modules (opener, sender) to the real Windows and UIA backends."""
from __future__ import annotations

import json

from . import reader, sendbook
from .store import Store, home_dir


def _win_title(sys_):
    # Win32 title: an iconic chat window reports 'LINE' as its UIA name
    return lambda ref: sys_.window_state(int(ref.NativeWindowHandle)).get("title", "")


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1, default=str))


def _guard_cfg(home):
    from .inputguard import DEFAULT_IDLE_S, DEFAULT_IDLE_WAIT_S, GuardConfig

    c = sendbook.load_config(home)
    return GuardConfig(home, idle_s=float(c.get("idle_s", DEFAULT_IDLE_S)),
                       idle_wait_s=float(c.get("idle_wait_s", DEFAULT_IDLE_WAIT_S)))


def run_action(a) -> int:
    home = home_dir()
    if a.cmd == "ledger":
        _print(list(sendbook.Ledger(home).latest().values()))
        return 0
    if a.cmd == "ledger-resolve":
        _print(sendbook.Ledger(home).resolve(a.id, a.state))
        return 0

    from .inputguard import GuardRefused, Win32System, set_dpi_aware
    from .opener import OpenRefused, open_chat
    from .attachments import AttachRefused, check
    from .scroller import ScrollRefused, load_history, scroll, to_bottom
    from .sender import SendRefused, prepare, send
    from .uia import UiaBackend

    set_dpi_aware()
    sys_ = Win32System()
    be = UiaBackend()
    fg0 = sys_.foreground()
    try:
        if a.cmd == "open":
            out = open_chat(be, sys_, a.name, _guard_cfg(home))
        elif a.cmd == "scroll":
            if a.to_bottom:
                out = to_bottom(be, sys_, a.chat, _guard_cfg(home))
            else:
                out = scroll(be, sys_, a.chat, _guard_cfg(home), a.up if a.up else -a.down)
        elif a.cmd == "save":
            out = check(be, sys_, a.chat, a.match)
        elif a.cmd == "history":
            out = load_history(be, sys_, a.chat, _guard_cfg(home), a.until_date)
            msgs = reader.read_chat(be, a.chat, 0, window_title=_win_title(sys_))
            out["saved_new"] = len(Store().add_messages(a.chat, msgs))
        elif a.confirm is None:
            out = prepare(be, sys_, home, a.title, a.text)
            out["next"] = f"send {a.title} --text <same text> --confirm {out['confirm']}"
        else:
            def read_rows(win):
                tree = be.snapshot(win, 40, 8000, 20.0)
                return reader.messages_from_tree(tree, a.title, probe_factory=None)

            out = send(be, sys_, home, a.title, a.text, a.confirm, _guard_cfg(home), read_rows)
    except (GuardRefused, OpenRefused, SendRefused, ScrollRefused, AttachRefused) as e:
        _print({"refused": str(e), "fg_before": fg0, "fg_after": sys_.foreground()})
        return 3
    out["fg_cli_start"] = fg0
    out["fg_cli_end"] = sys_.foreground()
    _print(out)
    if a.cmd == "save":
        return 5  # checked only; nothing was saved
    return 0 if out.get("sent", True) else 4
