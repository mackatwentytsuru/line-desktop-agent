"""LINE 拡張のページ(React 製)を、ページの中の JavaScript で読む・動かす部品。

守り:
- 押してよい部品は ALLOWED_CONTROLS の 5 つだけ(下の _control)。ほかの部品を押す道は作らない。
  削除・送信取り消し・通報・ブロック・退出・設定・転送・Keep・通話・友だち追加・スタンプショップ・支払いは名簿に無い。
- クラス名の後ろの hash(`gnb-module__button_action__aTdj7` の末尾)は版で変わるので、`[class*="chatroom-module__"]` の
  ような部分一致と、aria-label / 構造で探す。
"""
from __future__ import annotations

import json
import time
from typing import Optional

from .cdp import CdpClient, CdpError, connect_page

ALLOWED_CONTROLS = ("nav_chat", "open_chat", "save_attachment", "scroll_down_button", "none")

PRELUDE = r"""
const sleep = ms => new Promise(r => setTimeout(r, ms));
const q = (r, s) => r.querySelector(s);
const qa = (r, s) => [...r.querySelectorAll(s)];
const txt = e => e ? (e.textContent || '').replace(/‌/g, '').trim() : '';
const listScroller = () => {
  const inner = q(document, '[class*="chatlist-module__chatlist_inner"]');
  if (!inner) return null;
  return qa(inner, 'div').find(d => (d.style.overflow || '').includes('overlay') || ['auto', 'scroll'].includes(getComputedStyle(d).overflowY)) || null;
};
const itemInfo = it => {
  const mc = txt(q(it, '[class*="chatlistItem-module__member_count"]'));
  const m = mc.match(/\((\d[\d,]*)\)/);
  const t = q(it, 'time');
  return {
    mid: it.dataset.mid || '',
    name: txt(q(it, '[class*="chatlistItem-module__title_box"] pre')),
    members: m ? parseInt(m[1].replace(/,/g, ''), 10) : null,
    time: txt(t), datetime: t ? (t.getAttribute('datetime') || '') : '',
    preview: txt(q(it, '[class*="chatlistItem-module__description"] p')),
    unread: parseInt(txt(q(it, '[class*="chatlistItem-module__message_count"]')).replace(/,/g, ''), 10) || 0,
    top: parseFloat(it.style.top) || 0,
  };
};
const items = () => qa(document, '[class*="chatlistItem-module__chatlist_item"]');
const scanChats = async () => {
  const sc = listScroller();
  if (!sc) return null;
  const seen = new Map();
  sc.scrollTop = 0; await sleep(150);
  for (let i = 0; i < 80; i++) {
    items().forEach(it => { if (!seen.has(it.dataset.mid)) seen.set(it.dataset.mid, itemInfo(it)); });
    if (sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2) break;
    sc.scrollTop += Math.max(100, sc.clientHeight - 80); await sleep(150);
  }
  return {sc, list: [...seen.values()].sort((a, b) => a.top - b.top)};
};
const roomName = () => txt(q(document, '[class*="chatroomHeader-module__name"]'));
const msgNodes = () => { const ml = q(document, '.message_list'); return ml ? [...ml.children] : []; };
"""

STATUS_JS = r"""
(() => {
  const hdr = q(document, '[class*="chatroomHeader-module__name"]');
  return {
    url: location.href.split('#')[0], hash: location.hash, visibility: document.visibilityState,
    has_focus: document.hasFocus(),
    logged_in: !!q(document, '[class*="gnb-module__gnb"]') && !!q(document, 'button[aria-label="Chat"]'),
    room: hdr ? txt(hdr) : null, has_input: !!q(document, 'textarea-ex'),
    n_messages: msgNodes().length,
  };
})()
"""

LIST_JS = r"""
(async () => {
  const s = await scanChats();
  if (!s) return {error: 'nolist'};
  s.sc.scrollTop = 0;
  return {chats: s.list};
})()
"""

NAV_CHAT_JS = r"""
(async () => {
  if (location.hash.startsWith('#/chats')) return {ok: true, already: true};
  const b = qa(document, '[class*="gnb-module__gnb"] button').find(x => x.getAttribute('aria-label') === 'Chat');
  if (!b) return {error: 'no chat tab button'};
  b.click();
  for (let i = 0; i < 30; i++) { await sleep(150); if (listScroller()) return {ok: true}; }
  return {error: 'chat list did not appear'};
})()
"""

