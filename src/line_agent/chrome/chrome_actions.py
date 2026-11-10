"""Chrome 版の動かす操作: 開く・古い履歴・添付の保存・送信。

すべてページの中(CDP)だけで行う。物理のマウス・キーボード・前面の切り替えは使わない。
送信は sendbook(許可名簿・合言葉・台帳)をデスクトップ版と同じに通る。
"""
from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .. import sendbook
from ..store import iso_date
from . import chrome_reader as cr
from .cdp import CdpError, connect_browser
from .page import LinePage, PageError

SENT_WAIT_S = 4.0
HISTORY_MAX_STEPS = 80
HISTORY_STALL = 3
DOWNLOAD_START_S = 20.0
DOWNLOAD_IDLE_S = 90.0
DOWNLOAD_MAX_S = 900.0
MEDIA_KINDS = ("photo", "video", "file")


class ChromeRefused(Exception):
    pass


def stop_file(home: Path) -> Path:
    return Path(home) / "STOP"


def ensure_open(page: LinePage, name: str) -> dict:
    if page.current_room() == name:
        return {"ok": True, "already": True}
    r = page.open_chat(name)
    if page.current_room() != name:
        raise ChromeRefused(f"開いたトークが「{name}」になっていません")
    return r


# --- 古い履歴 ---
def load_history(page: LinePage, name: str, until_date: Optional[str] = None, max_steps: int = HISTORY_MAX_STEPS,
                 sleep: Callable = time.sleep) -> dict:
    ensure_open(page, name)
    stalls, steps, first = 0, 0, None
    last = {}
    while steps < max_steps:
        last = page.history_step()
        steps += 1
        first = first if first is not None else last["before"]
        oldest = last.get("oldest_ts") or 0
        if until_date and oldest and _iso_of(oldest) <= until_date:
            break
        stalls = stalls + 1 if last["after"] == last["before"] else 0
        if stalls >= HISTORY_STALL:
            break
    page.to_bottom()
    oldest = last.get("oldest_ts") or 0
    return {"chat": name, "steps": steps, "loaded_before": first, "loaded_after": last.get("after"),
            "oldest": cr.jp_date(oldest) if oldest else "", "reached_top": stalls >= HISTORY_STALL,
            "page_visibility": last.get("visibility")}


def _iso_of(ts_ms: int) -> str:
    return iso_date(cr.jp_date(ts_ms))


# --- 添付の保存 ---
def downloads_dir() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads" / "LINE保存"


def pick_attachment(msgs: list, match: str, at: Optional[str] = None) -> dict:
    """match = ファイル名(か「写真」「動画」)の全部か一部。at = 「YYYY-MM-DD」「HH:MM」「YYYY-MM-DD HH:MM」。
    ちょうど 1 件に決まらなければ断る。期限切れは断る。"""
    if not match or not match.strip():
        raise ChromeRefused("match が空です")
    media = [m for m in msgs if m["kind"] in MEDIA_KINDS]

    def label(m):
        return m.get("file_name") or m["text"]

    exact = [m for m in media if label(m) == match or m["text"] == match]
    hits = exact or [m for m in media if match in label(m) or match in m["text"]]
    if at:
        hits = [m for m in hits if _at_ok(m, at)]
    if not hits:
        raise ChromeRefused(f"{match!r} に当たる写真・動画・ファイルがありません(開いているトークの読み込み済みの分だけ)")
    if len(hits) > 1:
        desc = [f"{m['date']} {m['time']} {label(m)[:30]}" for m in hits[:6]]
        raise ChromeRefused(f"{len(hits)} 件に当たります: {desc}。--at で日時を足してください")
    m = hits[0]
    if m.get("expired"):
        raise ChromeRefused("期限切れの添付は保存できません(LINE のサーバーに無い)")
    if not m.get("can_save"):
        raise ChromeRefused("この行に「保存」の部品がありません")
    return m


