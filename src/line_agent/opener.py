"""Open a chat by its exact name, in the background (no mouse, no front).

All actions go through inputguard.run_guarded (the `hands` object). This file
only reads the UIA tree and decides what to do.

Order:
  1. a chat window with that exact Win32 title already shown -> nothing to do
  2. the window exists but is iconic -> show it without bringing it to the front
  3. otherwise the row in the main list (exact name; several rows with the
     name -> refuse). Not on screen: put the name into the search box through
     LegacyIAccessible (no typing), only when the box was empty, then read the
     list again. Press the row through UIA and wait up to 3 s for the window.
     LINE may bring the new window to the front; the guard then gives the
     owner's window back and logs it.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from .inputguard import GuardConfig, GuardRefused, Target, run_guarded
from .parse import parse_chat_row

OPEN_WAIT_S = 3.0
SEARCH_WAIT_S = 1.5
SEARCH_CLASS = "LcTextField"


class OpenRefused(Exception):
    pass


def chat_windows(backend, sys_, title: str) -> list:
    """Chat windows whose Win32 title is exactly `title`.

    Win32 title, not the UIA name: an iconic chat window reports 'LINE' as its
    UIA name but keeps the chat name as its window text."""
    out = []
    for w in backend.find_windows("LINE", ("ChatWindow",)):
        h = int(w.NativeWindowHandle or 0)
        if h and sys_.window_state(h).get("title") == title:
            out.append(w)
    return out


def _shown(sys_, w) -> bool:
    st = sys_.window_state(int(w.NativeWindowHandle))
    return st.get("exists") and st.get("visible") and not st.get("iconic")


def wait_window(backend, sys_, title: str, timeout_s: float, sleep: Callable = time.sleep) -> Optional[object]:
    t0 = time.monotonic()
    while True:
        ws = [w for w in chat_windows(backend, sys_, title) if _shown(sys_, w)]
        if len(ws) == 1:
            return ws[0]
        if time.monotonic() - t0 >= timeout_s:
            return None
        sleep(0.2)


def main_window(backend):
    ws = backend.find_windows("LINE", ("AllInOneWindow",))
    if len(ws) != 1:
        raise OpenRefused(f"expected exactly one LINE main window, found {len(ws)}")
    return ws[0]


def chat_list(main):
    lst = main.ListControl(ClassName="LcListView", searchDepth=12)
    if not lst.Exists(0):
        raise OpenRefused("chat list not found in the LINE main window")
    return lst


def matching_rows(lst, name: str) -> list:
    rows = []
    for r in lst.GetChildren():
        if r.ControlTypeName == "ButtonControl" and r.Name and parse_chat_row(r.Name)["name"] == name:
            rows.append(r)
    return rows


def _rect(c) -> tuple:
    r = c.BoundingRectangle
    return (r.left, r.top, r.right, r.bottom)


def row_on_screen(row, lst) -> bool:
    l, t, r, b = _rect(row)
    L, T, R, B = _rect(lst)
    return r > l and b > t and l >= L and r <= R and t >= T and b <= B


def find_row(lst, name: str):
    rows = matching_rows(lst, name)
    if len(rows) > 1:
        raise OpenRefused(f"{len(rows)} chats are named {name!r}; refusing to guess")
    return rows[0] if rows else None


def _glog(glog) -> dict:
    return {"fg_before": glog.fg_before, "fg_after": glog.fg_after, "steps": glog.steps,
            "cursor_before": glog.cursor_before, "cursor_after": glog.cursor_after,
            "line_front_seen": glog.line_front_seen, "gave_back_front": glog.gave_back_front}


def open_chat(backend, sys_, name: str, cfg: GuardConfig, log: Optional[list] = None) -> dict:
    """Background only. An iconic chat window is shown without coming to the front; a
    chat with no window is opened by pressing its list row through UIA."""
    log = [] if log is None else log
    existing = chat_windows(backend, sys_, name)
    if len(existing) > 1:
        raise OpenRefused(f"{len(existing)} chat windows are titled {name!r}")
    if existing and _shown(sys_, existing[0]):
        return {"opened": False, "already": True, "hwnd": int(existing[0].NativeWindowHandle), "steps": []}
    if existing:
        h = int(existing[0].NativeWindowHandle)
        _, glog = run_guarded(sys_, Target(h, "ChatWindow", name), cfg, lambda hands: hands.show_noactivate())
        w = wait_window(backend, sys_, name, OPEN_WAIT_S)
        if w is None:
            raise OpenRefused(f"chat window {name!r} did not come back from iconic; log={_glog(glog)}")
        return {"opened": True, "how": "show_in_background", "via_search": False, "hwnd": h, **_glog(glog)}

    main = main_window(backend)
    mh = int(main.NativeWindowHandle)
    target = Target(mh, "AllInOneWindow")
    lst = chat_list(main)
    row = find_row(lst, name)
    box = None
    if row is None or not row_on_screen(row, lst):
        row, lst, box = _row_via_search(sys_, main, name, target, cfg, log)
    via_search = box is not None

    def act(hands):
        try:
            hands.invoke(row)
            return wait_window(backend, sys_, name, OPEN_WAIT_S)
        finally:
            if box is not None:
                hands.take_back(box, (name,))  # put back the empty search box we found

    w, glog = run_guarded(sys_, target, cfg, act)
    if w is None:
        raise OpenRefused(f"chat window {name!r} did not appear after pressing its row; log={_glog(glog)}")
    return {"opened": True, "how": "row_press", "via_search": via_search, "hwnd": int(w.NativeWindowHandle),
            **_glog(glog)}


def _row_via_search(sys_, main, name, target, cfg, log):
    box = main.EditControl(ClassName=SEARCH_CLASS, searchDepth=14)
    if not box.Exists(0):
        raise OpenRefused("search box not found")
    if (box.GetValuePattern().Value or "") != "":  # read only
        raise OpenRefused("the search box is not empty (the owner may be using it); not touching it")

    def act(hands):
        hands.set_text(box, name)
        time.sleep(SEARCH_WAIT_S)
        if (box.GetValuePattern().Value or "") != name:
            raise GuardRefused("search box value did not take")

    _, glog = run_guarded(sys_, target, cfg, act)
    log.append({"search": name, "fg_before": glog.fg_before, "fg_after": glog.fg_after, "steps": glog.steps})
    lst = chat_list(main)
    row = find_row(lst, name)
    if row is None or not row_on_screen(row, lst):
        raise OpenRefused(f"no on-screen row named {name!r} even after search")
    return row, lst, box