OPEN_JS = r"""
(async () => {
  const want = __NAME__;
  const s = await scanChats();
  if (!s) return {error: 'nolist'};
  const hits = s.list.filter(c => c.name === want);
  if (hits.length === 0) return {error: 'notfound'};
  if (hits.length > 1) return {error: 'ambiguous', n: hits.length};
  const hit = hits[0];
  let it = null;
  for (let i = 0; i < 20 && !it; i++) {
    s.sc.scrollTop = Math.max(0, hit.top - 60); await sleep(200);
    it = items().find(x => x.dataset.mid === hit.mid);
  }
  if (!it) return {error: 'item not rendered'};
  if (roomName() === want && location.hash.includes(hit.mid)) { s.sc.scrollTop = 0; return {ok: true, mid: hit.mid, already: true}; }
  const b = q(it, 'button[aria-label="Go chatroom"]');
  if (!b) return {error: 'no open control'};
  b.click();
  for (let i = 0; i < 40; i++) {
    await sleep(200);
    if (roomName() === want && location.hash.includes(hit.mid) && q(document, '.message_list')) break;
    if (i === 39) return {error: 'chat did not open'};
  }
  let last = -1, stable = 0;
  for (let i = 0; i < 30 && stable < 3; i++) { await sleep(250); const n = msgNodes().length; stable = (n === last && n > 0) ? stable + 1 : 0; last = n; }
  s.sc.scrollTop = 0;
  return {ok: true, mid: hit.mid, n_messages: last};
})()
"""

READ_JS = r"""
(() => {
  const ml = q(document, '.message_list');
  if (!ml) return {error: 'noroom'};
  const plain = e => { const c = e.cloneNode(true); qa(c, 'img[alt]').forEach(i => i.replaceWith(document.createTextNode(i.alt === '(emoji)' ? '￼' : i.alt))); return (c.textContent || '').replace(/‌/g, ''); };
  const out = [];
  for (const k of ml.children) {
    const cls = k.className.toString();
    if (cls.includes('messageDate-module__date_wrap')) { out.push({t: 'date', ts: +k.dataset.timestamp || 0, label: k.dataset.messageContent || ''}); continue; }
    if (!cls.includes('message-module__message')) continue;
    const inner = q(k, '[class*="message-module__content_inner"]');
    const wrap = inner ? inner.firstElementChild : null;
    const wc = wrap ? wrap.className.toString() : '';
    const kind = /replyMessageContent/.test(wc) ? 'reply' : /textMessageContent/.test(wc) ? 'text'
      : /imageMessageContent/.test(wc) ? 'image' : /videoMessageContent/.test(wc) ? 'video'
      : /fileMessageContent/.test(wc) ? 'file' : /stickerMessageContent/.test(wc) ? 'sticker'
      : 'other:' + (wc.split(' ')[0] || cls.split(' ')[0]).split('-module')[0];
    const un = q(k, 'pre[class*="username"]');
    const prefix = (k.dataset.messageContentPrefix || '').replace(/^\d{1,2}:\d{2}\s*/, '').trim();
    const rec = {t: 'msg', kind, ts: +k.dataset.timestamp || 0, selectId: k.dataset.messageSelectId || '',
      direction: k.dataset.direction || '', name: (un ? txt(un) : '') || prefix,
      time: txt(q(k, 'time[class*="send_time"]')), readCount: txt(q(k, '[class*="read_count"]')),
      mid: wrap ? (wrap.dataset.messageId || '') : '',
      saveButton: qa(k, '[class*="message-module__action_group"] button').some(b => txt(b) === '保存')};
    if (kind === 'text') rec.text = plain(q(wrap, 'pre[class*="textMessageContent-module__text"]') || wrap).trim();
    else if (kind === 'reply') {
      rec.replyTo = txt(q(wrap, 'button[class*="button_move"] pre[class*="username"]'));
      const body = q(wrap, '[class*="replyMessageContent-module__reply_content"]');
      rec.text = body ? plain(body).trim() : '';
    } else if (kind === 'file') {
      rec.fileName = txt(q(wrap, '[class*="fileMessageContent-module__name"]'));
      rec.fileSize = txt(q(wrap, '[class*="fileMessageContent-module__size"]'));
      const p = q(wrap, '[class*="fileMessageContent-module__period"]');
      rec.expired = !!p && p.dataset.expired === 'true'; rec.period = txt(p);
    } else if (kind === 'video') rec.playTime = txt(q(wrap, '[class*="play_time"]'));
    else if (kind === 'image') rec.imageMids = qa(wrap, '[class*="imageMessageContent-module__item"]').map(i => i.dataset.messageId || '');
    else if (kind.startsWith('other:')) rec.text = (inner ? inner.innerText : '').trim().slice(0, 300);
    out.push(rec);
  }
  return {chat: roomName(), hash: location.hash, n_dom: ml.children.length, recs: out};
})()
"""

HISTORY_STEP_JS = r"""
(async () => {
  const ml = q(document, '.message_list');
  if (!ml) return {error: 'noroom'};
  const before = ml.children.length, h0 = ml.scrollHeight;
  ml.scrollTop = -(ml.scrollHeight);
  let n = before;
  for (let i = 0; i < 24; i++) { await sleep(250); n = ml.children.length; if (n !== before) { await sleep(400); n = ml.children.length; break; } }
  const kids = [...ml.children];
  const stamps = kids.map(k => +k.dataset.timestamp).filter(x => x > 0);
  return {before, after: n, oldest_ts: stamps.length ? Math.min(...stamps) : 0, visibility: document.visibilityState};
})()
"""

BOTTOM_JS = r"""
(async () => {
  const ml = q(document, '.message_list');
  if (!ml) return {error: 'noroom'};
  ml.scrollTop = 0; await sleep(300);
  return {ok: true, scrollTop: ml.scrollTop};
})()
"""