def _at_ok(m: dict, at: str) -> bool:
    from datetime import datetime

    d = datetime.fromtimestamp(m["ts"] / 1000)
    full = d.strftime("%Y-%m-%d %H:%M")
    return full == at or full.startswith(at) or full.endswith(at) or d.strftime("%H:%M") == at


def _safe(s: str, limit: int = 80) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", s).strip(" .")
    return s[:limit] or "LINE"


def _ext_of(path: Path) -> str:
    head = path.open("rb").read(16)
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp":
        return ".mov" if head[8:12] == b"qt  " else ".mp4"
    return ""


def _unique(dest: Path, name: str) -> Path:
    p = dest / name
    stem, ext, n = p.stem, p.suffix, 1
    while p.exists():
        p = dest / f"{stem} ({n}){ext}"
        n += 1
    return p


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _wait_downloads(b, expected: int, clock: Callable = time.time) -> list:
    """Browser.downloadWillBegin / downloadProgress を読んで、expected 個の完了を待つ。完了したものの info を返す。"""
    begun, done = {}, []
    t0 = last = clock()
    while len(done) < expected:
        ev = b.next_event(("Browser.downloadWillBegin", "Browser.downloadProgress"), 0.5)
        now = clock()
        if ev is None:
            if not begun and now - t0 > DOWNLOAD_START_S:
                raise ChromeRefused("ダウンロードが始まりません(LINE のサーバーで期限切れの可能性)")
            if now - last > DOWNLOAD_IDLE_S or now - t0 > DOWNLOAD_MAX_S:
                raise ChromeRefused("ダウンロードが止まりました(時間切れ)")
            continue
        p = ev["params"]
        last = now
        if ev["method"].endswith("WillBegin"):
            begun[p["guid"]] = p
        elif p.get("state") == "completed":
            done.append(p)
        elif p.get("state") == "canceled":
            raise ChromeRefused("ダウンロードが取り消されました")
    return done


def save_attachment(page: LinePage, chat: str, match: str, at: Optional[str] = None, dest: Optional[Path] = None,
                    browser_factory: Optional[Callable] = None) -> dict:
    ensure_open(page, chat)
    msgs = cr.read_open_chat(page, chat)
    m = pick_attachment(msgs, match, at)
    expected = m.get("n_images", 1) if m["kind"] == "photo" else 1
    dest = Path(dest or downloads_dir())
    dest.mkdir(parents=True, exist_ok=True)
    b = (browser_factory or connect_browser)()
    try:
        b.send("Browser.setDownloadBehavior", {"behavior": "allowAndName", "downloadPath": str(dest),
                                               "eventsEnabled": True})
        page.click_save(m["sel"])
        done = _wait_downloads(b, expected)
    finally:
        b.close()
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(m["ts"] / 1000))
    saved = []
    for i, p in enumerate(done):
        src = Path(p["filePath"])
        if m["kind"] == "file":
            name = _safe(m.get("file_name") or f"LINE_{stamp}")
        else:
            ext = _ext_of(src)
            suffix = f"_{i + 1}" if len(done) > 1 else ""
            name = f"{_safe(chat, 30)}_{stamp}{suffix}{ext}"
        target = _unique(dest, name)
        src.rename(target)
        saved.append({"path": str(target), "bytes": target.stat().st_size, "sha256": _sha(target)})
    return {"chat": chat, "kind": m["kind"], "date": m["date"], "time": m["time"], "name": m.get("file_name", ""),
            "shown_size": m.get("file_size", ""), "saved": saved}


# --- 送信 ---
def _precheck(home: Path, chat: str, text: str) -> None:
    """ページに触る前の確かめ(デスクトップ版と同じ決まり)。"""
    cfg = sendbook.load_config(home)
    if stop_file(home).exists():
        raise ChromeRefused("STOP ファイルがあります")
    if not sendbook.is_allowed(chat, cfg):
        raise ChromeRefused(sendbook.refusal_text(chat, cfg))
    if not text or not text.strip():
        raise ChromeRefused("文面が空です")
    if len(text) > 2000:
        raise ChromeRefused("文面が長すぎます(2000 字まで)")
    if sendbook.Ledger(home).any_unresolved():
        raise ChromeRefused("台帳に結果の分からない送信(sending/unknown)が残っています。目で確かめて ledger-resolve してください")


