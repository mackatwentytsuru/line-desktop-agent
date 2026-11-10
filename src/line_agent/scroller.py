"""Scroll a chat's message list in the background, and load older history.

The list (LcListView) has no UIA scroll support, so the guard posts
WM_MOUSEWHEEL to the chat window's hwnd at the centre of the list (a message,
not the physical wheel; measured to work while another app is in front and
even where another window covers the point). All actions go through
inputguard.run_guarded (the `hands` object); this file only reads and decides.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

from .inputguard import GuardConfig, Target, run_guarded
from .opener import chat_windows

NOTCHES_PER_STEP = 3
SETTLE_S = 0.4
MAX_STEPS = 300
SAME_LIMIT = 3  # history stops when the first row did not change this many times
_DATE_RE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日")


class ScrollRefused(Exception):
    pass


def _the_window(backend, sys_, title: str):
    ws = chat_windows(backend, sys_, title)
    if len(ws) != 1:
        raise ScrollRefused(f"need exactly one chat window titled {title!r}, found {len(ws)} (open it first)")
    return ws[0]


def _the_list(win):
    view = win.GroupControl(ClassName="ChatMessageView", searchDepth=8)
    lst = view.ListControl(ClassName="LcListView", searchDepth=4) if view.Exists(0) else None
    if lst is None or not lst.Exists(0):
        raise ScrollRefused("message list not found")
    return lst


def _centre(lst) -> tuple:
    r = lst.BoundingRectangle
    return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)


def list_state(lst) -> dict:
    """First/last row name and position: enough to see whether scrolling moved."""
    kids = lst.GetChildren()
    if not kids:
        return {"count": 0, "first": "", "first_top": 0, "last": "", "last_bottom": 0, "dates": []}
    dates = [k.Name for k in kids if _DATE_RE.match(k.Name or "")]
    return {"count": len(kids), "first": kids[0].Name or "", "first_top": kids[0].BoundingRectangle.top,
            "last": kids[-1].Name or "", "last_bottom": kids[-1].BoundingRectangle.bottom, "dates": dates}


def iso_of(date_name: str) -> str:
    m = _DATE_RE.match(date_name or "")
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def _run(sys_, win, title, cfg, steps: Callable):
    return run_guarded(sys_, Target(int(win.NativeWindowHandle), "ChatWindow", title), cfg, steps)


def _log(glog) -> dict:
    return {"fg_before": glog.fg_before, "fg_after": glog.fg_after, "line_front_seen": glog.line_front_seen,
            "cursor_before": glog.cursor_before, "cursor_after": glog.cursor_after,
            "wheel_posts": len(glog.steps)}


def scroll(backend, sys_, title: str, cfg: GuardConfig, notches: int,
           sleep: Callable = time.sleep) -> dict:
    """notches > 0 = up (older), < 0 = down (newer)."""
    win = _the_window(backend, sys_, title)
    lst = _the_list(win)
    before = list_state(lst)
    x, y = _centre(lst)

    def act(hands):
        left = notches
        while left:
            n = max(-10, min(10, left))
            hands.wheel(x, y, n)
            left -= n
        sleep(SETTLE_S)

    _, glog = _run(sys_, win, title, cfg, act)
    after = list_state(lst)
    return {"chat": title, "notches": notches, "moved_px": after["first_top"] - before["first_top"],
            "before": _short(before), "after": _short(after), **_log(glog)}


def _short(st: dict) -> dict:
    return {"count": st["count"], "first": st["first"][:40], "first_top": st["first_top"],
            "last": st["last"][:40]}


def to_bottom(backend, sys_, title: str, cfg: GuardConfig, sleep: Callable = time.sleep) -> dict:
    win = _the_window(backend, sys_, title)
    lst = _the_list(win)
    x, y = _centre(lst)
    lst_bottom = lst.BoundingRectangle.bottom

    def act(hands):
        same, prev = 0, None
        for i in range(MAX_STEPS):
            st = list_state(lst)
            key = (st["last"], st["last_bottom"])
            if st["last_bottom"] <= lst_bottom + 2 and key == prev:
                return i
            same = same + 1 if key == prev else 0
            if same >= SAME_LIMIT:
                return i
            prev = key
            hands.wheel(x, y, -10)
            sleep(SETTLE_S)
        return MAX_STEPS

    steps, glog = _run(sys_, win, title, cfg, act)
    return {"chat": title, "steps": steps, "state": _short(list_state(lst)), **_log(glog)}


def load_history(backend, sys_, title: str, cfg: GuardConfig, until_iso: Optional[str],
                 sleep: Callable = time.sleep) -> dict:
    """Scroll up until the oldest date row is on or before until_iso, or the
    first row stops changing SAME_LIMIT times (top reached)."""
    win = _the_window(backend, sys_, title)
    lst = _the_list(win)
    x, y = _centre(lst)

    def done(st) -> Optional[str]:
        isos = [iso_of(d) for d in st["dates"] if iso_of(d)]
        if until_iso and isos and min(isos) <= until_iso:
            return "reached_date"
        return None

    def act(hands):
        same, prev = 0, None
        for i in range(MAX_STEPS):
            st = list_state(lst)
            why = done(st)
            if why:
                return why, i
            key = (st["count"], st["first"], st["first_top"])
            same = same + 1 if key == prev else 0
            if same >= SAME_LIMIT:
                return "top", i
            prev = key
            hands.wheel(x, y, 10)
            sleep(SETTLE_S)
        return "max_steps", MAX_STEPS

    (why, steps), glog = _run(sys_, win, title, cfg, act)
    st = list_state(lst)
    isos = sorted(iso_of(d) for d in st["dates"] if iso_of(d))
    return {"chat": title, "stopped": why, "steps": steps, "rows": st["count"],
            "oldest_date": isos[0] if isos else "", **_log(glog)}
