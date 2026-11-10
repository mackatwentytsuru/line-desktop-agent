"""Chrome 版(src/line_agent/chrome/)の守りを、ソースの文字で確かめる。

PC 版の禁止語の試験(test_no_actions.py)は chrome/ を見ない。代わりにここで:
- CDP の入力(Input.*)・押す動作(.click())は chrome/ の中だけ、しかもページの部品の名簿の中だけ。
- OS を動かす道(ctypes・Win32・UIA・pyautogui・前面・カーソル)と、前に出す・Cookie/保存領域を読む CDP は一切ない。
"""
import re
from pathlib import Path

from line_agent.chrome import cdp, page

SRC = Path(__file__).resolve().parent.parent / "src" / "line_agent"
CHROME = SRC / "chrome"

OS_WORDS = ["ctypes", "windll", "user32", "SendInput", "SetCursorPos", "SetForegroundWindow", "keybd_event",
            "mouse_event", "pyautogui", "uiautomation", "ShowWindow", "BringWindowToTop", "SwitchToThisWindow",
            "AttachThreadInput", "PostMessage", "SendMessage", "subprocess", "clipboard"]
CDP_NEVER = ["Page.bringToFront", "Target.activateTarget", "Target.", "Network.", "Storage.", "IndexedDB",
             "DOMStorage", "Fetch.", "Page.navigate", "Page.reload", "Input.dispatchMouseEvent", "Input.dispatchTouchEvent",
             "document.cookie", "localStorage", "sessionStorage", "indexedDB", "fetch(", "XMLHttpRequest", "WebSocket("]


def _text(name):
    return (CHROME / name).read_text(encoding="utf-8")


def _chrome_files():
    return [f for f in CHROME.glob("*.py")]


def test_no_os_level_actions_in_chrome_package():
    hits = [(f.name, w) for f in _chrome_files() for w in OS_WORDS if re.search(re.escape(w), f.read_text(encoding="utf-8"))]
    assert not hits, hits


def test_no_forbidden_cdp_or_page_calls():
    hits = [(f.name, w) for f in _chrome_files() for w in CDP_NEVER
            if w in f.read_text(encoding="utf-8")]
    assert not hits, hits


def test_cdp_method_names_are_all_on_the_list():
    used = set()
    for f in _chrome_files():
        used |= set(re.findall(r'"((?:Runtime|Page|DOM|Input|Emulation|Browser)\.[a-zA-Z]+)"', f.read_text(encoding="utf-8")))
    used = {u for u in used if not u.startswith("Browser.download")}  # イベント名(受け取るだけ)
    assert used <= cdp.ALLOWED_METHODS, used - cdp.ALLOWED_METHODS
    assert not {m for m in cdp.ALLOWED_METHODS if m.split(".")[0] in ("Network", "Storage", "Target", "Fetch", "IndexedDB")}
    assert "Page.bringToFront" not in cdp.ALLOWED_METHODS


def test_cdp_input_only_in_page_module_and_only_text_and_keys():
    for f in _chrome_files():
        txt = f.read_text(encoding="utf-8")
        if f.name in ("page.py", "cdp.py"):
            continue
        assert "Input." not in txt, f.name
    assert {m for m in cdp.ALLOWED_METHODS if m.startswith("Input.")} == {"Input.dispatchKeyEvent", "Input.insertText"}
    keys = set(re.findall(r'\{"Enter": 13, "Backspace": 8\}', _text("page.py")))
    assert keys, "押せるキーは Enter と Backspace だけ"


def test_only_three_clicks_and_all_in_page_js():
    for f in _chrome_files():
        if f.name != "page.py":
            assert ".click()" not in f.read_text(encoding="utf-8"), f.name
    assert _text("page.py").count(".click()") == 3
    assert set(page.ALLOWED_CONTROLS) == {"nav_chat", "open_chat", "save_attachment", "scroll_down_button", "none"}


def test_page_controls_are_only_the_listed_ones():
    txt = _text("page.py")
    labels = set(re.findall(r"aria-label=\"([^\"]+)\"", txt)) | set(re.findall(r"getAttribute\('aria-label'\) === '([^']+)'", txt))
    assert labels == {"Chat", "Go chatroom"}, labels
    assert "txt(x) === '保存'" in txt and "名前を付けて" not in txt.replace("名前を付けて保存", "")
    for bad in ("削除", "取り消", "通報", "ブロック", "退出", "Add friend", "logout", "Keep", "転送ボタン"):
        # 名簿の外の部品を探すコードを書いていない(説明の文には出てよい: PRELUDE と JS の外)
        js = "".join(re.findall(r'r"""(.*?)"""', txt, re.S))
        assert bad not in js, bad


def test_chrome_start_script_never_widens_the_address():
    ps = (SRC.parent.parent / "scripts" / "start_line_chrome.ps1").read_text(encoding="utf-8")
    code = [ln for ln in ps.splitlines() if not ln.strip().startswith("#")]
    assert not any("remote-debugging-address" in ln for ln in code)
    assert not re.search(r"Stop-Process|taskkill", " ".join(code))
    assert "--remote-debugging-port=$Port" in ps and "$Port = 9233" in ps and "chrome-profile" in ps
