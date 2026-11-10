"""The only door to actions on LINE. Background only: nothing here moves the
mouse, presses a physical key, or brings a LINE window to the front.

Measured 11-10 (docs/学んだこと.md, 段 7):
  - text: the LegacyIAccessible value setter never took the foreground; the
    UIA ValuePattern setter and UIA focus both did, so neither is used.
  - Enter: WM_KEYDOWN/WM_KEYUP VK_RETURN posted to the ChatWindow hwnd sends
    while another app stays in front (Qt has one native hwnd per top window).
  - scroll: WM_MOUSEWHEEL posted to the ChatWindow hwnd scrolls its list in the
    background, even where another window covers the point.
  - posted mouse buttons and a posted context-menu message did nothing (Qt
    reads the real button state), so there is no click here at all.

Every action goes through `run_guarded(...)`, which checks, before the first
action and again before each one:
  - no STOP file,
  - the target window is the exact handle, belongs to LINE.exe, has the exact
    UIA class (and exact title when asked), is enabled, and LINE shows no other
    window (popup / dialog) besides chat windows, the main window and shadows,
  - the target is not the foreground window (the owner may be using it),
  - an element we touch lies inside the target window,
  - optional (idle_s > 0): the owner has been idle and stays idle.
While the action runs, the foreground is polled every 20 ms; if a LINE window
ever came to the front, the owner's window is given back afterwards and the
log says so. Any failed check raises `GuardRefused`; nothing is retried.

Raw Windows calls live only in the act methods of Win32System, and only the
`_Hands` object (created only inside `run_guarded`) calls them. The only key
is Enter, and only as a message to a verified LINE window.
tests/test_no_actions.py checks these rules.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

PROCESS_IMAGE = "line.exe"
OK_LINE_CLASSES = ("AllInOneWindow", "ChatWindow")
DEFAULT_IDLE_S = 0.0  # background actions do not need the owner to be idle
DEFAULT_IDLE_WAIT_S = 30.0
INPUT_CLASS = "AutoSuggestTextArea"
FG_POLL_S = 0.02

VK_RETURN = 0x0D
_SCAN_RETURN = 0x1C
WM_KEYDOWN, WM_KEYUP, WM_MOUSEWHEEL = 0x0100, 0x0101, 0x020A
_LP_DOWN = 1 | (_SCAN_RETURN << 16)
_LP_UP = _LP_DOWN | (1 << 30) | (1 << 31)
_WHEEL_DELTA = 120
_SW_SHOWNOACTIVATE = 4


class GuardRefused(Exception):
    """A safety check failed. Nothing more was done."""


class InputUncertain(GuardRefused):
    """Only part of a key (down without up) was posted: the result is unknown."""


def set_dpi_aware() -> None:
    """Per-monitor DPI aware v2 so all coordinates are physical pixels."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def stop_file(home: Path) -> Path:
    return Path(home) / "STOP"


# ---------------------------------------------------------------- Win32 layer

class _Pt(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _user32():
    # a private copy: setting argtypes on the shared ctypes.windll.user32 would
    # break other code (uiautomation) that calls the same functions differently
    u = ctypes.WinDLL("user32", use_last_error=True)
    H = ctypes.c_void_p
    u.GetForegroundWindow.restype = H
    u.WindowFromPoint.restype = H
    u.WindowFromPoint.argtypes = [_Pt]
    u.GetAncestor.restype = H
    u.GetAncestor.argtypes = [H, ctypes.c_uint]
    for name in ("IsWindow", "IsWindowVisible", "IsWindowEnabled", "IsIconic"):
        getattr(u, name).argtypes = [H]
    u.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(ctypes.c_ulong)]
    u.GetWindowTextW.argtypes = [H, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
    u.PostMessageW.argtypes = [H, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    u.ShowWindow.argtypes = [H, ctypes.c_int]
    u.SetForegroundWindow.argtypes = [H]
    return u


def _image_name(pid: int) -> str:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint(1024)
        if k32.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(size)):
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1]
        return ""
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def _pack_xy(x: int, y: int) -> int:
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


