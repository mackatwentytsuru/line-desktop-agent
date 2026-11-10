"""Safety: only the guarded modules may act, only through the guard, and only
in the background (no mouse, no physical key, no focus or front changes).

- Every module except opener / sender / scroller / inputguard: no action word at
  all (same ban as stage 1; reader / parse / store / watcher / pixels / uia /
  linkbar / cli stay read-only).
- opener / sender / scroller: action words only as `hands.<method>` (the object
  that run_guarded hands to them); no ctypes, no raw `_act_` calls.
- inputguard.py: raw Windows calls only inside Win32System._act_* methods; the
  `_act_` methods are called only from _Hands; every _Hands action runs
  self._check first; _Hands is created only in run_guarded.
- Nowhere: SendInput, SetCursorPos, mouse/key events, SetFocus, posted mouse
  buttons, WM_CHAR, the foreground-stealing ValuePattern SetValue.
- PostMessageW only with WM_KEYDOWN/WM_KEYUP (VK_RETURN) or WM_MOUSEWHEEL.
"""
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "line_agent"
GUARDED = {"opener.py", "sender.py", "scroller.py", "inputguard.py"}

FORBIDDEN = [
    "Invoke", "SetValue", "SendKeys", "SetFocus", "Click", "keybd_event", "SendInput",
    "PostMessage", "SendMessage", "SetForegroundWindow", "ShowWindow", "pyautogui", "ahk",
    "mouse_event", "SetCursorPos", "SwitchToThisWindow", "BringWindowToTop", "SetActiveWindow",
    "Select", "Toggle", "Expand", "Collapse", "SetWindowPos", "Minimize", "Maximize", "Restore",
    "Activate", "typewrite", "hotkey", "WindowPattern", "SendMessageW", "PostMessageW",
]
HANDS_METHODS = {"invoke", "set_text", "post_enter", "wheel", "show_noactivate", "take_back"}
RAW_API = ["PostMessageW(", "ShowWindow(", "SetForegroundWindow(", "Invoke()", "SetValue("]
NEVER = ["SendInput", "SetCursorPos", "mouse_event", "keybd_event", "SendMessage", "SetFocus",
         "BUTTONDOWN", "BUTTONUP", "WM_CHAR", "WM_CONTEXTMENU", "SendKeys", "pyautogui", "typewrite",
         "hotkey", "SwitchToThisWindow", "SetActiveWindow", "BringWindowToTop", "AttachThreadInput",
         "GetValuePattern().SetValue", "keyboard", "clipboard"]


def _hits(text: str) -> list:
    return [w for w in FORBIDDEN if re.search(re.escape(w), text, re.IGNORECASE)]


def _all_src():
    # chrome/ は別の決まり(tests/test_chrome_safety.py)。PC 版の部品だけをここで調べる
    return {f.name: f.read_text(encoding="utf-8") for f in SRC.glob("*.py")}


def test_read_only_modules_have_no_action_words():
    hits = []
    for name, text in _all_src().items():
        if name not in GUARDED:
            hits += [(name, w) for w in _hits(text)]
    assert not hits, hits


def test_guarded_files_exist():
    assert {f.name for f in SRC.glob("*.py")} >= GUARDED


def test_never_anywhere():
    hits = [(n, w) for n, t in _all_src().items() for w in NEVER if re.search(re.escape(w), t, re.IGNORECASE)]
    assert not hits, hits


def test_guarded_users_act_only_through_hands():
    for name in GUARDED - {"inputguard.py"}:
        text = (SRC / name).read_text(encoding="utf-8")
        used = set(re.findall(r"\bhands\.(\w+)", text))
        assert used <= HANDS_METHODS, (name, used - HANDS_METHODS)
        stripped = re.sub(r"\bhands\.\w+", "hands.X", text)
        assert not _hits(stripped), (name, _hits(stripped))
        for bad in ("ctypes", "windll", "_act_", "_Hands", "user32"):
            assert bad not in text, (name, bad)


def _guard_tree():
    text = (SRC / "inputguard.py").read_text(encoding="utf-8")
    return text, ast.parse(text)


def _cls(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _segment(text, node):
    return ast.get_source_segment(text, node) or ""


def test_raw_api_only_inside_act_methods():
    text, tree = _guard_tree()
    w32 = _cls(tree, "Win32System")
    rest = text
    for f in w32.body:
        if isinstance(f, ast.FunctionDef) and f.name.startswith("_act_"):
            rest = rest.replace(_segment(text, f), "")
    for api in RAW_API:
        assert api not in rest, api


def test_post_message_only_enter_and_wheel():
    text, _ = _guard_tree()
    calls = re.findall(r"PostMessageW\(([^,]+),\s*(\w+),\s*([^,]+),", text)
    assert calls, "no PostMessageW call found"
    for _, msg, wp in calls:
        assert msg in ("WM_KEYDOWN", "WM_KEYUP", "WM_MOUSEWHEEL"), msg
        if msg != "WM_MOUSEWHEEL":
            assert wp.strip() == "VK_RETURN", wp
    assert set(re.findall(r"\bVK_\w+", text)) == {"VK_RETURN"}
    assert set(re.findall(r"\bWM_\w+", text)) == {"WM_KEYDOWN", "WM_KEYUP", "WM_MOUSEWHEEL"}


def test_foreground_only_given_back():
    text, tree = _guard_tree()
    assert text.count("SetForegroundWindow(") == 1
    users = re.findall(r"\._act_give_back_foreground\(", text)
    assert len(users) == 1
    hands = _segment(text, _cls(tree, "_Hands"))
    give = hands[hands.index("def _give_back"):]
    assert "_act_give_back_foreground" in give


def test_act_methods_called_only_from_hands():
    text, tree = _guard_tree()
    hands_src = _segment(text, _cls(tree, "_Hands"))
    w32_src = _segment(text, _cls(tree, "Win32System"))
    outside = text.replace(hands_src, "").replace(w32_src, "")
    assert "._act_" not in outside
    assert not re.search(r"\b(?!self)\w+\._act_", w32_src)


def test_every_hands_action_checks_first():
    _, tree = _guard_tree()
    hands = _cls(tree, "_Hands")
    for f in hands.body:
        if not isinstance(f, ast.FunctionDef) or f.name.startswith("_"):
            continue
        assert f.name in HANDS_METHODS, f.name
        if f.name == "take_back":
            continue  # own narrower checks: element in target, value is exactly ours
        calls = [n for n in ast.walk(f) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        names = [c.func.attr for c in calls]
        assert "_check" in names, f.name
        first_act = next((i for i, n in enumerate(names) if n.startswith("_act_")), None)
        if first_act is not None:
            assert names.index("_check") < first_act, f.name


def test_hands_created_only_in_run_guarded():
    text, tree = _guard_tree()
    assert len(re.findall(r"_Hands\(", text)) == 1
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_guarded")
    assert "_Hands(" in _segment(text, run)
