"""Fake Windows + fake LINE UIA objects for the guarded modules' tests."""


class FakeValue:
    def __init__(self, elem):
        self._e = elem

    @property
    def Value(self):
        return self._e.value


class FakeElem:
    def __init__(self, root, cls="", ctype="", name="", value="", rect=(0, 0, 0, 0), children=None,
                 mangle=None):
        self.root = root
        self.ClassName = cls
        self.ControlTypeName = ctype
        self.Name = name
        self.value = value
        self.rect = rect
        self.children = children if children is not None else []
        self.mangle = mangle  # function applied to values we set (to simulate a mismatch)
        self.NativeWindowHandle = 0

    def GetValuePattern(self):
        return FakeValue(self)

    def GetChildren(self):
        return list(self.children)

    def Exists(self, _t=0):
        return True

    @property
    def BoundingRectangle(self):
        class R:
            pass

        r = R()
        r.left, r.top, r.right, r.bottom = self.rect
        return r


class FakeSys:
    """Records every action in .acts. State is plain attributes."""

    def __init__(self, hwnd=100, title="一括送信", cls="ChatWindow", image="LINE.exe"):
        self.tick = 100_000
        self.last_input = 90_000  # 10 s idle
        self.fg = 999
        self.cursor_pos = (10, 10)
        self.windows = {hwnd: {"exists": True, "image": image, "pid": 7, "uia_class": cls, "title": title,
                               "visible": True, "enabled": True, "iconic": False,
                               "rect": (0, 0, 1000, 1000)}}
        self.others = []
        self.root_map = None  # None -> any point maps to the window under test
        self.acts = []
        self.on_act = None  # callback(name, args) for scripted reactions
        self.enter_empties = True
        self.default_hwnd = hwnd

    # reads
    def tick_ms(self):
        return self.tick

    def last_input_ms(self):
        return self.last_input

    def foreground(self):
        return self.fg

    def cursor(self):
        return self.cursor_pos

    def root_at(self, x, y):
        return self.default_hwnd if self.root_map is None else self.root_map(x, y)

    def pid_of(self, hwnd):
        return 7 if hwnd in self.windows else 0

    def window_state(self, hwnd):
        return dict(self.windows.get(hwnd, {"exists": False}))

    def other_line_windows(self, pid):
        return list(self.others)

    def element_root(self, elem):
        return elem.root

    def value_of(self, elem):
        return elem.value

    # actions
    def _rec(self, name, *args):
        self.acts.append((name,) + args)
        if self.on_act:
            self.on_act(name, args)

    def _act_invoke(self, elem):
        self._rec("invoke", elem.Name)

    def _act_legacy_text(self, elem, text):
        elem.value = elem.mangle(text) if (elem.mangle and text) else text
        self._rec("set_text", text)

    def _act_post_enter(self, hwnd):
        self._rec("post_enter", hwnd)

    def _act_post_wheel(self, hwnd, x, y, notches):
        self._rec("wheel", hwnd, notches)

    def _act_show_noactivate(self, hwnd):
        self.windows[hwnd]["iconic"] = False
        self._rec("show_noactivate", hwnd)

    def _act_give_back_foreground(self, hwnd):
        self.fg = hwnd
        self._rec("give_back", hwnd)

    def action_names(self):
        return [a[0] for a in self.acts]


class FakeWin(FakeElem):
    def __init__(self, hwnd, panel=None, lst=None, search=None):
        super().__init__(hwnd)
        self.NativeWindowHandle = hwnd
        self.panel, self.lst, self.search = panel, lst, search

    def GroupControl(self, ClassName="", searchDepth=0):
        return self.panel

    def ListControl(self, ClassName="", searchDepth=0):
        return self.lst

    def EditControl(self, ClassName="", searchDepth=0):
        return self.search


class FakeBackend:
    def __init__(self, chats=(), main=None):
        self.chats = list(chats)
        self.main = main

    def find_windows(self, process, classes):
        out = []
        if "ChatWindow" in classes:
            out += self.chats
        if "AllInOneWindow" in classes and self.main is not None:
            out.append(self.main)
        return out


def chat_with_input(hwnd=100, value="", mangle=None):
    inp = FakeElem(hwnd, "AutoSuggestTextArea", "EditControl", "メッセージを入力", value, mangle=mangle)
    panel = FakeElem(hwnd, "MessageInputPanel", "GroupControl",
                     children=[FakeElem(hwnd, "LcWidget", "GroupControl", children=[inp])])
    return FakeWin(hwnd, panel=panel), inp