class Win32System:
    """Real Windows. Read methods are public; action methods start with _act_."""

    def __init__(self):
        import uiautomation as auto

        self._auto = auto
        self._u = _user32()

    # ---- reads
    def tick_ms(self) -> int:
        return int(ctypes.windll.kernel32.GetTickCount()) & 0xFFFFFFFF

    def last_input_ms(self) -> int:
        li = _LastInput()
        li.cbSize = ctypes.sizeof(_LastInput)
        self._u.GetLastInputInfo(ctypes.byref(li))
        return int(li.dwTime)

    def foreground(self) -> int:
        return int(self._u.GetForegroundWindow() or 0)

    def cursor(self) -> tuple:
        p = _Pt()
        self._u.GetCursorPos(ctypes.byref(p))
        return (p.x, p.y)

    def root_at(self, x: int, y: int) -> int:
        h = self._u.WindowFromPoint(_Pt(x, y))
        return int(self._u.GetAncestor(h, 2) or 0) if h else 0

    def pid_of(self, hwnd: int) -> int:
        pid = ctypes.c_ulong()
        self._u.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return pid.value

    def window_state(self, hwnd: int) -> dict:
        h = ctypes.c_void_p(hwnd)
        if not self._u.IsWindow(h):
            return {"exists": False}
        t = ctypes.create_unicode_buffer(512)
        self._u.GetWindowTextW(h, t, 512)
        r = wintypes.RECT()
        self._u.GetWindowRect(h, ctypes.byref(r))
        c = self._auto.ControlFromHandle(hwnd)
        return {
            "exists": True, "image": _image_name(self.pid_of(hwnd)), "pid": self.pid_of(hwnd),
            "uia_class": (c.ClassName or "") if c else "", "title": t.value,
            "visible": bool(self._u.IsWindowVisible(h)), "enabled": bool(self._u.IsWindowEnabled(h)),
            "iconic": bool(self._u.IsIconic(h)), "rect": (r.left, r.top, r.right, r.bottom),
        }

    def other_line_windows(self, pid: int) -> list:
        """Visible top-level windows of `pid` that are not a chat/main window or a shadow."""
        u = self._u
        out = []
        cb_t = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(h, _):
            if h and self.pid_of(h) == pid and u.IsWindowVisible(ctypes.c_void_p(h)):
                c = self._auto.ControlFromHandle(h)
                cls = (c.ClassName or "") if c else ""
                if cls not in OK_LINE_CLASSES and not cls.startswith("shadow_"):
                    out.append((int(h), cls))
            return True

        u.EnumWindows(cb_t(cb), 0)
        return out

    def element_root(self, elem) -> int:
        """Top-level window handle of a UIA element."""
        top = elem.GetTopLevelControl()
        return int(top.NativeWindowHandle or 0) if top else 0

    def value_of(self, elem) -> str:
        return elem.GetValuePattern().Value or ""

    # ---- actions (only _Hands calls these)
    def _act_invoke(self, elem) -> None:
        elem.GetInvokePattern().Invoke()

    def _act_legacy_text(self, elem, text: str) -> None:
        # IAccessible put_accValue: measured not to take the foreground
        elem.GetLegacyIAccessiblePattern().SetValue(text)

    def _act_post_enter(self, hwnd: int) -> None:
        if not self._u.PostMessageW(ctypes.c_void_p(hwnd), WM_KEYDOWN, VK_RETURN, _LP_DOWN):
            raise GuardRefused("posting Enter failed (nothing was posted)")
        if not self._u.PostMessageW(ctypes.c_void_p(hwnd), WM_KEYUP, VK_RETURN, _LP_UP):
            raise InputUncertain("Enter down was posted but up failed")

    def _act_post_wheel(self, hwnd: int, x: int, y: int, notches: int) -> None:
        wp = ((_WHEEL_DELTA * int(notches)) & 0xFFFF) << 16
        if not self._u.PostMessageW(ctypes.c_void_p(hwnd), WM_MOUSEWHEEL, wp, _pack_xy(x, y)):
            raise GuardRefused("posting the wheel failed")

    def _act_show_noactivate(self, hwnd: int) -> None:
        self._u.ShowWindow(ctypes.c_void_p(hwnd), _SW_SHOWNOACTIVATE)

    def _act_give_back_foreground(self, hwnd: int) -> None:
        """Only after LINE took the front by itself: give the owner's window back."""
        self._u.SetForegroundWindow(ctypes.c_void_p(hwnd))


