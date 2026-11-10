"""前面の窓とカーソルを 20 ms ごとに見張る測定用の道具(読むだけ。何も動かさない)。

使い方(別のスクリプトから): with FrontProbe() as p: ...何か動かす...; p.report()
全プロセスの前面窓(GetForegroundWindow)のハンドル・プロセス名・題名と、カーソル位置の変化を数える。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def describe(h) -> str:
    if not h:
        return "0"
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(h, buf, 256)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    exe = ""
    hp = kernel32.OpenProcess(0x1000, False, pid.value)
    if hp:
        n = wt.DWORD(520)
        b2 = ctypes.create_unicode_buffer(520)
        if kernel32.QueryFullProcessImageNameW(hp, 0, b2, ctypes.byref(n)):
            exe = b2.value.rsplit("\\", 1)[-1]
        kernel32.CloseHandle(hp)
    return f"{int(h)}:{exe}:{buf.value[:20]}"


def cursor():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


class FrontProbe:
    def __init__(self, interval: float = 0.02):
        self.interval = interval
        self.fg_changes: list = []
        self.cur_changes: list = []
        self.polls = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        last_fg, last_cur = user32.GetForegroundWindow(), cursor()
        self.start_fg, self.start_cur = describe(last_fg), last_cur
        while not self._stop.is_set():
            fg, cur = user32.GetForegroundWindow(), cursor()
            self.polls += 1
            if fg != last_fg:
                self.fg_changes.append((round(time.time() % 1000, 2), describe(fg)))
                last_fg = fg
            if cur != last_cur:
                self.cur_changes.append((round(time.time() % 1000, 2), cur))
                last_cur = cur
            time.sleep(self.interval)

    def __enter__(self):
        self._t.start()
        time.sleep(0.1)
        return self

    def __exit__(self, *a):
        self._stop.set()
        self._t.join()

    def report(self) -> dict:
        return {"polls": self.polls, "fg_start": self.start_fg, "fg_changes": self.fg_changes,
                "cursor_start": self.start_cur, "cursor_changes": self.cur_changes[:5], "cursor_change_count": len(self.cur_changes)}
