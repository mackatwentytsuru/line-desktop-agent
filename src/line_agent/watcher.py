"""Read-only watcher: polls the chat list, records changes, stores new
messages of chats that are already open. Never opens a chat."""
from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

from . import reader
from .parse import DEFAULT_OFFICIAL
from .store import Store, home_dir, now_ts
from .uia import foreground_hwnd, window_info

MAX_BACKOFF_S = 300


def stop_requested(home: Path) -> bool:
    return (home / "STOP").exists()


def with_keys(chats: list) -> list:
    """Add c["key"]: the name, or name#2, name#3 for chats sharing a name
    (the list can hold several chats with one name; they must not overwrite
    each other). c["dup"] is True for those."""
    count: dict = {}
    total: dict = {}
    for c in chats:
        total[c["name"]] = total.get(c["name"], 0) + 1
    out = []
    for c in chats:
        n = count[c["name"]] = count.get(c["name"], 0) + 1
        out.append({**c, "key": c["name"] if n == 1 else f"{c['name']}#{n}", "dup": total[c["name"]] > 1})
    return out


def decide_notify(kind: str, unread: int, prev_unread: int, new_messages: list) -> tuple:
    """(notify, reason) for one chat-list change. Open chats and official
    accounts never notify; the owner's own messages never notify; a new
    message from the other side (or whose sender is unknown) does; with no
    message text available, a rise of the unread count does."""
    if kind == "openchat":
        return False, "open chat"
    if kind == "official":
        return False, "official account"
    real = [m for m in new_messages if m.get("kind") not in ("date", "marker")]
    if real:
        if all(m.get("sender") == "self" for m in real):
            return False, "only own messages"
        live = [m for m in real if m.get("sender") != "self" and m.get("kind") != "system"
                and m.get("sender") != "system" and not m.get("stale")]
        if live:
            return True, "new message from other/unknown"
        return False, "only system / old / own messages"
    if unread > prev_unread:
        return True, "unread increased"
    return False, "list-only change, unread did not increase"


def load_official(home: Path) -> set:
    f = home / "official_names.txt"
    if not f.exists():
        return set()
    return {ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")}


def detect_changes(prev: dict, chats: list) -> list:
    """Chats (with keys) whose (unread, preview, time) differ from `prev`
    (key -> tuple). A chat missing from `prev` counts as changed."""
    return [c for c in chats if prev.get(c["key"]) != (c["unread"], c["preview"], c["time"])]


class Watcher:
    def __init__(self, backend, home: Path | None = None, interval: float = 30.0,
                 fg=foreground_hwnd, sleep=time.sleep, start_date: str | None = None, title_of=lambda ref: window_info(ref)[1]):
        self.be = backend
        self.home = Path(home) if home else home_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.home)
        self.interval = interval
        self.fg, self.sleep, self.title_of = fg, sleep, title_of
        self.start_date = start_date or time.strftime("%Y-%m-%d")  # older messages never notify
        self.last_fg = None
        self.fails = 0
        self.baselined: set = set()  # titles of chat windows already read once by this process

    def log(self, msg: str) -> None:
        with open(self.home / "watcher.log", "a", encoding="utf-8") as f:
            f.write(f"{now_ts()} {msg}\n")

    def _emit(self, ev: dict) -> None:
        with open(self.home / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def _baseline_windows(self) -> None:
        """Windows that became open since the last tick are read once, silently:
        their existing messages are older than now and must not notify."""
        titles = set()
        for w in self.be.find_windows("LINE", ("ChatWindow",)):
            titles.add(self.title_of(w))
        for t in sorted(titles - self.baselined):
            try:
                self.store.add_messages(t, floor_iso=self.start_date, msgs=reader.read_chat(self.be, t, 0, self.title_of, probe_factory=None))
            except reader.ChatNotOpen:
                continue
            self.log(f"baseline read of open window ({len(t)} chars title)")
        self.baselined = titles

    def tick(self) -> list:
        """One poll. Returns the events written. Raises reader.LineNotFound."""
        fg = self.fg()
        official = DEFAULT_OFFICIAL | load_official(self.home)
        chats = [{**c, "kind": "official" if c["kind"] == "friend" and c["name"] in official else c["kind"]}
                 for c in reader.list_chats(self.be)["chats"]]
        chats = with_keys(chats)
        self._baseline_windows()
        prev = self.store.chat_states()
        kinds = self.store.chat_kinds()
        baseline = not prev
        events = []
        if not baseline:
            for c in detect_changes(prev, chats):
                if c["key"] in kinds and kinds[c["key"]] is None:
                    continue  # row saved before v2: its preview shape changed, not the chat
                new = []
                try:
                    if c["dup"]:
                        raise reader.ChatNotOpen("same name as another chat; not read")
                    msgs = reader.read_chat(self.be, c["name"], 0, self.title_of, group_hint=c["kind"] == "group")
                    new = self.store.add_messages(c["name"], msgs, floor_iso=self.start_date)
                except reader.ChatNotOpen:
                    pass
                notify, reason = decide_notify(c["kind"], c["unread"], (prev.get(c["key"]) or (0,))[0] or 0, new)
                ev = {"ts": now_ts(), "chat": c["name"], "kind": c["kind"], "unread": c["unread"],
                      "preview": c["preview"], "notify": notify, "reason": reason, "new_messages": new}
                self._emit(ev)
                events.append(ev)
        self.store.upsert_chats([{**c, "name": c["key"]} for c in chats])
        fg_after = self.fg()
        changed = self.last_fg is not None and fg_after != self.last_fg
        self.log(f"tick chats={len(chats)} events={len(events)} notify={sum(e['notify'] for e in events)} "
                 f"baseline={baseline} fg={fg}->{fg_after} fg_changed_since_last_tick={changed}")
        self.last_fg = fg_after
        return events

    def _wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not stop_requested(self.home):
            self.sleep(min(1.0, max(0.0, end - time.monotonic())))

    def run(self, max_ticks: int | None = None) -> int:
        ticks = 0
        while not stop_requested(self.home):
            try:
                self.tick()
                self.fails = 0
                delay = self.interval
            except reader.LineNotFound as e:
                self.fails += 1
                delay = min(self.interval * 2 ** self.fails, MAX_BACKOFF_S)
                self.log(f"LINE not found ({e}); retry in {delay:.0f}s")
            except Exception:
                self.fails += 1
                delay = min(self.interval * 2 ** self.fails, MAX_BACKOFF_S)
                self.log("error: " + traceback.format_exc().replace("\n", " | "))
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            self._wait(delay)
        self.log("stopped (STOP file)" if stop_requested(self.home) else "stopped (max ticks)")
        return 0