SAVE_CLICK_JS = r"""
(() => {
  const sid = __SID__;
  const k = msgNodes().find(x => x.dataset.messageSelectId === sid);
  if (!k) return {error: 'message not found'};
  const b = qa(k, '[class*="message-module__action_group"] button').find(x => txt(x) === '保存');
  if (!b) return {error: 'no save control on this message'};
  b.click();
  return {ok: true};
})()
"""

INPUT_STATE_JS = r"""
(() => {
  const t = q(document, 'textarea-ex');
  if (!t) return {error: 'noinput'};
  return {raw: t.rawValue === undefined ? null : String(t.rawValue), empty: t.dataset.isEmpty === 'true',
          active: document.activeElement ? document.activeElement.tagName : ''};
})()
"""

INPUT_FOCUS_JS = r"""
(() => { const t = q(document, 'textarea-ex'); if (!t) return {error: 'noinput'}; t.focus(); return {ok: true, active: document.activeElement.tagName}; })()
"""


class PageError(Exception):
    pass


class LinePage:
    """ページを動かす窓口。セッション(CDP)1 本につき 1 つ。"""

    def __init__(self, cdp: CdpClient):
        self.c = cdp

    # --- 低水準 ---
    def _js(self, src: str, **subs: str):
        for k, v in subs.items():
            src = src.replace(f"__{k}__", json.dumps(v, ensure_ascii=False))
        # 毎回かこい(関数の中)に入れる: Runtime.evaluate の const は次の呼び出しに残って「宣言済み」になるため
        r = self.c.evaluate("(async () => {" + PRELUDE + " return await " + src.strip() + "; })()")
        if isinstance(r, dict) and r.get("error"):
            raise PageError(str(r["error"]))
        return r

    def _control(self, which: str, src: str, **subs: str):
        """押す部品は名簿の中だけ。"""
        if which not in ALLOWED_CONTROLS:
            raise PageError(f"{which} は押してよい部品の名簿にありません")
        return self._js(src, **subs)

    # --- 状態 ---
    def status(self) -> dict:
        return self._js(STATUS_JS)

    def require_login(self) -> dict:
        st = self.status()
        if not st.get("logged_in"):
            raise PageError("LINE 拡張にログインしていません(ログイン画面か別のページ)")
        return st

    # --- 一覧・開く ---
    def list_chats(self) -> list:
        self.require_login()
        self._control("nav_chat", NAV_CHAT_JS)
        return self._js(LIST_JS)["chats"]

    def open_chat(self, name: str) -> dict:
        self.require_login()
        self._control("nav_chat", NAV_CHAT_JS)
        try:
            return self._control("open_chat", OPEN_JS, NAME=name)
        except PageError as e:
            msg = str(e)
            if msg == "notfound":
                raise PageError(f"トーク一覧に「{name}」がありません") from None
            if msg == "ambiguous":
                raise PageError(f"「{name}」が一覧に 2 つ以上あります(同名は開きません)") from None
            raise

    def current_room(self) -> Optional[str]:
        return self.status().get("room")

    # --- 読む ---
    def read_records(self) -> dict:
        return self._js(READ_JS)

    def history_step(self) -> dict:
        return self._js(HISTORY_STEP_JS)

    def to_bottom(self) -> dict:
        return self._js(BOTTOM_JS)

    # --- 添付 ---
    def click_save(self, select_id: str) -> dict:
        return self._control("save_attachment", SAVE_CLICK_JS, SID=select_id)

    # --- 入力欄 ---
    def input_state(self) -> dict:
        return self._js(INPUT_STATE_JS)

    def focus_input(self) -> dict:
        return self._js(INPUT_FOCUS_JS)

    def insert_text(self, text: str) -> None:
        """入力欄にフォーカスして、CDP の insertText で入れる(物理のキーボードは使わない)。"""
        self.focus_input()
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            if i:
                self.key("Enter", shift=True)
            if ln:
                self.c.send("Input.insertText", {"text": ln})

    def key(self, key: str, shift: bool = False) -> None:
        code = {"Enter": 13, "Backspace": 8}[key]
        mod = 8 if shift else 0
        base = {"key": key, "code": key, "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code, "modifiers": mod}
        down = dict(base, type="keyDown", **({"text": "\r"} if key == "Enter" else {}))
        self.c.send("Input.dispatchKeyEvent", down)
        self.c.send("Input.dispatchKeyEvent", dict(base, type="keyUp"))

    def press_enter(self) -> None:
        self.key("Enter")

    def backspace(self, n: int) -> None:
        for _ in range(n):
            self.key("Backspace")


def open_session(port: int = 9233, focus_emulation: bool = True) -> LinePage:
    c = connect_page(port)
    c.send("Runtime.enable")
    if focus_emulation:
        try:
            c.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except CdpError:
            pass
    return LinePage(c)


def wait_until(fn, timeout: float, step: float = 0.25):
    end = time.time() + timeout
    while True:
        v = fn()
        if v:
            return v
        if time.time() >= end:
            return None
        time.sleep(step)