def _prepare_page(page: LinePage, chat: str) -> None:
    page.require_login()
    ensure_open(page, chat)
    if page.current_room() != chat:
        raise ChromeRefused("開いているトークが送り先と違います")
    st = page.input_state()
    if st.get("raw") != "":
        raise ChromeRefused("入力欄が空ではありません(本人の書きかけかもしれない)。触りません")


def prepare(page: LinePage, home: Path, chat: str, text: str, tokens=None) -> dict:
    _precheck(home, chat, text)
    _prepare_page(page, chat)
    tokens = tokens or sendbook.TokenBook(home)
    return {"dry_run": True, "backend": "chrome", "target": chat, "text": text, "confirm": tokens.issue(chat, text),
            "expires_in_s": sendbook.TOKEN_TTL_S, "room": page.current_room()}


def _is_ours(m: Optional[dict], text: str, since_ms: int) -> bool:
    return bool(m) and m["sender"] == "self" and m["kind"] == "text" and cr_norm(m["text"]) == cr_norm(text) \
        and m["ts"] >= since_ms - 60_000


def cr_norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def send(page: LinePage, home: Path, chat: str, text: str, token: str, tokens=None, ledger=None,
         sleep: Callable = time.sleep) -> dict:
    _precheck(home, chat, text)
    _prepare_page(page, chat)
    tokens = tokens or sendbook.TokenBook(home)
    ledger = ledger or sendbook.Ledger(home)
    try:
        tokens.consume(token, chat, text)
    except ValueError as e:
        raise ChromeRefused(str(e)) from None
    sid = uuid.uuid4().hex[:12]
    base = {"target": chat, "text": text, "payload": sendbook.payload_hash(chat, text), "backend": "chrome"}
    ledger.write(sid, "sending", **base)
    started = int(time.time() * 1000)
    entered = False
    try:
        if page.current_room() != chat:
            raise ChromeRefused("送る直前に、開いているトークが変わりました")
        page.insert_text(text)
        sleep(0.3)
        back = page.input_state().get("raw")
        if back != text:
            if back and text.startswith(back):  # 空だった欄に入った自分の文字の一部だけ消す
                page.backspace(len(back))
            raise ChromeRefused(f"入力欄の中身が確認した文面と違います: {back!r}")
        entered = True
        page.press_enter()
    except (ChromeRefused, CdpError, PageError) as e:
        ledger.write(sid, "unknown" if entered else "failed", error=str(e), **base)
        raise ChromeRefused(("unknown: " if entered else "failed: ") + str(e)) from None
    deadline = time.time() + SENT_WAIT_S
    left = text
    while time.time() < deadline and left != "":
        sleep(0.2)
        left = page.input_state().get("raw") or ""
    if left != "":
        cleaned = False
        if left in (text, text + "\n"):
            page.backspace(len(left))
            cleaned = True
        ledger.write(sid, "failed", error=f"Enter のあとも入力欄が空になりません: {left!r}", cleaned=cleaned, **base)
        return {"sent": False, "state": "failed", "left": left, "cleaned": cleaned, "id": sid, "backend": "chrome"}
    row = None
    for _ in range(12):
        rows = cr.read_open_chat(page, chat)
        row = rows[-1] if rows else None
        if _is_ours(row, text, started):
            ledger.write(sid, "sent", **base)
            return {"sent": True, "state": "sent", "last_row": {k: row[k] for k in ("date", "time", "sender", "kind", "text")},
                    "id": sid, "backend": "chrome"}
        sleep(0.5)
    ledger.write(sid, "unknown", error="入力欄は空になったが、最後の行に自分の文面が見えない", last_row=row, **base)
    return {"sent": False, "state": "unknown", "id": sid, "backend": "chrome"}
