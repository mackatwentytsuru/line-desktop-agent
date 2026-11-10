"""Read-only access to LINE windows through UI Automation.

Only property reads and tree walking. The rest of the package depends on the
small `Backend` interface and on plain `Node` snapshots, so parsers can be
tested with fake trees.
"""
from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

PROCESS_NAME = "LINE"
WINDOW_CLASSES = ("AllInOneWindow", "ChatWindow")

DEFAULT_MAX_DEPTH = 30
DEFAULT_MAX_NODES = 5000
DEFAULT_TIMEOUT_S = 15.0
# Panels not needed for the fast path (the chat list rows are read from the list).
FAST_SKIP_CLASSES = ("LcTitleBar", "AdvertisementPanel")


@dataclass
class Node:
    name: str = ""
    class_name: str = ""
    control_type: str = ""
    pid: int = 0
    hwnd: int = 0
    rect: tuple = (0, 0, 0, 0)
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def find_first(self, pred) -> Optional["Node"]:
        for n in self.walk():
            if pred(n):
                return n
        return None


class Backend(Protocol):
    def find_windows(self, process_name: str, classes: tuple) -> list: ...

    def snapshot(self, window_ref, max_depth: int, max_nodes: int, timeout_s: float) -> Node: ...


def _process_name(pid: int) -> str:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            base = buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
            return base[:-4] if base.lower().endswith(".exe") else base
        return ""
    finally:
        k32.CloseHandle(h)


def _to_node(c) -> Node:
    r = c.BoundingRectangle
    return Node(
        name=c.Name or "",
        class_name=c.ClassName or "",
        control_type=(c.ControlTypeName or "").replace("Control", ""),
        pid=c.ProcessId,
        hwnd=c.NativeWindowHandle or 0,
        rect=(r.left, r.top, r.right, r.bottom),
    )


class UiaBackend:
    """Real backend using the `uiautomation` package (property reads only)."""

    def __init__(self):
        import uiautomation as auto

        self._auto = auto

    def find_windows(self, process_name: str = PROCESS_NAME, classes: tuple = WINDOW_CLASSES) -> list:
        """Top-level windows of the process whose UIA class is in `classes`.

        Uses the Win32 window list (fast, ~0.1 s) instead of asking UIA for
        every desktop child (~5 s). Only reads.
        """
        user32 = ctypes.windll.user32
        handles: list = []
        pid = ctypes.c_ulong()
        cb_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(h, _):
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            handles.append((h, pid.value))
            return True

        user32.EnumWindows(cb_type(cb), 0)
        pid_cache: dict = {}
        found = []
        for h, p in handles:
            if p not in pid_cache:
                pid_cache[p] = _process_name(p).lower() == process_name.lower()
            if not pid_cache[p]:
                continue
            c = self._auto.ControlFromHandle(h)
            if c is not None and (c.ClassName or "") in classes:
                found.append(c)
        return found

    def snapshot_main(self, window_ref, timeout_s: float = DEFAULT_TIMEOUT_S) -> Node:
        """Fast read of the main window: skip title bar, shadows, ads, and the
        insides of the chat list rows. Reads properties only."""
        deadline = time.monotonic() + timeout_s

        def rec(c, depth, in_list=False):
            node = _to_node(c)
            cls = node.class_name
            if in_list or depth >= 10 or cls in FAST_SKIP_CLASSES or cls.startswith("shadow_"):
                return node
            is_list = cls == "LcListView"
            for ch in c.GetChildren():
                if time.monotonic() > deadline:
                    break
                node.children.append(rec(ch, depth + 1, is_list))
            return node

        return rec(window_ref, 0)

    def snapshot(self, window_ref, max_depth=DEFAULT_MAX_DEPTH, max_nodes=DEFAULT_MAX_NODES,
                 timeout_s=DEFAULT_TIMEOUT_S) -> Node:
        deadline = time.monotonic() + timeout_s
        count = 0

        def rec(c, depth):
            nonlocal count
            node = _to_node(c)
            count += 1
            if depth >= max_depth:
                return node
            for ch in c.GetChildren():
                if count >= max_nodes or time.monotonic() > deadline:
                    break
                node.children.append(rec(ch, depth + 1))
            return node

        return rec(window_ref, 0)


def window_info(ref) -> tuple:
    """(class_name, name) of a live window ref."""
    return (ref.ClassName or "", ref.Name or "")


def foreground_hwnd() -> int:
    """Read-only: handle of the current foreground window."""
    return int(ctypes.windll.user32.GetForegroundWindow() or 0)
