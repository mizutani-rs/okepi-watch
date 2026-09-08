
#!/usr/bin/env python3
"""おけぴ掲示板の検索結果を定期取得し、新着投稿を Slack に通知する。

環境変数:
  OKEPI_URL          監視対象の検索結果URL（必須）
  SLACK_WEBHOOK_URL  Slack Incoming Webhook のURL（必須）
  MAX_NOTIFY         1回の実行で個別通知する上限（既定 10）
"""

import json
import os
import pathlib
import re
import sys
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = os.environ["OKEPI_URL"]
WEBHOOK = os.environ["SLACK_WEBHOOK_URL"]
MAX_NOTIFY = int(os.environ.get("MAX_NOTIFY", "10"))

STATE_PATH = pathlib.Path("state/seen.json")
KEEP = 500  # 保持する既知ID数の上限

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 投稿詳細ページのURL形式: https://okepi.net/bbs/posting/detail/5291657
ID_RE = re.compile(r"/bbs/posting/detail/(\d+)")

# 一覧のリンク文字列は末尾に「9/7 16:13 新着121 hit ( 1 )」のように
# 更新日時・閲覧数・問合せ数が続く。更新日時までで切る。
UPDATED_RE = re.compile(r"\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}")
TAIL_RE = re.compile(r"\s*(?:新着)?\d*\s*hit\s*\(\s*\d+\s*\)\s*$")


def tidy(text):
    """末尾の閲覧数・問合せ数を落とす（更新日時までは残す）。"""
    hits = list(UPDATED_RE.finditer(text))
    if hits:
        return text[: hits[-1].end()].strip()
    return TAIL_RE.sub("", text).strip()


def load_state():
    if not STATE_PATH.exists():
        return {"seen": [], "last_count": 0, "warned": False}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def fetch(url):
    res = requests.get(
        url,
        headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"},
        timeout=30,
    )
    res.raise_for_status()
    res.encoding = res.apparent_encoding or "utf-8"
    return res.text


def extract(page_html, base_url):
    """{投稿ID: {"title": ..., "url": ...}} を返す。"""
    soup = BeautifulSoup(page_html, "html.parser")
    items = {}
    for a in soup.select("a[href]"):
        m = ID_RE.search(a["href"])
        if not m:
            continue
        pid = m.group(1)
        if pid in items:
            continue

        text = " ".join(a.get_text(" ", strip=True).split())
        if len(text) < 6:
            # リンク文字が「詳細」等だけの場合は、囲みブロックの文言を使う
            block = a.find_parent(["li", "tr", "article", "section", "div"])
            if block:
                text = " ".join(block.get_text(" ", strip=True).split())
        text = tidy(text)
        items[pid] = {
            "title": text[:300] or f"投稿 {pid}",
            "url": urljoin(base_url, a["href"]),
        }
    return items


def notify(text):
    res = requests.post(WEBHOOK, json={"text": text}, timeout=20)
    res.raise_for_status()


def main():
    state = load_state()
    seen = set(state["seen"])
    first_run = not STATE_PATH.exists()

    page = fetch(URL)
    items = extract(page, URL)

    # --- 取得ゼロ = 壊れた可能性。ログにHTMLの頭を出しつつ一度だけ警告する ---
    if not items:
        print("!! 投稿を1件も抽出できませんでした。HTMLの先頭3000文字:", file=sys.stderr)
        print(page[:3000], file=sys.stderr)
        if state["last_count"] > 0 and not state["warned"]:
            notify(
                ":warning: おけぴ監視: ページから投稿を抽出できませんでした。"
                "サイト構造が変わった可能性があります（Actions のログを確認してください）。"
            )
            state["warned"] = True
            save_state(state)
        return

    state["warned"] = False
    new_ids = [pid for pid in items if pid not in seen]

    if first_run:
        print(f"初回実行: {len(items)}件を既知として登録し、通知はしません。")
    elif new_ids:
        print(f"新着 {len(new_ids)}件")
        for pid in new_ids[:MAX_NOTIFY]:
            it = items[pid]
            notify(f":tickets: *おけぴ新着*\n{it['title']}\n{it['url']}")
        if len(new_ids) > MAX_NOTIFY:
            notify(
                f"…ほか {len(new_ids) - MAX_NOTIFY} 件の新着があります\n{URL}"
            )
    else:
        print("新着なし")

    # 既知IDを更新（新しいものを前に寄せて上限で切る）
    merged = list(dict.fromkeys(list(items.keys()) + state["seen"]))[:KEEP]
    state["seen"] = merged
    state["last_count"] = len(items)
    save_state(state)


if __name__ == "__main__":
    main()
