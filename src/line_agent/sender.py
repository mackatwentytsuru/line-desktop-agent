"""Send one message to an allowed chat, after a dry-run and a confirm token.

  prepare(...)  dry-run: checks everything that can be checked without acting,
                prints target title, window handle, text, and issues a
                one-time token (5 minutes, bound to target + text).
  send(...)     the real send. Order:
     1. STOP file, allowlist (only chats in config send_allowlist), token, ledger
     2. exactly one chat window with that exact title; exactly one input; the
        input is EMPTY (never touch the owner's draft)
     3. ledger: sending
     4. under the guard, all in the background (LINE never comes to the
        front): put the text with the LegacyIAccessible value setter, read it back
        (must be equal), post WM_KEYDOWN/UP Enter to the chat window's hwnd
     5. read the chat: the last row must be our text from the owner
        -> ledger sent; otherwise failed / unknown. unknown is never retried.
LINE shows no send button in the UIA tree (measured 11-10), so Enter is the
only way; it is a message to the verified LINE window, never a physical key.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import sendbook
from .inputguard import (INPUT_CLASS, GuardConfig, GuardRefused, InputUncertain, Target, run_guarded,
                         stop_file, wait_owner_idle)
from .opener import chat_windows

SENT_WAIT_S = 3.0
SETTLE_S = 0.3


class SendRefused(Exception):
    pass


def _input_of(win):
    panel = win.GroupControl(ClassName="MessageInputPanel", searchDepth=10)
    if not panel.Exists(0):
        raise SendRefused("message input panel not found")
    cands = []
    for child in panel.GetChildren():
        cands.append(child)
        cands.extend(child.GetChildren())
    edits = [c for c in cands if c.ControlTypeName == "EditControl" and c.ClassName == INPUT_CLASS]
    if len(edits) != 1:
        raise SendRefused(f"expected exactly one chat input, found {len(edits)}")
    return edits[0]


def _value(elem) -> str:
    return elem.GetValuePattern().Value or ""


def precheck(backend, sys_, home: Path, title: str, text: str) -> tuple:
    """Checks before any action. Returns (window, input element)."""
    cfg = sendbook.load_config(home)
    if stop_file(home).exists():
        raise SendRefused("STOP file present")
    if not sendbook.is_allowed(title, cfg):
        raise SendRefused(sendbook.refusal_text(title, cfg))
    if not text or not text.strip():
        raise SendRefused("empty text")
    if sendbook.Ledger(home).any_unresolved():
        raise SendRefused("the ledger has an unresolved send (sending/unknown); "
                          "check LINE by eye and resolve it first, nothing is retried")
    wins = chat_windows(backend, sys_, title)
    if len(wins) != 1:
        raise SendRefused(f"need exactly one chat window titled {title!r}, found {len(wins)} (open it first)")
    win = wins[0]
    st = sys_.window_state(int(win.NativeWindowHandle))
    if st.get("uia_class") != "ChatWindow" or st.get("image", "").lower() != "line.exe":
        raise SendRefused(f"window is not a LINE chat window: {st}")
    inp = _input_of(win)
    if _value(inp) != "":
        raise SendRefused("the chat input is not empty (owner's draft?); not touching it")
    return win, inp


def prepare(backend, sys_, home: Path, title: str, text: str, tokens=None) -> dict:
    win, _ = precheck(backend, sys_, home, title, text)
    tokens = tokens or sendbook.TokenBook(home)
    token = tokens.issue(title, text)
    return {"dry_run": True, "target": title, "hwnd": int(win.NativeWindowHandle), "text": text,
            "confirm": token, "expires_in_s": sendbook.TOKEN_TTL_S}


def last_own_text(read_rows: Callable, win) -> Optional[dict]:
    rows = read_rows(win)
    return rows[-1] if rows else None


def _matches(row: Optional[dict], text: str) -> bool:
    if not row:
        return False
    body = row.get("text", "")
    return row.get("sender") == "self" and (body == text or (row.get("raw", "")).startswith(text + ", "))


def send(backend, sys_, home: Path, title: str, text: str, token: str, gcfg: GuardConfig,
         read_rows: Callable, tokens=None, ledger=None, sleep: Callable = time.sleep) -> dict:
    """read_rows(win) -> parsed messages of that window (reader.messages_from_tree)."""
    win, inp = precheck(backend, sys_, home, title, text)
    tokens = tokens or sendbook.TokenBook(home)
    ledger = ledger or sendbook.Ledger(home)
    if gcfg.idle_s > 0:  # optional: wait for the owner first, so a busy PC does not use up the token
        try:
            wait_owner_idle(sys_, gcfg.idle_s, gcfg.idle_wait_s, gcfg.poll_s, sleep)
        except GuardRefused as e:
            raise SendRefused(str(e)) from None
    if sys_.foreground() == int(win.NativeWindowHandle):
        raise SendRefused("the chat window is in front (the owner may be using it); not sending")
    try:
        tokens.consume(token, title, text)
    except ValueError as e:
        raise SendRefused(str(e)) from None
    hwnd = int(win.NativeWindowHandle)
    sid = uuid.uuid4().hex[:12]
    base = {"target": title, "hwnd": hwnd, "text": text, "payload": sendbook.payload_hash(title, text)}
    ledger.write(sid, "sending", **base)
    state = {"entered": False}

    def act(hands):
        hands.set_text(inp, text)
        sleep(SETTLE_S)
        back = _value(inp)
        if back != text:
            # the field was empty and nobody else typed in it: what is there now is ours
            hands.take_back(inp, (text, back))
            raise GuardRefused(f"text in the input is not what was confirmed: {back!r}")
        try:
            state["entered"] = True  # from here a send may have happened
            hands.post_enter()
        except InputUncertain:
            state["pressed"] = True  # key down went in without key up: unknown
            raise
        except GuardRefused:
            hands.take_back(inp, (text,))
            raise
        state["pressed"] = True
        t0 = time.monotonic()
        while time.monotonic() - t0 < SENT_WAIT_S and _value(inp) != "":
            sleep(0.2)
        left = _value(inp)
        # Enter did not send: empty only our own text, now, in the same session
        cleaned = bool(left) and hands.take_back(inp, _ours(text))
        return left, cleaned

    try:
        (left, cleaned), glog = run_guarded(sys_, Target(hwnd, "ChatWindow", title), gcfg, act)
    except GuardRefused as e:
        st = "unknown" if state.get("pressed") else "failed"
        ledger.write(sid, st, error=str(e), **base)
        raise SendRefused(f"{st}: {e}") from None
    except Exception as e:
        st = "unknown" if state["entered"] else "failed"
        ledger.write(sid, st, error=repr(e), **base)
        raise
    info = {"id": sid, "fg_before": glog.fg_before, "fg_after": glog.fg_after, "steps": glog.steps,
            "cursor_before": glog.cursor_before, "cursor_after": glog.cursor_after,
            "line_front_seen": glog.line_front_seen, "gave_back_front": glog.gave_back_front}
    if left != "":
        ledger.write(sid, "failed", error=f"input not emptied after Enter: {left!r}", cleaned=cleaned, **base)
        return {"sent": False, "state": "failed", "left": left, "cleaned": cleaned, **info}
    row = None
    for _ in range(10):
        row = last_own_text(read_rows, win)
        if _matches(row, text):
            ledger.write(sid, "sent", **base)
            return {"sent": True, "state": "sent", "last_row": row, **info}
        sleep(0.5)
    ledger.write(sid, "unknown", error="input emptied but the text was not seen as the last own row",
                 last_row=row, **base)
    return {"sent": False, "state": "unknown", "last_row": row, **info}


def _ours(text: str) -> tuple:
    """Values the input can hold after our own text plus one Enter as a new line."""
    return tuple(sorted({text, text + "\n", "\n" + text, text + "\r\n", "\r\n" + text, text + "\r", "\r" + text}))
