# Chrome 版(LINE 公式の Chrome 拡張を CDP で動かす第 2 の道、11-10)

デスクトップ版(Qt)は添付の保存を裏でできなかった(docs/学んだこと.md 段 8)。そこで、LINE 公式の Chrome 拡張(id `ophjlpahpchlmihnnnihgmmeilfjmjjc`)を入れた専用 Chrome を、Chrome DevTools Protocol(CDP)で**ページの中から**動かす。
実体: `src/line_agent/chrome/`(cdp.py = 小さな CDP クライアント、page.py = ページの部品、chrome_reader.py = 読み、chrome_actions.py = 開く・履歴・保存・送信、backend.py = 道の選び方、cli_chrome.py = CLI)。

## 前提
- 専用 Chrome(プロフィールの置き場は環境変数 `LINE_AGENT_CHROME_PROFILE` か config.json の `chrome_profile`。無ければ `%LOCALAPPDATA%\line-desktop-agent\chrome-profile`): `chrome.exe --user-data-dir="<プロフィールの置き場>" --remote-debugging-port=9233`(待ち受けは既定の 127.0.0.1 のまま。広げない)。拡張にログイン済み。
- 起動: `powershell -NoProfile -File scripts\start_line_chrome.ps1`(9233 番が答えていれば何もしない。同じプロフィールの Chrome が動いているのに答えないときは、2 つ目を起動せず止まる。最小化で起動する)。
- Python は `.venv` に `websocket-client` だけ追加(requirements.txt)。

## 使い方(CLI。`$env:PYTHONPATH='src'` で `python -m line_agent ...`)
| コマンド | 内容 |
|---|---|
| `chrome-list` | トーク一覧(名前・種類・未読・最後の一言・時刻) |
| `chrome-open <名前>` | トークを開く(完全一致。同名が 2 つあれば止まる。開くと既読が付く) |
| `chrome-read <名前> --last N` | 開いて読み、DB に保存(重複は Store の決まりで捨てる) |
| `chrome-history <名前> [--until-date YYYY-MM-DD]` | 上へ送って古い履歴を読み込み、保存 |
| `chrome-save <名前> --match <文字> [--at "YYYY-MM-DD HH:MM"]` | 写真・動画・ファイルを `%USERPROFILE%\Downloads\LINE保存` に保存。パス・大きさ・SHA256 を返す |
| `chrome-send 一括送信 --text "..."` | 下見(合言葉が出る)。`--confirm <合言葉>` を付けると本番 |

`--all-since` は作っていない(複数まとめての保存は、明示した 1 件ずつの決まりを優先)。

## MCP
- `line_status` が `backend`(chrome / pc)・理由・Chrome 版にログイン済みか(`chrome.logged_in`)を返す。
- 道の選び方: 環境変数 `LINE_AGENT_BACKEND` > `config.json` の `"backend": "pc"|"chrome"|"auto"` > auto(9233 番が答え、LINE 拡張のページがあり、ログイン済みなら chrome。違えば pc)。
- `line_list_chats` / `line_read_chat` / `line_open_chat` / `line_history` / `line_draft_send` / `line_send` / `line_save_attachment` は、chrome のとき Chrome 版を使う。`line_save_attachment(chat, match, at)` は本当に保存する(`.claude/settings.json` では「毎回たずねる」のまま)。
- 注意: Chrome 版の `line_read_chat` は、窓が閉じていても自分でトークを開く(PC 版は開かずにエラー)。開くと既読が付く(本人は了承済み)。

## 守り(コードとテストで固定)
- 入力はページの中だけ: `Runtime.evaluate`(`element.click()`)と `Input.insertText` / `Input.dispatchKeyEvent`。物理のマウス・キー・カーソル・前面の切り替えは使わない(ctypes・Win32・UIA を chrome/ に入れない)。
- 使える CDP のメソッドは名簿(`cdp.py` の `ALLOWED_METHODS`)だけ。`Page.bringToFront` / `Target.activateTarget` / Network / Storage / Cookie は名簿に無く、送る前に断る。
- 押せる部品は 3 つだけ: 下の「トーク」タブ(aria-label `Chat`)、一覧の行の `Go chatroom`、メッセージの「保存」(文字が `保存` と完全一致のボタン。「名前を付けて保存」「転送」は押さない)。削除・送信取消・通報・ブロック・退出・設定・転送・Keep・通話・友だち追加・スタンプショップ・支払いは、探すコードも無い。
- 拡張の Cookie・IndexedDB・localStorage は読まない。LINE のサーバーへ自分で通信しない。非公式のプロトコルのライブラリは使わない。
- 送信: `send_allowlist` の相手だけ(`sendbook` の許可名簿・合言葉 5 分 1 回・台帳をデスクトップ版と共通で使う)。ページに触る前に STOP・許可名簿・文面・台帳を確かめる。入力欄が空でなければ断る。入れた文字を読み戻して一致を確かめてから Enter(CDP のキー)。送った後、最後の行が自分の発言として読めたら sent、欄が空にならなければ failed(自分の入れた文字だけ消す)、行が見えなければ unknown(台帳に残り、次の送信を全部断る。自動で送り直さない)。
- 試験: `tests/test_chrome.py`(にせものの CDP・ページ)、`tests/test_chrome_safety.py`(ソースの文字で、OS を動かす道・名簿外の CDP・押す部品を確かめる)。PC 版の `tests/test_no_actions.py` は chrome/ を見ない(代わりに上の試験)。

## 裏・最小化でも動くか(11-10 に実測)
- 窓が最小化されると、**ページは hidden になり、描画(requestAnimationFrame)が止まる**。そこで接続のたびに `Emulation.setFocusEmulationEnabled(true)` を送る。すると最小化中でも visible・focus になり、読み・開く・履歴・保存・送信が全部動いた。接続を閉じると元に戻る(本人が Chrome を触るときに影響しない)。
- 20 ms ごとの測定(`scripts/front_probe.py`): 前面の窓(全プロセス)もカーソルも、読み・開く・履歴・保存 4 種・送信 2 通の間に一度も変わらなかった。最小化にした直後、OS が前面に選んだのは別の Chrome 窓で、そのまま変わらなかった。

## Chrome 版が持つ履歴
- デスクトップ版より短い。グループA は 10/28 まで、友だちAは 10/30 まで(デスクトップ版は 9/8、3/14 まで持っている)。上へ送ると 3 回続けて増えなくなって止まる。古い分が要るときは PC 版の `history` を使う。
- 写真は 1 通で複数枚ありうる。1 枚ずつ別のダウンロードになり、**終わる順番は毎回変わる**ので、`_1` `_2` の番号は投稿の順と一致しない。画像は 1108×1477 の JPEG(約 200〜240 KB)。LINE のサーバーに載っている版で、スマホの原寸かは、元の写真と比べられないので分からない。

## 版が変わったとき
- クラス名の後ろの hash(`gnb-module__button_action__aTdj7`)は版で変わる。コードは `[class*="chatroom-module__"]` の部分一致・aria-label・構造(`.message_list`、`data-timestamp`、`data-direction="reverse"` が自分)で探している。読めなくなったら `page.py` の JS を直す。