# ---------------------------------------------------------------- the guard

@dataclass
class Target:
    hwnd: int
    uia_class: str
    title: Optional[str] = None  # exact title required when not None


@dataclass
class GuardConfig:
    home: Path
    idle_s: float = DEFAULT_IDLE_S
    idle_wait_s: float = DEFAULT_IDLE_WAIT_S
    poll_s: float = 0.25


@dataclass
class GuardLog:
    fg_before: int = 0
    fg_after: int = 0
    cursor_before: tuple = (0, 0)
    cursor_after: tuple = (0, 0)
    steps: list = field(default_factory=list)
    line_front_seen: list = field(default_factory=list)  # LINE windows seen in front
    gave_back_front: bool = False


def wait_owner_idle(sys_, idle_s: float, max_wait_s: float, poll_s: float = 0.25,
                    sleep: Callable = time.sleep) -> float:
    """Wait until no user input for idle_s seconds (0 = no wait). Returns idle seconds, or raises."""
    waited = 0.0
    while True:
        idle = ((sys_.tick_ms() - sys_.last_input_ms()) & 0xFFFFFFFF) / 1000.0
        if idle >= idle_s:
            return idle
        if waited >= max_wait_s:
            raise GuardRefused(f"the owner is using the PC (idle {idle:.1f}s < {idle_s}s for {max_wait_s}s); nothing done")
        sleep(poll_s)
        waited += poll_s


def check_target(sys_, t: Target) -> dict:
    st = sys_.window_state(t.hwnd)
    if not st.get("exists"):
        raise GuardRefused(f"window {t.hwnd} no longer exists")
    if st["image"].lower() != PROCESS_IMAGE:
        raise GuardRefused(f"window {t.hwnd} belongs to {st['image']!r}, not LINE.exe")
    if st["uia_class"] != t.uia_class:
        raise GuardRefused(f"window class {st['uia_class']!r} != {t.uia_class!r}")
    if t.title is not None and st["title"] != t.title:
        raise GuardRefused(f"window title {st['title']!r} != {t.title!r}")
    if not st["visible"] or not st["enabled"]:
        raise GuardRefused("target window is hidden or disabled (a dialog may be open)")
    others = sys_.other_line_windows(st["pid"])
    if others:
        raise GuardRefused(f"LINE shows another window (popup/dialog): {others[:3]}")
    return st


class _FrontWatch:
    """Polls the foreground every 20 ms and records LINE windows seen in front."""

    def __init__(self, sys_, pid: int, seen: list):
        self._sys, self._pid, self.seen = sys_, pid, seen
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(FG_POLL_S)

    def poll(self) -> None:
        f = self._sys.foreground()
        if f and self._sys.pid_of(f) == self._pid and (not self.seen or self.seen[-1] != f):
            self.seen.append(f)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self.poll()
        self._stop.set()
        self._t.join(1.0)


