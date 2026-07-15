"""
twitter_news.py — 抓取 Twitter/X 关注时间线（home timeline）

用 twikit + 浏览器 cookie 抓取你关注账号的最新推文。
归档到 runs/news_raw/YYYY-MM-DD_twitter.md 和 twitter_scan.md。

首次使用：
1. pip install twikit python-dotenv
2. 浏览器登录 x.com（小号）
3. F12 → Application → Cookies → https://x.com
   复制 auth_token 和 ct0 两个值
4. 填入 .env 文件：
   TWITTER_AUTH_TOKEN=xxx
   TWITTER_CT0=xxx
5. python twitter_news.py          # 抓取并打印
   python twitter_news.py --save   # 抓取并存档

cookie 过期后重新从浏览器复制即可（一般几周到几个月）。
"""

import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

COOKIES_FILE = ROOT / "cookies.json"
SCAN_FILE = ROOT / "twitter_scan.md"
RAW_DIR = ROOT / "runs" / "news_raw"

TWITTER_AUTH_TOKEN = os.environ.get("TWITTER_AUTH_TOKEN", "")
TWITTER_CT0 = os.environ.get("TWITTER_CT0", "")
CST = timezone(timedelta(hours=8))


def _to_cst(ts_str: str) -> datetime:
    """twikit created_at 格式: 'Thu Jul 10 14:30:00 +0000 2026'"""
    try:
        dt = datetime.strptime(ts_str, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(CST)
    except Exception:
        return datetime.now(CST)


def _login(client):
    """用浏览器 cookie 加载会话。"""
    if COOKIES_FILE.exists():
        client.load_cookies(str(COOKIES_FILE))
        return
    if not TWITTER_AUTH_TOKEN or not TWITTER_CT0:
        raise RuntimeError(
            "未找到 cookies.json 且未设置 TWITTER_AUTH_TOKEN/TWITTER_CT0。\n"
            "请从浏览器 F12 → Cookies → x.com 复制 auth_token 和 ct0 值到 .env。"
        )
    client.set_cookies({
        "auth_token": TWITTER_AUTH_TOKEN,
        "ct0": TWITTER_CT0,
    })
    client.save_cookies(str(COOKIES_FILE))


def fetch_timeline(count: int = 100) -> list[dict]:
    """抓取 home timeline，返回推文列表。cookie 过期自动重新登录。"""
    from twikit import Client
    from twikit.errors import Unauthorized

    client = Client("en-US")
    _login(client)

    try:
        tweets = client.get_latest_timeline(count=count, cursor=None)
    except Unauthorized:
        # cookie 过期，删除后重新登录
        COOKIES_FILE.unlink(missing_ok=True)
        _login(client)
        tweets = client.get_latest_timeline(count=count, cursor=None)
    except IndexError:
        # twikit 偶尔因 Twitter API 返回空 item 报 IndexError，重试一次
        time.sleep(3)
        tweets = client.get_latest_timeline(count=count, cursor=None)

    items = []
    for tw in tweets:
        dt = _to_cst(tw.created_at)
        user = tw.user
        if isinstance(user, dict):
            screen_name = user.get('legacy', {}).get('screen_name', 'unknown')
            name = user.get('legacy', {}).get('name', '')
        elif user:
            screen_name = user.screen_name
            name = user.name
        else:
            screen_name = 'unknown'
            name = ''
        items.append({
            "ts": int(dt.timestamp()),
            "time": dt.strftime("%Y-%m-%d %H:%M"),
            "day": dt.strftime("%Y-%m-%d"),
            "user": screen_name,
            "name": name,
            "text": (tw.text or "").replace("|", "\\|"),
            "likes": tw.favorite_count,
            "retweets": tw.retweet_count,
            "replies": tw.reply_count,
            "url": f"https://x.com/{screen_name}/status/{tw.id}",
        })
    return items


def to_markdown(items: list[dict], title: str = "Twitter 关注时间线") -> str:
    lines = [f"# {title}\n", f"共 {len(items)} 条\n"]
    for it in items:
        when = it["time"][11:]  # HH:MM
        lines.append(
            f"- {when} [@{it['user']}] {it['text']}"
            f"  ❤{it['likes']} 🔁{it['retweets']} 💬{it['replies']}"
        )
        if it["url"]:
            lines.append(f"  {it['url']}")
    return "\n".join(lines) + "\n"


def archive_by_day(items: list[dict]):
    """按日归档到 runs/news_raw/YYYY-MM-DD_twitter.md"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list] = {}
    for it in items:
        by_day.setdefault(it["day"], []).append(it)

    for day, entries in sorted(by_day.items()):
        out = RAW_DIR / f"{day}_twitter.md"
        lines = [f"# Twitter 关注时间线 — {day}\n", f"共 {len(entries)} 条\n"]
        for it in entries:
            when = it["time"][11:]
            lines.append(
                f"- {when} [@{it['user']}] {it['text']}"
                f"  ❤{it['likes']} 🔁{it['retweets']} 💬{it['replies']}"
            )
            if it["url"]:
                lines.append(f"  {it['url']}")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {day}: {len(entries)} 条 → {out.name}")


def run(count: int = 100, save: bool = False):
    """入口：抓取 + 打印 + 可选存档。"""
    items = fetch_timeline(count=count)
    print(f"\n######## Twitter 关注时间线（{len(items)} 条）########\n")
    print(to_markdown(items))

    if save:
        SCAN_FILE.write_text(to_markdown(items), encoding="utf-8")
        print(f"→ 已存 Markdown: {SCAN_FILE}")
        archive_by_day(items)
        print(f"→ 已按日归档到 {RAW_DIR}/")

    return items


if __name__ == "__main__":
    import sys
    save = "--save" in sys.argv or "-s" in sys.argv
    count = 100
    for arg in sys.argv[1:]:
        if arg.isdigit():
            count = int(arg)
    run(count=count, save=save)
