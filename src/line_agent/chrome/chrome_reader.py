"""Chrome 版の読み取り: ページの記録(page.py)を、既存の Store が受け取れる dict にそろえる。

形は parse.parse_message と同じ(date=「2026年11月10日」、time=「午後 4:31」、sender=self/other/system、
kind=text/photo/video/file/sticker/system、text、raw)。ファイル・動画・写真の text も、デスクトップ版の書き方に合わせた
(ファイル: 「ファイル, [期限切れ, ]名前」、動画: 「動画, 動画の長さ, N秒[, 期限切れ]」、写真: 「写真」)ので、同じ行に当たれば
Store の重複の決まりでデスクトップ版と同じ行になる。追加の欄(ts / sel / file_name / file_size / expired / n_images)は Store が使わない。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .page import LinePage, PageError

MEDIA = {"image": "photo", "video": "video", "file": "file"}


class ChromeChatNotOpen(Exception):
    pass


def jp_date(ts_ms: int) -> str:
    d = datetime.fromtimestamp(ts_ms / 1000)
    return f"{d.year}年{d.month}月{d.day}日"


def jp_time(ts_ms: int) -> str:
    d = datetime.fromtimestamp(ts_ms / 1000)
    return f"{'午前' if d.hour < 12 else '午後'} {d.hour % 12 or 12}:{d.minute:02d}"


def _seconds(play: str) -> Optional[int]:
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", (play or "").strip())
    if not m:
        return None
    return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def to_message(rec: dict) -> Optional[dict]:
    """ページの 1 件 -> Store 用の dict。日付の区切りは None。"""
    if rec.get("t") != "msg":
        return None
    kind = rec["kind"]
    ts = int(rec.get("ts") or 0)
    time_s = rec.get("time") or (jp_time(ts) if ts else "")
    own = rec.get("direction") == "reverse"
    name = rec.get("name", "")
    extra: dict = {}
    sender = "self" if own else "other"
    if kind in ("text", "reply"):
        out_kind, text = "text", rec.get("text", "")
    elif kind == "image":
        out_kind, text = "photo", "写真"
        extra["n_images"] = max(1, len(rec.get("imageMids") or []))
    elif kind == "file":
        out_kind = "file"
        label = "期限切れ, " if rec.get("expired") else ""
        text = f"ファイル, {label}{rec.get('fileName', '')}"
        extra.update(file_name=rec.get("fileName", ""), file_size=rec.get("fileSize", ""),
                     expired=bool(rec.get("expired")))
    elif kind == "video":
        out_kind = "video"
        sec = _seconds(rec.get("playTime", ""))
        text = "動画" + (f", 動画の長さ, {sec}秒" if sec is not None else "")
        extra["play_time"] = rec.get("playTime", "")
    elif kind == "sticker":
        out_kind, text = "sticker", ""
    elif re.search(r"system|notice|announce", kind, re.I):
        out_kind, text, sender = "system", rec.get("text", ""), "system"
    else:
        out_kind, text = "other", rec.get("text", "")
    read = rec.get("readCount", "")
    parts = ([name] if sender == "other" and name else []) + ([text] if text else []) + [time_s]
    if sender == "self" and read:
        parts.append(read if "既読" in read else f"既読 {read}")
    return {"date": jp_date(ts) if ts else "", "time": time_s, "sender": sender,
            "sender_name": name if sender != "system" else "", "kind": out_kind, "text": text,
            "raw": ", ".join(parts), "read_mark": bool(read and "既読" in read), "ts": ts,
            "sel": rec.get("selectId", ""), "mid": rec.get("mid", ""), "can_save": bool(rec.get("saveButton")),
            **extra}


def messages_from_records(recs: list) -> list:
    """DOM は新しい順(column-reverse)。古い順にして、日付の区切りを外す。"""
    out = [m for m in (to_message(r) for r in reversed(recs)) if m]
    return sorted(out, key=lambda m: m["ts"]) if all(m["ts"] for m in out) else out


def kind_of_chat(mid: str, members: Optional[int]) -> str:
    if mid[:1] in ("C", "R"):
        return "group"
    return "friend" if mid[:1] == "U" else "other"


def list_chats(page: LinePage) -> dict:
    chats = []
    for c in page.list_chats():
        chats.append({"name": c["name"], "kind": kind_of_chat(c["mid"], c.get("members")), "unread": c["unread"],
                      "preview": c["preview"], "time": c["time"], "members": c.get("members"), "mid": c["mid"]})
    return {"chats": chats, "total_unread": sum(c["unread"] for c in chats)}


def read_open_chat(page: LinePage, name: str) -> list:
    """開いているトークを読む。名前が違えば止まる(違うトークを name として保存しない)。"""
    r = page.read_records()
    if r.get("chat") != name:
        raise ChromeChatNotOpen(f"開いているトークは「{r.get('chat')}」です(「{name}」ではありません)")
    return messages_from_records(r["recs"])


def read_chat(page: LinePage, name: str, last: int = 0, open_first: bool = True) -> list:
    if open_first and page.current_room() != name:
        page.open_chat(name)
    msgs = read_open_chat(page, name)
    return msgs[-last:] if last else msgs


__all__ = ["PageError", "ChromeChatNotOpen", "list_chats", "read_chat", "read_open_chat", "messages_from_records", "to_message"]