class _Hands:
    """Background actions, each preceded by the full check. Only run_guarded creates one."""

    def __init__(self, sys_, target: Target, cfg: GuardConfig, log: GuardLog, baseline: int):
        self._sys = sys_
        self._t = target
        self._cfg = cfg
        self._log = log
        self._baseline = baseline

    def _check(self, elem=None, point: Optional[tuple] = None) -> dict:
        s = self._sys
        if stop_file(self._cfg.home).exists():
            raise GuardRefused("STOP file present")
        if self._cfg.idle_s > 0 and s.last_input_ms() != self._baseline:
            raise GuardRefused("the owner used the mouse or keys while we were working; stopped")
        st = check_target(s, self._t)
        if s.foreground() == self._t.hwnd:
            raise GuardRefused("the target window is in front (the owner may be using it); not acting")
        if elem is not None and s.element_root(elem) != self._t.hwnd:
            raise GuardRefused("the element is not inside the target window")
        if point is not None:
            x, y = point
            l, t, r, b = st["rect"]
            if not (l <= x < r and t <= y < b):
                raise GuardRefused(f"point {point} is outside the target window")
        return st

    def invoke(self, elem) -> None:
        self._check(elem=elem)
        self._sys._act_invoke(elem)
        self._log.steps.append("invoke")

    def set_text(self, elem, text: str) -> None:
        self._check(elem=elem)
        self._sys._act_legacy_text(elem, text)
        self._log.steps.append("set_text")

    def post_enter(self) -> None:
        self._check()
        self._sys._act_post_enter(self._t.hwnd)
        self._log.steps.append("post_enter")

    def wheel(self, x: int, y: int, notches: int) -> None:
        if not isinstance(notches, int) or notches == 0 or abs(notches) > 10:
            raise GuardRefused("wheel notches must be 1..10 up or down")
        self._check(point=(x, y))
        self._sys._act_post_wheel(self._t.hwnd, x, y, notches)
        self._log.steps.append(f"wheel{notches:+d}")

    def show_noactivate(self) -> None:
        """Un-minimize without activating (no-op when not iconic)."""
        st = self._check()
        if st["iconic"]:
            self._sys._act_show_noactivate(self._t.hwnd)
            self._log.steps.append("show_noactivate")

    def take_back(self, elem, ours: tuple) -> bool:
        """Empty a field we filled ourselves, only if its value is exactly one of
        `ours`. Never touches anything else. Used to clean up when stopping."""
        s = self._sys
        if stop_file(self._cfg.home).exists():
            return False
        if s.element_root(elem) != self._t.hwnd:
            return False
        if s.value_of(elem) not in ours:
            return False
        s._act_legacy_text(elem, "")
        self._log.steps.append("take_back")
        return True

    def _give_back(self, fg: int) -> None:
        """If a LINE window is in front now and was not before, give the owner's window back."""
        s = self._sys
        cur = s.foreground()
        pid = s.pid_of(self._t.hwnd)
        if fg and cur != fg and cur and s.pid_of(cur) == pid and s.pid_of(fg) != pid:
            s._act_give_back_foreground(fg)
            self._log.gave_back_front = True


def run_guarded(sys_, target: Target, cfg: GuardConfig, action: Callable,
                sleep: Callable = time.sleep) -> tuple:
    """Run action(hands) under the guard. Returns (result, GuardLog).

    Raises GuardRefused when a check fails (before or during the action)."""
    log = GuardLog()
    if stop_file(cfg.home).exists():
        raise GuardRefused("STOP file present")
    if cfg.idle_s > 0:
        wait_owner_idle(sys_, cfg.idle_s, cfg.idle_wait_s, cfg.poll_s, sleep)
    st = check_target(sys_, target)
    if sys_.foreground() == target.hwnd:
        raise GuardRefused("the target window is in front (the owner may be using it); not acting")
    log.fg_before = sys_.foreground()
    log.cursor_before = tuple(sys_.cursor())
    hands = _Hands(sys_, target, cfg, log, sys_.last_input_ms())
    try:
        with _FrontWatch(sys_, st["pid"], log.line_front_seen):
            result = action(hands)
    finally:
        try:
            hands._give_back(log.fg_before)
        except Exception as e:
            log.steps.append(f"give_back_error:{e!r}")
        log.fg_after = sys_.foreground()
        log.cursor_after = tuple(sys_.cursor())
    return result, log
