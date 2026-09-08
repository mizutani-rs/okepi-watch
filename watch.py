#!/usr/bin/env python3
"""おけぴ掲示板の検索結果を定期取得し、新着投稿を Slack に通知する。

監視対象は targets.json に列挙する。1件ごとに state/<id>.json で
既知の投稿IDを持ち、増えた分だけ Slack に流す。

環境変数:
  SLACK_WEBHOOK_URL  Slack Incoming Webhook のURL（必須）
  MAX_NOTIFY         1対象・1回の実行で個別通知する上限（既定 10）
  OKEPI_URL          targets.json が無い場合のみ使う単一URL（旧方式）
"""

import json
import os
import pathlib
import re
import sys
import time
import traceback
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

WEBHOOK = os.environ["SLACK_WEBHOOK_URL"]
MAX_NOTIFY = int(os.environ.get("MAX_NOTIFY", "10"))

TARGETS_PATH = pathlib.Path("targets.json")
STATE_DIR = pathlib.Path("state")
KEEP = 500  # 対象ごとに保持する既知ID数の上限
SLEEP = 2   # 対象間の待ち時間（秒）

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

ID_OK_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def load_targets():
    """[{"id":..., "name":..., "url":...}, ...] を返す。"""
    if TARGETS_PATH.exists():
        targets = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    elif os.environ.get("OKEPI_URL"):
        targets = [{"id": "default", "name": "おけぴ", "url": os.environ["OKEPI_URL"]}]
    else:
        sys.exit("targets.json も OKEPI_URL も見つかりません。")

    for t in targets:
        if not ID_OK_RE.match(t.get("id", "")):
            sys.exit(f"id は半角英数・ハイフン・アンダースコアのみ: {t!r}")
        t.setdefault("name", t["id"])
    ids = [t["id"] for t in targets]
    if len(ids) != len(set(ids)):
        sys.exit("targets.json の id が重複しています。")
    return targets


def state_path(tid):
    return STATE_DIR / f"{tid}.json"


def load_state(tid):
    p = state_path(tid)
    if not p.exists():
        return {"seen": [], "last_count": 0, "warned": False}, True
    return json.loads(p.read_text(encoding="utf-8")), False


def save_state(tid, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path(tid).write_text(
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


def tidy(text):
    """末尾の閲覧数・問合せ数を落とす（更新日時までは残す）。"""
    hits = list(UPDATED_RE.finditer(text))
    if hits:
        return text[: hits[-1].end()].strip()
    return TAIL_RE.sub("", text).strip()


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
            block = a.find_parent(["li", "tr", "article", "section", "div"])
            if block:
                text = " ".join(block.get_text(" ", strip=True).split())
        items[pid] = {
            "title": tidy(text)[:300] or f"投稿 {pid}",
            "url": urljoin(base_url, a["href"]),
        }
    return items


def notify(text):
    res = requests.post(WEBHOOK, json={"text": text}, timeout=20)
    res.raise_for_status()


def process(target):
    tid, name, url = target["id"], target["name"], target["url"]
    state, first_run = load_state(tid)
    seen = set(state["seen"])

    items = extract(fetch(url), url)

    # --- 取得ゼロ = 壊れた可能性。ログにHTMLの頭を出しつつ一度だけ警告する ---
    if not items:
        print(f"[{tid}] !! 投稿を1件も抽出できませんでした", file=sys.stderr)
        if state["last_count"] > 0 and not state["warned"]:
            notify(
                f":warning: おけぴ監視「{name}」: ページから投稿を抽出できませんでした。"
                f"条件に合う投稿が無くなっただけかもしれませんが、"
                f"サイト構造が変わった可能性もあります。\n{url}"
            )
            state["warned"] = True
            save_state(tid, state)
        return

    state["warned"] = False
    new_ids = [pid for pid in items if pid not in seen]

    if first_run:
        print(f"[{tid}] 初回実行: {len(items)}件を既知として登録（通知なし）")
    elif new_ids:
        print(f"[{tid}] 新着 {len(new_ids)}件")
        for pid in new_ids[:MAX_NOTIFY]:
            it = items[pid]
            notify(f":tickets: *{name}* 新着\n{it['title']}\n{it['url']}")
        if len(new_ids) > MAX_NOTIFY:
            notify(f"…「{name}」ほか {len(new_ids) - MAX_NOTIFY} 件の新着\n{url}")
    else:
        print(f"[{tid}] 新着なし（{len(items)}件）")

    merged = list(dict.fromkeys(list(items.keys()) + state["seen"]))[:KEEP]
    state["seen"] = merged
    state["last_count"] = len(items)
    save_state(tid, state)


def main():
    targets = load_targets()
    failed = []

    for i, t in enumerate(targets):
        if i:
            time.sleep(SLEEP)
        try:
            process(t)
        except Exception:
            print(f"[{t['id']}] 失敗:", file=sys.stderr)
            traceback.print_exc()
            failed.append(t["id"])

    if failed:
        sys.exit(f"失敗した対象: {', '.join(failed)}")


if __name__ == "__main__":
    main()
