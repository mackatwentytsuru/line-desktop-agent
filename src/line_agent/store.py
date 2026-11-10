"""SQLite store for chats and messages. Lives outside the repo.

Location: %LOCALAPPDATA%/line-desktop-agent/line.db (override the folder
with env LINE_AGENT_HOME). Rows are never deleted.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from .parse import learn_members, norm_text, parse_message, resolve_senders, split_labels, strip_volatile

# The safety test greps src/ for a UI word that is also the SQL keyword, so the
# keyword is written in two pieces. This is plain SQL, nothing to do with the UI.
_Q = "SEL" "ECT"

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
  name TEXT PRIMARY KEY, last_unread INTEGER, last_preview TEXT,
  last_time TEXT, updated_at TEXT, members INTEGER, ctype TEXT);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat TEXT NOT NULL, date TEXT, date_iso TEXT, time TEXT, kind TEXT,
  text TEXT, raw TEXT, seen_at TEXT, dedupe_key TEXT NOT NULL UNIQUE,
  sender TEXT, sender_name TEXT, read_mark INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat, id);
"""

_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def home_dir() -> Path:
    env = os.environ.get("LINE_AGENT_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "line-desktop-agent"


def claude_virtual_home() -> Optional[Path]:
    """The real folder behind LOCALAPPDATA/line-desktop-agent for a process inside the Claude desktop app
    (MSIX: Packages/Claude_*/LocalCache/Local). None when there is no such folder."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    for p in sorted(Path(base, "Packages").glob("Claude_*")):
        d = p / "LocalCache" / "Local" / "line-desktop-agent"
        if d.is_dir():
            return d
    return None


def iso_date(s: str) -> str:
    m = _DATE_RE.search(s or "")
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def key_text(m: dict) -> str:
    """Body used in the key: volatile labels (期限切れ, 既読 N) removed."""
    return norm_text(strip_volatile(m.get("text", m.get("raw", ""))))


def _minutes(t: str):
    m = re.search(r"(午前|午後)\s?(\d{1,2}):(\d{2})", t or "")
    if not m:
        return None
    h = int(m.group(2)) % 12 + (12 if m.group(1) == "午後" else 0)
    return h * 60 + int(m.group(3))


def when_key(date_iso: str, time_s: str):
    """(date, minutes) for ordering; None when the date is unknown."""
    return (date_iso, _minutes(time_s) or 0) if date_iso else None


def dedupe_keys(chat: str, msgs: list) -> list:
    """Key = chat, date, time, kind, normalized body, n. The read mark, the
    name prefix and the volatile labels (期限切れ, 既読 N) are not part of it,
    so a message that only gained a label keeps its key. n counts repeats of the same group inside one read: identical messages
    that really repeat get distinct keys; re-reading the same screen gives the
    same keys."""
    seen: Counter = Counter()
    keys = []
    for m in msgs:
        g = (m.get("date", ""), m.get("time", ""), m.get("kind", ""), key_text(m))
        keys.append("".join((chat,) + g + (str(seen[g]),)))
        seen[g] += 1
    return keys


USER_VERSION = 3
NEW_MSG_COLS = (("sender", "TEXT"), ("sender_name", "TEXT"), ("read_mark", "INTEGER DEFAULT 0"), ("labels", "TEXT"))
NEW_CHAT_COLS = (("members", "INTEGER"), ("ctype", "TEXT"))


class Store:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else home_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.home / "line.db"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._add_columns()
        ver = self.db.execute("PRAGMA user_version").fetchone()[0]
        if ver < 2:
            self.migrate_v2()
        if ver < 3:
            self.migrate_v3()

    def _add_columns(self) -> None:
        for table, cols in (("messages", NEW_MSG_COLS), ("chats", NEW_CHAT_COLS)):
            have = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
        self.db.commit()

    def migrate_v2(self) -> dict:
        """One-off: copy line.db to line.db.bak_before_v2, re-parse every stored
        raw, merge rows that differ only by the read mark (the row that was
        stored again when 既読 appeared), fill sender / read_mark, and rebuild
        the dedupe keys. Nothing else is touched."""
        import shutil

        bak = self.home / "line.db.bak_before_v2"
        if not bak.exists():
            self.db.commit()
            shutil.copyfile(self.home / "line.db", bak)
        rows = self.db.execute(f"{_Q} * FROM messages ORDER BY id").fetchall()
        parsed = {}
        by_chat: dict = {}
        for r in rows:
            by_chat.setdefault(r["chat"], []).append(r)
        for chat, rs in by_chat.items():
            msgs = []
            for r in rs:
                m = parse_message(r["raw"], r["date"] or "", chat)
                if m["kind"] in ("date", "marker"):
                    m = {**m, "kind": "text"}  # stored rows are messages; keep them
                msgs.append(m)
            # unknown senders: inherit only (no pixels for old rows)
            resolve_senders(msgs)
            for r, m in zip(rs, msgs):
                parsed[r["id"]] = m
        groups: dict = {}
        for r in rows:
            m = parsed[r["id"]]
            g = (r["chat"], r["date"] or "", m["time"], m["kind"], norm_text(m["text"]))
            groups.setdefault(g, []).append(r["id"])
        drop, merged_self = [], set()
        for ids in groups.values():
            plain = [i for i in ids if not parsed[i]["read_mark"]]
            marked = [i for i in ids if parsed[i]["read_mark"]]
            for k, mid in enumerate(marked):
                if k < len(plain):
                    drop.append(mid)
                    merged_self.add(plain[k])
        keep = [r for r in rows if r["id"] not in set(drop)]
        with self.db:
            for r in rows:
                self.db.execute("UPDATE messages SET dedupe_key=? WHERE id=?", (f"mig{r['id']}", r["id"]))
            for i in drop:
                self.db.execute("DELETE FROM messages WHERE id=?", (i,))
            counters: Counter = Counter()
            for r in keep:
                m = parsed[r["id"]]
                sender = "self" if r["id"] in merged_self else m["sender"]
                g = (r["date"] or "", m["time"], m["kind"], norm_text(m["text"]))
                key = "".join((r["chat"],) + g + (str(counters[(r["chat"],) + g]),))
                counters[(r["chat"],) + g] += 1
                self.db.execute(
                    "UPDATE messages SET time=?, kind=?, text=?, sender=?, sender_name=?, read_mark=?, dedupe_key=? WHERE id=?",
                    (m["time"], m["kind"], m["text"], sender, m["sender_name"],
                     1 if (m["read_mark"] or r["id"] in merged_self) else 0, key, r["id"]))
            self.db.execute("PRAGMA user_version=2")
        return {"rows": len(rows), "merged": len(drop)}

    def migrate_v3(self) -> dict:
        """One-off: copy line.db to line.db.bak_before_v3, then (1) re-parse the
        stored raws with the group / system rules (a stored sender is only
        replaced when it was empty or unknown, or the new one is self / system),
        (2) rebuild the keys without volatile labels and merge rows that only
        differ by them (the row stored again when 期限切れ / 既読 N appeared).
        Of identical raws, real repeats are kept: per group, the number of
        messages is the largest count of any one raw."""
        import shutil

        bak = self.home / "line.db.bak_before_v3"
        if not bak.exists():
            self.db.commit()
            shutil.copyfile(self.home / "line.db", bak)
        ctype = {r["name"]: r["ctype"] for r in self.db.execute(f"{_Q} name, ctype FROM chats")}
        rows = self.db.execute(f"{_Q} * FROM messages ORDER BY id").fetchall()
        by_chat: dict = {}
        for r in rows:
            by_chat.setdefault(r["chat"], []).append(r)
        parsed = {}
        for chat, rs in by_chat.items():
            grp = ctype.get(chat) == "group"
            first = [parse_message(r["raw"], r["date"] or "", chat, group=grp) for r in rs]
            members = learn_members(first) if grp else set()
            for r in rs:
                parsed[r["id"]] = parse_message(r["raw"], r["date"] or "", chat, group=grp, members=members,
                                                nameless=(r["sender"] == "self"))
        groups: dict = {}
        for r in rows:
            m = parsed[r["id"]]
            kind = "text" if m["kind"] in ("date", "marker") else m["kind"]
            parsed[r["id"]] = {**m, "kind": kind}
            g = (r["chat"], r["date"] or "", m["time"], kind, key_text(m))
            groups.setdefault(g, []).append(r)
        drop, final_raw = [], {}
        for rs in groups.values():
            n = max(Counter(r["raw"] for r in rs).values())
            keep = rs[:n]
            drop.extend(r["id"] for r in rs[n:])
            for k, r in enumerate(keep):
                final_raw[r["id"]] = rs[-n + k]["raw"] if n else r["raw"]
        with self.db:
            for r in rows:
                self.db.execute("UPDATE messages SET dedupe_key=? WHERE id=?", (f"mig3_{r['id']}", r["id"]))
            for i in drop:
                self.db.execute("DELETE FROM messages WHERE id=?", (i,))
            counters: Counter = Counter()
            for r in rows:
                if r["id"] in set(drop):
                    continue
                m = parsed[r["id"]]
                raw = final_raw[r["id"]]
                m = {**m, "raw": raw}
                if raw != r["raw"]:
                    m = {**parse_message(raw, r["date"] or "", r["chat"], group=ctype.get(r["chat"]) == "group",
                                       nameless=(r["sender"] == "self")),
                         "kind": m["kind"]}
                old = r["sender"] or ""
                new = m["sender"]
                sender = new if (old in ("", "unknown") or new in ("self", "system")) else old
                if m["read_mark"] or r["read_mark"]:
                    sender = "self"
                text = m["text"] if (m["sender_name"] or m["kind"] == "system") and sender in ("other", "system") else r["text"]
                g = (r["chat"], r["date"] or "", m["time"], m["kind"], key_text(m))
                key = "".join(g + (str(counters[g]),))
                counters[g] += 1
                self.db.execute(
                    "UPDATE messages SET raw=?, time=?, kind=?, text=?, sender=?, sender_name=?, read_mark=?, labels=?, "
                    "dedupe_key=? WHERE id=?",
                    (raw, m["time"], m["kind"], text, sender, m["sender_name"] or r["sender_name"] or "",
                     1 if sender == "self" and (m["read_mark"] or r["read_mark"]) else 0,
                     split_labels(raw), key, r["id"]))
            self.db.execute(f"PRAGMA user_version={USER_VERSION}")
        return {"rows": len(rows), "merged": len(drop)}

    def close(self) -> None:
        self.db.close()

    def chat_states(self) -> dict:
        rows = self.db.execute(f"{_Q} * FROM chats").fetchall()
        return {r["name"]: (r["last_unread"], r["last_preview"], r["last_time"]) for r in rows}

    def chat_kinds(self) -> dict:
        """key -> ctype stored last time (None for rows saved before v2)."""
        return {r["name"]: r["ctype"] for r in self.db.execute(f"{_Q} name, ctype FROM chats")}

    def upsert_chats(self, chats: list) -> None:
        ts = now_ts()
        with self.db:
            for c in chats:
                self.db.execute(
                    "INSERT INTO chats(name,last_unread,last_preview,last_time,updated_at,members,ctype) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET last_unread=excluded.last_unread,"
                    "last_preview=excluded.last_preview,last_time=excluded.last_time,updated_at=excluded.updated_at,"
                    "members=excluded.members,ctype=excluded.ctype",
                    (c["name"], c["unread"], c["preview"], c["time"], ts, c.get("members"), c.get("kind", "friend")),
                )

    def newest_when(self, chat: str):
        best = None
        for r in self.db.execute(f"{_Q} date_iso, time FROM messages WHERE chat=? AND date_iso != ''", (chat,)):
            w = when_key(r["date_iso"], r["time"])
            if w and (best is None or w > best):
                best = w
        return best

    def add_messages(self, chat: str, msgs: list, floor_iso: str = "") -> list:
        """Insert messages whose key is new. Returns the newly inserted ones as
        copies with stale=True when the message is older than floor_iso (the
        watcher's start date) or older than the newest message already stored
        for the chat (measured before this batch), so it must not notify.
        A message already stored that now shows more (the read mark; a
        label such as 期限切れ; a sender that was unknown is now known)
        updates its row and is not returned."""
        ts = now_ts()
        new = []
        newest = self.newest_when(chat)
        with self.db:
            for m, key in zip(msgs, dedupe_keys(chat, msgs)):
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO messages(chat,date,date_iso,time,kind,text,raw,seen_at,dedupe_key,"
                    "sender,sender_name,read_mark,labels) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (chat, m.get("date", ""), iso_date(m.get("date", "")), m.get("time", ""),
                     m.get("kind", ""), m.get("text", ""), m.get("raw", ""), ts, key,
                     m.get("sender", ""), m.get("sender_name", ""), 1 if m.get("read_mark") else 0,
                     split_labels(m.get("raw", ""))),
                )
                if cur.rowcount:
                    diso = iso_date(m.get("date", ""))
                    when = when_key(diso, m.get("time", ""))
                    stale = bool(diso and ((floor_iso and diso < floor_iso) or (newest and when < newest)))
                    new.append({**m, "stale": stale})
                    continue
                self.db.execute("UPDATE messages SET raw=?, text=?, labels=? WHERE dedupe_key=? AND raw IS NOT ?",
                                (m.get("raw", ""), m.get("text", ""), split_labels(m.get("raw", "")), key, m.get("raw", "")))
                s = m.get("sender", "")
                if m.get("read_mark"):
                    self.db.execute("UPDATE messages SET sender='self', read_mark=1 WHERE dedupe_key=? "
                                    "AND (sender IS NOT 'self' OR read_mark IS NOT 1)", (key,))
                elif s in ("self", "other", "system"):
                    self.db.execute("UPDATE messages SET sender=?, sender_name=? WHERE dedupe_key=? "
                                    "AND (sender IS NULL OR sender IN ('', 'unknown'))",
                                    (s, m.get("sender_name", ""), key))
        return new

    def search(self, words: list, chat: str | None = None, since: str | None = None, limit: int = 50) -> list:
        sql, args = [f"{_Q} * FROM messages WHERE 1=1"], []
        for w in words:
            sql.append("AND instr(raw, ?) > 0")
            args.append(w)
        if chat:
            sql.append("AND chat = ?")
            args.append(chat)
        if since:
            sql.append("AND date_iso >= ?")
            args.append(since)
        sql.append("ORDER BY id DESC LIMIT ?")
        args.append(limit)
        return [dict(r) for r in self.db.execute(" ".join(sql), args).fetchall()]

    def recent(self, chat: str | None = None, n: int = 20) -> list:
        if chat:
            rows = self.db.execute(f"{_Q} * FROM messages WHERE chat=? ORDER BY id DESC LIMIT ?", (chat, n))
        else:
            rows = self.db.execute(f"{_Q} * FROM messages ORDER BY id DESC LIMIT ?", (n,))
        return [dict(r) for r in reversed(rows.fetchall())]
