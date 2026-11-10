"""Pure parsers for LINE UIA strings. No UIA access here."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter

TIME_RE = re.compile(r"(?:午前|午後)\s?\d{1,2}:\d{2}")
CHAT_TIME_RE = re.compile(
    r"(?:(?:午前|午後)\s?\d{1,2}:\d{2}|\d{1,2}:\d{2}|昨日|[日月火水木金土]曜日|\d{1,2}/\d{1,2}|\d{4}/\d{1,2}/\d{1,2}|[\d/:]+…|(?:\d{4}年)?\d{1,2}月\d{1,2}日)$"
)
DATE_RE = re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日(?:\s?\([日月火水木金土]\)|（[日月火水木金土]）)?$")


# Accounts that are not people. The list row has no mark for this, so it is a
# name list (extend it with official_names.txt in the data folder).
DEFAULT_OFFICIAL = frozenset({"LINE", "LINE Pay", "Service Messages", "LINE VOOM", "LINE Official Account"})
MEMBERS_RE = re.compile(r"\d+人")


def _count(s: str):
    return int(s.replace(",", "")) if re.fullmatch(r"\d[\d,]*", s.strip()) else None


def parse_chat_row(raw: str, official=DEFAULT_OFFICIAL) -> dict:
    """'name, unread, preview, time' -> dict with name, unread, preview, time,
    members, kind (friend / group / openchat / official), raw.

    Shapes seen: group 'name, N人, [unread,] preview, time' (an unnamed group
    has several names joined by ', '); open chat 'name, members, [unread,]
    preview, time, name' (the name is repeated at the end).
    Unread is taken only from the field after the name/members when it is a
    pure integer. A name that itself contains ', ' can be wrong (see raw).
    """
    parts = raw.split(", ")
    kind, members = "friend", None
    if len(parts) >= 4 and parts[-1] == parts[0] and CHAT_TIME_RE.match(parts[-2].strip()):
        kind = "openchat"
        parts.pop()
        members = _count(parts[1])
        name, rest = parts[0], parts[2:] if members is not None else parts[1:]
    else:
        gi = next((i for i in range(1, len(parts)) if MEMBERS_RE.fullmatch(parts[i].strip())), None)
        if gi is not None:
            kind, members = "group", int(parts[gi].strip()[:-1])
            name, rest = ", ".join(parts[:gi]), parts[gi + 1:]
        else:
            name, rest = (parts[0] if parts else ""), parts[1:]
    time_s = ""
    if rest and CHAT_TIME_RE.match(rest[-1].strip()) and (len(rest) >= 2 or kind != "friend" or len(parts) >= 2):
        time_s = rest.pop().strip()
    unread = 0
    if rest and re.fullmatch(r"\d+", rest[0].strip()):
        unread = int(rest[0].strip())
        rest = rest[1:]
    if kind == "friend" and name in official:
        kind = "official"
    return {"name": name, "unread": unread, "preview": ", ".join(rest), "time": time_s,
            "members": members, "kind": kind, "raw": raw}


def parse_total_unread(raw: str):
    """'トーク, 347' -> 347 (None if not that shape)."""
    m = re.fullmatch(r"トーク(?:, (\d+))?", raw.strip())
    if not m:
        return None
    return int(m.group(1) or 0)


MARKER_TEXTS = ("ここから未読メッセージ",)
_END_RE = re.compile(r",\s*(" + TIME_RE.pattern + r")(?:,\s*既読(?:\s*\d+)?)?$")
_READ_RE = re.compile(r",\s*既読(?:\s*\d+)?$")


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip()


_OWNER_OVERRIDE = None
_OWNER_CACHE = None


def set_owner_names(names) -> None:
    """Set the owner's LINE display names (config key self_names). None = load from config again."""
    global _OWNER_OVERRIDE, _OWNER_CACHE
    _OWNER_OVERRIDE = None if names is None else frozenset(str(n) for n in names)
    _OWNER_CACHE = None


def owner_names() -> frozenset:
    """The owner's display names: set_owner_names(), else config.json self_names in the data dir, else none."""
    global _OWNER_CACHE
    if _OWNER_OVERRIDE is not None:
        return _OWNER_OVERRIDE
    if _OWNER_CACHE is None:
        names: list = []
        try:
            from .store import home_dir
            cfg = json.loads((home_dir() / "config.json").read_text(encoding="utf-8"))
            names = [str(n) for n in (cfg.get("self_names") or [])]
        except (OSError, ValueError, AttributeError):
            pass
        _OWNER_CACHE = frozenset(names)
    return _OWNER_CACHE


VOLATILE_RE = re.compile(r"(?:,\s*)?(?:期限切れ|既読(?:\s*\d+)?)(?=,|$)")
_REPLY_RE = re.compile(r"^(.+?)のメッセージへの(.+?)のリプライ。")
_SYSTEM_RES = [re.compile(p) for p in (
    r"^(.+)がグループを退会しました。?$",
    r"^(.+)が(.+)をグループに追加しました。?$",
    r"^(.+)がグループに参加しました。?$",
    r"^(.+)が(.+)をグループから退会させました。?$",
    r"^グループ通話が(?:開始されました|終了しました)。?(?:, 参加)?$",
    r"^(?:.*, )?ノートを見る$",
)]


