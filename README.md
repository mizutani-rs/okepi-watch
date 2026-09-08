# okepi-watch
# okepi-watch

おけぴ掲示板の検索結果ページを10分おきに見に行き、新着投稿を Slack に流す。

## セットアップ

1. **リポジトリを作る**（パブリックにすると Actions の実行時間が無料）
   `watch.py` と `.github/workflows/okepi-watch.yml` を置いて push。

2. **Slack の Incoming Webhook を用意**
   Slack の App 設定 → Incoming Webhooks を有効化 → 通知したいチャンネルを選んで
   `https://hooks.slack.com/services/...` を発行。

3. **リポジトリに値を登録**
   - Settings → Secrets and variables → Actions → **Secrets** タブ
     `SLACK_WEBHOOK_URL` = 発行した Webhook URL
   - 同じ画面の **Variables** タブ
     `OKEPI_URL` = 監視したい検索結果URL（長いままで可。ダブルクォート不要）

4. **手動で1回流す**
   Actions タブ → okepi watch → Run workflow。
   初回は現在の投稿を「既知」として記録するだけで、通知は飛びません。
   2回目以降の実行から新着だけが届きます。

## 動作確認

- 手動実行後、`state/seen.json` がコミットされていれば取得は成功している。
- ログに「投稿を1件も抽出できませんでした」と出る場合は、`watch.py` の `ID_RE`
  （詳細ページURLから投稿IDを拾う正規表現）がサイト構造と合っていない。
  ログに出力される HTML の先頭を見て直す。

## 確認済みの仕様

- 検索結果ページはサーバー側でHTMLを生成しているため、ログインもJS実行も不要。
- 投稿詳細ページのURLは `https://okepi.net/bbs/posting/detail/5291657` の形式。
  `watch.py` はこのパターンで投稿IDを拾っている。

## 調整ポイント

- 間隔: workflow の `cron`。`*/5` で5分おき。ただし GitHub の schedule は
  混雑時に数分遅れることがあるので「即時」ではなく「数分以内」。
- 1回の通知上限: 環境変数 `MAX_NOTIFY`（既定10）。超えた分はまとめて1通。

## 注意

- 60日間リポジトリに何も活動がないと、GitHub は schedule を自動停止する。
  この workflow は state をコミットするので通常は止まらないが、通知が来ない期間が
  続いたら Actions タブを確認する。
- アクセス間隔を詰めすぎない。10分おき程度が無難。
