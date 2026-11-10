"""Attachments (photo / video / file rows): find, check, and report. Read-only.

Saving cannot be done in the background yet (measured 11-10, docs/学んだこと.md
段 8): the link bar under a media row is not a UI Automation / MSAA element,
LINE ignores posted mouse buttons (Qt reads the real button state), and the
context menu that a posted right button opens does not react to UIA, MSAA,
posted hover or posted keys. So `check` only reports what can be known
without acting:
  - the row (exact one match; 期限切れ rows are refused),
  - whether its bar shows 保存 (not saved yet) or フォルダを開く (saved),
    read from a PrintWindow image of the chat window (works while covered),
  - for files: where a file with the same name already is in Downloads.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .linkbar import find_bar

MEDIA_KINDS = ("写真", "動画", "ファイル")
NOT_SUPPORTED = ("saving in the background is not possible yet: the 保存 link is not a UI element and "
                 "LINE ignores posted button presses; save it by hand in LINE (保存 under the row)")
_FILE_RE = re.compile(r"(?:^|, )ファイル, (?:期限切れ, )?(.+?), (?:午前|午後)\s?\d{1,2}:\d{2}")


class AttachRefused(ValueError):
    pass


def media_kind(name: str) -> str:
    parts = [p.strip() for p in (name or "").split(", ")]
    for k in MEDIA_KINDS:
        if k in parts[:3]:
            return k
    return ""


def file_name_of(row_name: str) -> str:
    m = _FILE_RE.search(row_name or "")
    return m.group(1) if m else ""


def pick_row(rows: list, match: str):
    """rows: UIA children of the message list. Exactly one media row whose name
    equals `match` or contains it."""
    if not match or not match.strip():
        raise AttachRefused("match is empty")
    media = [r for r in rows if media_kind(r.Name or "")]
    exact = [r for r in media if r.Name == match]
    hits = exact or [r for r in media if match in (r.Name or "")]
    if not hits:
        raise AttachRefused(f"no photo/video/file row matches {match!r}")
    if len(hits) > 1:
        raise AttachRefused(f"{len(hits)} rows match {match!r}: {[h.Name[:50] for h in hits[:5]]}; be more exact")
    row = hits[0]
    if "期限切れ" in (row.Name or ""):
        raise AttachRefused("this attachment has expired (期限切れ); it cannot be saved")
    return row


def bar_state(bar: list) -> str:
    """4 labels with the 2nd widest = 保存 | 名前を付けて保存 | 転送 | Keep; 3 = フォルダを開く | 転送 | Keep."""
    if len(bar) == 4:
        widths = [b - a for a, b, _ in bar]
        if widths[1] == max(widths):
            return "not_saved"
    if len(bar) == 3:
        return "saved"
    return "unknown"


def downloads_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"


def find_in_downloads(fname: str, folder: Optional[Path] = None) -> list:
    if not fname:
        return []
    folder = folder or downloads_dir()
    stem, ext = os.path.splitext(fname)
    rx = re.compile(re.escape(stem) + r"( \(\d+\))?" + re.escape(ext) + "$")
    if not folder.exists():
        return []
    return sorted(str(p) for p in folder.iterdir() if rx.match(p.name))


def check(backend, sys_, chat: str, match: str, grab=None) -> dict:
    from .opener import chat_windows

    ws = chat_windows(backend, sys_, chat)
    if len(ws) != 1:
        raise AttachRefused(f"need exactly one chat window titled {chat!r}, found {len(ws)} (open it first)")
    win = ws[0]
    view = win.GroupControl(ClassName="ChatMessageView", searchDepth=8)
    lst = view.ListControl(ClassName="LcListView", searchDepth=4)
    row = pick_row(lst.GetChildren(), match)
    kind = media_kind(row.Name)
    rr, lr = row.BoundingRectangle, lst.BoundingRectangle
    state = "unknown (scroll the row into view)"
    if rr.top >= lr.top and rr.bottom <= lr.bottom:
        if grab is None:
            from .pixels import window_image as grab
        img, (L, T) = grab(int(win.NativeWindowHandle))
        state = bar_state(find_bar(img.crop((rr.left - L, rr.top - T, rr.right - L, rr.bottom - T))))
    fname = file_name_of(row.Name) if kind == "ファイル" else ""
    return {"chat": chat, "row": row.Name, "kind": kind, "bar": state, "file_name": fname,
            "in_downloads": find_in_downloads(fname), "saved_by_tool": False, "why": NOT_SUPPORTED}