def split_labels(s: str) -> str:
    """The volatile labels (期限切れ, 既読 N) found in s, joined by ','."""
    return ",".join(x.strip(", ").strip() for x in VOLATILE_RE.findall(s))


def strip_volatile(s: str) -> str:
    return VOLATILE_RE.sub("", s)


def parse_message(raw: str, current_date: str = "", title: str = "", group: bool = False,
                  members=frozenset(), nameless: bool = False) -> dict:
    """One Text element name in a chat -> dict.

    kind: date / marker / system / text / photo / file / video / other /
    sticker_or_image. sender: 'system' for join/leave/call/note rows; 'self'
    when the row ends with ', 既読' (only the owner's own rows do) or a reply
    whose writer is the owner; 'other' when it starts with '<title>, ' (1:1) or,
    in a group, with a member name; else 'unknown' (a follow-up of the same
    person has no name and, before it is read, looks like a row of the owner;
    resolve_senders / the pixel check decide). Date and marker rows have sender
    ''. text has the name prefix and the read mark removed. In a group the
    first field is taken as the member's name; name_known says whether it is
    in `members` (learned from other rows); nameless=True forces 'no name'.
    names: member names seen in a reply / system row (for learning).
    """
    s = raw.strip()
    base = {"date": current_date, "text": "", "time": "", "raw": raw, "sender": "",
            "sender_name": "", "read_mark": False, "names": (), "name_known": True}
    if DATE_RE.match(s):
        return {**base, "date": s, "kind": "date"}
    if s in MARKER_TEXTS:
        return {**base, "kind": "marker"}
    body, time_s = s, ""
    m = _END_RE.search(s)
    if m:
        time_s = m.group(1)
        body = s[: m.start()]
    read_mark = bool(_READ_RE.search(s)) if m else False
    if not m:
        r = _READ_RE.search(s)
        if r:
            read_mark, body = True, s[: r.start()]
    for rx in _SYSTEM_RES:
        sm = rx.match(body)
        if sm:
            return {**base, "kind": "system", "sender": "system", "text": body, "time": time_s,
                    "names": tuple(sm.groups())}
    sender, sender_name, known, names = "unknown", "", True, ()
    rm = _REPLY_RE.match(body)
    if rm:
        names = (rm.group(1), rm.group(2))
        sender_name = rm.group(2)
        sender = "self" if sender_name in owner_names() else "other"
    if read_mark:
        sender = "self"
        sender_name = sender_name if rm else ""
    elif rm:
        pass
    elif group and not nameless:
        fields = body.split(", ")
        cand = fields[0].strip()
        if cand in owner_names():
            sender = "self"
        elif len(fields) >= 2 and cand and len(cand) <= 40:
            sender, sender_name, known = "other", cand, cand in members
            body = ", ".join(fields[1:])
    elif title and (body.startswith(title + ", ") or body == title + ","):
        sender, sender_name = "other", title
        body = body[len(title) + 2:] if body != title + "," else ""
    kind = "text"
    if body == "":
        kind = "sticker_or_image"
    elif body == "写真" or body.startswith("写真, "):
        kind = "photo"
    elif body.startswith("ファイル, "):
        kind = "file"
    elif body.startswith("動画, "):
        kind = "video"
    elif body in ("スタンプ", "ボイスメッセージ") or body.startswith("スタンプ, "):
        kind = "other"
    return {**base, "text": body, "time": time_s, "kind": kind, "sender": sender,
            "sender_name": sender_name, "read_mark": read_mark, "names": names, "name_known": known}


def learn_members(msgs: list) -> set:
    """Member names of a group, learned from one read: names in reply /
    system rows, and first-field names that start two or more rows."""
    names = set()
    count: Counter = Counter()
    for m in msgs:
        names.update(n for n in m.get("names", ()) if n not in owner_names())
        if m["sender"] == "other" and m["sender_name"] and not m.get("names"):
            count[m["sender_name"]] += 1
    names.update(n for n, c in count.items() if c >= 2)
    return names


def resolve_senders(msgs: list, owner_only: bool = False, pixel=None) -> list:
    """Fill sender for 'unknown' rows, in place, in screen order.

    Order: (name prefix / read mark were done by parse_message) -> chat where
    the owner is the only member -> follow-up of an 'other' row in the same
    minute -> pixel check. `pixel(i)` returns 'self'/'other'/'unknown' for
    row i (or is None). When a pixel answer is definite it wins over the
    inherited guess (a row of the owner sent in the same minute as the other
    person's row looks identical in text). Date / marker rows reset the chain.
    """
    prev = None
    for i, m in enumerate(msgs):
        if m["kind"] in ("date", "marker", "system"):
            prev = None
            continue
        if m["sender"] == "unknown":
            if owner_only:
                m["sender"] = "self"
            else:
                guess = "other" if (prev and prev["sender"] == "other" and m["time"]
                                    and prev["time"] == m["time"]) else ""
                px = pixel(i) if pixel else "unknown"
                m["sender"] = px if px in ("self", "other") else (guess or "unknown")
        prev = m
    return msgs
