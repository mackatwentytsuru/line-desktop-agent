# LINE の MCP サーバー(11-10)

`<このリポジトリ>` で開いた Claude Code のセッションだけで使える(プロジェクト範囲。`.mcp.json` と `.claude/settings.json` はこのリポジトリの中だけ。`~/.claude.json` などは触っていない)。
実体: `src/line_agent/mcp_server.py`(stdio。既存の reader / store / opener / scroller / sender を呼ぶだけの薄い包み)。

## 道具(9 個)
| 道具 | 内容 | 許可 |
|---|---|---|
| line_list_chats(limit=50) | トーク一覧(名前・種類・未読・最後の一言・時刻) | 自動 |
| line_read_chat(name, last=30) | 開いている窓を読む。新しい行は DB に保存 | 自動 |
| line_recent(chat, n=20) | DB の最近の発言 | 自動 |
| line_search(words, chat, since, limit=50) | DB を言葉で探す | 自動 |
| line_status() | 見張り・最後の tick・STOP・置き場所・未対応の機能 | 自動 |
| line_draft_send(chat, text) | 送信の下見。送らない。合言葉(5 分・1 回)を返す | 自動 |
| line_send(chat, text, token) | 本番の送信。許可リストの相手だけ | **毎回たずねる** |
| line_open_chat(name) | 裏でトークを開く | **毎回たずねる** |
| line_history(name, until_date) | 上へ送って古い履歴を読み込み保存 | **毎回たずねる** |

作っていないもの: 削除・転送・通報・設定。
添付の保存(line_save_attachment)は **Chrome 版のときだけ本当に保存する**(docs/Chrome版の使い方.md)。PC 版のときは確かめるだけ。

## 道(backend)
`line_status` が `backend`(chrome / pc)を返す。9233 番の専用 Chrome に LINE 拡張がログイン済みなら chrome、違えば pc。`config.json` の `"backend"` か環境変数 `LINE_AGENT_BACKEND` で固定できる。

## 送るときの流れ
1. line_draft_send で相手・文面・合言葉を得る(相手が `send_allowlist` に無ければ断られる)
2. 本人に相手と文面を見せて「はい」をもらう
3. line_send(同じ chat・同じ text・合言葉)。台帳に sending → sent。最後の行が自分の発言として読めたら sent
- 失敗は `{"ok": false, "error": "..."}`。トレースバックは返さない。

## 再起動・止め方
- 道具が変になったら Claude Code で `/mcp` から line を再接続。コードを直したあとも同じ。
- 初回は「このプロジェクトの MCP を信頼するか」と聞かれる(`enabledMcpjsonServers` に line を入れてある)。
- 非常停止: `scripts\stop_watcher.ps1` か、置き場所の `STOP` ファイル(送信・開く・スクロールも止まる)。
- 単体の動作確認: `.venv\Scripts\python.exe -m pytest -q`。

## 置き場所
`LINE_AGENT_HOME` があればそれ。なければ、Claude アプリの実際の置き場所(`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Local\line-desktop-agent`)があればそれ、なければ `%LOCALAPPDATA%\line-desktop-agent`。見張り(start_watcher.ps1)と同じ規則で、同じ line.db を見る。
