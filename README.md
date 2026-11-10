# line-desktop-agent

自分の LINE を、Claude Code から読んだり(まとめ・検索・返信の下書き)、本人が確かめたときだけ送ったりするための、**個人用の道具**です。公式のクライアントだけを使います。

- LINE for PC(Windows): UI Automation(UIA)で、画面の部品を読む・窓へメッセージを投函する
- LINE の Chrome 拡張: Chrome DevTools Protocol(CDP)で、拡張のページを読む・操作する

## やらないこと(意図して作っていません)
- 非公式の通信(LINE のサーバーを直接たたくライブラリなど)は使わない。
- LINE のデータベースや暗号化されたキャッシュを解読しない。拡張の Cookie / localStorage も読まない。
- 一斉送信・自動返信はしない。送る前に必ず本人の確認が入る。
- 削除・転送・通報・設定変更の道具は作っていない。

## 安全の作り
- **許可リスト**: 送れるのは、データ置き場の `config.json` の `send_allowlist` に書いたトークだけ。空か無ければ送信は無効(下見が、有効にする方法を表示する)。コードに送り先は入っていない。
- **下見 → 合言葉 → 本番**: 下見(dry-run)は送らず、5 分・1 回だけ使える合言葉を返す。本番は、同じ相手・同じ文面・その合言葉が揃ったときだけ。
- **台帳**(`send_ledger.jsonl`): 送る前に `sending`、あとで `sent` / `failed` / `unknown`。結果が分からない送信が残っている間は、次の送信をすべて断る。自動で送り直さない。
- **使わない操作**: マウスを動かさない・物理のキーを押さない・LINE の窓を前に出さない(窓へのメッセージ投函と UIA / CDP だけ)。押すキーは Enter だけ。ほかのキーの部品は作っていない(テストで確かめる)。
- **非常停止**: データ置き場に `STOP` ファイルがあれば、見張り・送信・開く・スクロールがすべて止まる。

## 準備
1. Windows 11 + Python 3.10 以上。リポジトリで仮想環境を作り、`pip install -r requirements.txt`。
2. データ置き場(既定 `%LOCALAPPDATA%\line-desktop-agent`。環境変数 `LINE_AGENT_HOME` で変更)に `config.json` を作る。

   ```json
   {
     "self_names": ["自分の LINE 表示名"],
     "send_allowlist": ["自分だけのテスト用トークの名前"],
     "chrome_profile": "D:\\path\\to\\chrome-profile"
   }
   ```
   `self_names` は、返信行の送り手が自分かを見分けるのに使う(無ければ空)。`chrome_profile` は Chrome 版を使うときの専用プロフィールの置き場(環境変数 `LINE_AGENT_CHROME_PROFILE` でも指定できる。既定は `%LOCALAPPDATA%\line-desktop-agent\chrome-profile`)。
3. 動作確認: `set PYTHONPATH=src` のあと `.venv\Scripts\python.exe -m pytest -q`、`.venv\Scripts\python.exe -m line_agent list`。
4. MCP サーバー(Claude Code 用): `.mcp.json.example` を `.mcp.json` にコピーして、パスを自分の環境に合わせる。`.claude/settings.json` に、自動で許可する道具・毎回たずねる道具の分け方がある。
5. 見張り(任意): `scripts\start_watcher.ps1`(タスクスケジューラから呼ぶ)、止めるのは `scripts\stop_watcher.ps1`。Chrome 版は `scripts\start_line_chrome.ps1` で専用 Chrome を起動し、LINE 拡張にログインしておく。
6. 詳しい使い方・実測した内容は `docs/` にある。

## 既知の限界
- LINE for PC は Qt 製で、添付(写真・ファイル)の保存ボタンが UIA に出ないため、PC 版では保存できない。Chrome 版ならできる。
- Chrome 版が持つ履歴は、PC 版より短い。
- 拡張のページの作り(DOM)は、LINE 側の更新で変わりうる。変わると読めなくなる。
- 画面ロック中・ログオフ中は動かない(UIA でデスクトップを読むため)。

## 注意(必ず読んでください)
**個人の LINE アカウントを自動操作することは、LINE の利用規約に反する可能性があり、アカウントが制限・停止されるおそれがあります。自己責任で使ってください。** このプロジェクトは LY Corporation(LINE)とは無関係で、公認も受けていません。他人のトークの内容を扱うので、プライバシーにも気をつけてください(トークの内容は `data/`・`*.db` として git に入れない設定にしてあります)。

---

## English summary
A private-use tool that lets Claude Code read (and, only after explicit confirmation, send messages in) your own personal LINE account through the official clients: LINE for PC via Windows UI Automation, and the LINE Chrome extension via the Chrome DevTools Protocol.

It deliberately does **not**: use any unofficial protocol, decrypt LINE databases or caches, send in bulk, or auto-reply.

Safety design: sending is disabled unless `send_allowlist` in `config.json` names the chat; a dry-run returns a one-time token (5 minutes) that the real send must match; an append-only ledger blocks further sends while a result is unresolved; no mouse/physical keys/foreground changes (only Enter, posted to the window); a `STOP` file halts everything.

Setup: create a venv, `pip install -r requirements.txt`, write `config.json` (`self_names`, `send_allowlist`, optional `chrome_profile`) in the data folder (`%LOCALAPPDATA%\line-desktop-agent` or `LINE_AGENT_HOME`), copy `.mcp.json.example` to `.mcp.json`, run `python -m pytest -q`.

Known limits: the Qt-based PC client cannot save attachments (the Chrome version can); the Chrome version shows a shorter history; the extension's DOM may change.

**Warning: automating a personal LINE account may conflict with LINE's Terms of Use and can put the account at risk. Use at your own risk. Not affiliated with or endorsed by LY Corporation.**
