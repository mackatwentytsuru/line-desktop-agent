"""High level read-only API: list_chats() and read_chat(title)."""
from __future__ import annotations

import re

from .parse import learn_members, parse_chat_row, parse_message, parse_total_unread, resolve_senders
from .uia import Node


class ChatNotOpen(Exception):
    pass


class LineNotFound(Exception):
    pass


def _is_list(n: Node) -> bool:
    return n.control_type == "List" and n.class_name == "LcListView"


def chats_from_tree(main: Node) -> dict:
    lst = main.find_first(_is_list)
    chats = []
    if lst is not None:
        for ch in lst.children:
            if ch.control_type == "Button" and ch.name:
                chats.append(parse_chat_row(ch.name))
    total = None
    for n in main.walk():
        if n.control_type == "TabItem":
            t = parse_total_unread(n.name)
            if t is not None:
                total = t
                break
    return {"total_unread": total, "chats": chats}


def owner_only_chat(chat: Node, title: str) -> bool:
    """True when the header button reads '<title>, 1人' (the owner is the only member)."""
    want = f"{title}, 1人"
    return chat.find_first(lambda n: n.name == want) is not None


def header_members(chat: Node, title: str):
    """N from a header button '<title>, N人' (None when there is none)."""
    rx = re.compile(re.escape(title) + r", (\d+)人$")
    n = chat.find_first(lambda x: bool(rx.match(x.name or "")))
    return int(rx.match(n.name).group(1)) if n is not None else None


def messages_from_tree(chat: Node, title: str = "", probe_factory=None, group_hint: bool = False) -> list:
    """Messages in screen order, dates and unread markers removed. Each has
    sender (self / other / unknown) resolved as described in parse.resolve_senders.
    probe_factory(root_node, list_node) -> object with .sender(rect), or None."""
    view = chat.find_first(lambda n: n.class_name == "ChatMessageView") or chat
    lst = view.find_first(_is_list)
    if lst is None:
        return []
    size = header_members(chat, title) if title else None
    group = group_hint or (size is not None and size >= 2)
    els = [el for el in lst.children if el.name]
    date = ""
    parsed = []
    for el in els:
        m = parse_message(el.name, date, title, group=group)
        if m["kind"] == "date":
            date = m["date"]
        parsed.append(m)
    probe = probe_factory(chat, lst) if probe_factory else None
    pixel = (lambda i: probe.sender(els[i].rect)) if probe else None
    if group:
        # learn member names, then re-read rows whose name is still unconfirmed:
        # a color check of 'self' means the first field was text, not a name.
        members = learn_members(parsed)
        date = ""
        for i, el in enumerate(els):
            m = parsed[i]
            if m["kind"] == "date":
                date = m["date"]
                continue
            unconfirmed = m["sender"] == "other" and m["sender_name"] and m["sender_name"] not in members
            nameless = bool(unconfirmed and pixel and pixel(i) == "self")
            parsed[i] = parse_message(el.name, date, title, group=True, members=members, nameless=nameless)
    rows = list(zip(parsed, els))
    resolve_senders(parsed, size == 1 if size is not None else False, pixel)
    return [m for m, _ in rows if m["kind"] not in ("date", "marker")]


def list_chats(backend) -> dict:
    wins = backend.find_windows("LINE", ("AllInOneWindow",))
    if not wins:
        raise LineNotFound("LINE main window (AllInOneWindow) not found")
    fast = getattr(backend, "snapshot_main", None)
    tree = fast(wins[0]) if fast else backend.snapshot(wins[0], 30, 5000, 15.0)
    return chats_from_tree(tree)


def default_probe(root: Node, lst: Node):
    from .pixels import PixelProbe

    return PixelProbe(root.hwnd, lst.rect)


def read_chat(backend, title: str, last: int, window_title, probe_factory=default_probe,
              group_hint: bool = False) -> list:
    """Read an already-open ChatWindow whose title equals `title` exactly.

    `window_title` is function(ref)->str giving the window title of a ref.
    """
    ref = None
    for w in backend.find_windows("LINE", ("ChatWindow",)):
        if window_title(w) == title:
            ref = w
            break
    if ref is None:
        raise ChatNotOpen(f"chat window '{title}' is not open (this tool never opens chats)")
    msgs = messages_from_tree(backend.snapshot(ref, 40, 8000, 20.0), title, probe_factory, group_hint)
    return msgs[-last:] if last else msgs
