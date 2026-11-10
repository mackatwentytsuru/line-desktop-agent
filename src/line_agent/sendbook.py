"""Confirm tokens and the send ledger (plain files, no UI).

Files under the data folder (store.home_dir()):
  config.json        optional: {"send_allowlist": [...], "self_names": [...], "idle_s": 3, "idle_wait_s": 30,
                     "chrome_profile": "<path>"}  (send_allowlist empty/missing = sending disabled)
  send_tokens.json   one-time confirm tokens (only hashes are kept)
  send_ledger.jsonl  append-only: one line per state change of a send
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Optional

# Sends go only to chats listed in config.json "send_allowlist". Empty or missing = sending is disabled.
ENABLE_HINT = ('config.json に {"send_allowlist": ["<送ってよいトークの名前>"]} を書くと、その相手にだけ送れる'
               "(いまは送信が無効)。まず自分だけのテスト用トークで試すこと。")
TOKEN_TTL_S = 300
UNRESOLVED = ("sending", "unknown")


def load_config(home: Path) -> dict:
    p = Path(home) / "config.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def allowlist(cfg: dict) -> tuple:
    return tuple(str(t) for t in (cfg.get("send_allowlist") or []))


def refusal_text(title: str, cfg: dict) -> str:
    """Why a send to title is refused (the dry-run shows this)."""
    al = allowlist(cfg)
    if not al:
        return f"send_allowlist が空なので送信は無効です。{ENABLE_HINT}"
    return f"{title!r} は送ってよい相手ではありません(許可: {list(al)})"


def is_allowed(title: str, cfg: dict) -> bool:
    return title in allowlist(cfg)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def payload_hash(target: str, text: str) -> str:
    return _h(target + "\x1f" + text)


class TokenBook:
    def __init__(self, home: Path, clock=time.time):
        self.path = Path(home) / "send_tokens.json"
        self.clock = clock

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def issue(self, target: str, text: str) -> str:
        token = secrets.token_hex(4)
        d = self._load()
        now = self.clock()
        d = {k: v for k, v in d.items() if v["expires"] > now and not v["used"]}
        d[_h(token)] = {"payload": payload_hash(target, text), "created": now,
                        "expires": now + TOKEN_TTL_S, "used": False}
        self._save(d)
        return token

    def consume(self, token: str, target: str, text: str) -> None:
        """Marks the token used. Raises ValueError when it does not fit."""
        d = self._load()
        rec = d.get(_h(token or ""))
        if rec is None:
            raise ValueError("unknown confirm token (run the dry-run first)")
        if rec["used"]:
            raise ValueError("this confirm token was already used")
        if rec["expires"] <= self.clock():
            raise ValueError("the confirm token expired (5 minutes); run the dry-run again")
        if rec["payload"] != payload_hash(target, text):
            raise ValueError("the confirm token was made for a different target or text")
        rec = dict(rec, used=True)
        self._save(dict(d, **{_h(token): rec}))


class Ledger:
    def __init__(self, home: Path):
        self.path = Path(home) / "send_ledger.jsonl"

    def entries(self) -> list:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def latest(self) -> dict:
        """id -> latest entry."""
        out: dict = {}
        for e in self.entries():
            out[e["id"]] = e
        return out

    def write(self, sid: str, state: str, **info) -> dict:
        e = {"id": sid, "state": state, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **info}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return e

    def unresolved_for(self, target: str, text: str) -> Optional[dict]:
        ph = payload_hash(target, text)
        for e in self.latest().values():
            if e.get("payload") == ph and e["state"] in UNRESOLVED:
                return e
        return None

    def any_unresolved(self) -> list:
        return [e for e in self.latest().values() if e["state"] in UNRESOLVED]

    def resolve(self, sid: str, state: str) -> dict:
        if state not in ("sent", "failed"):
            raise ValueError("resolve to sent or failed only")
        cur = self.latest().get(sid)
        if cur is None:
            raise ValueError(f"no ledger entry {sid}")
        info = {k: v for k, v in cur.items() if k not in ("id", "state", "at")}
        return self.write(sid, state, resolved_by_owner=True, **info)
