"""
backfill_news.py — 回补历史快讯，按日拆分存档

用法：
  python backfill_news.py 30 tech      # 回补近 30 天 Techmeme
  python backfill_news.py 30 twitter   # 回补 Twitter
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "runs" / "news_raw"


def backfill_techmeme(days: int):
    import news
    import scheduler
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n拉取 Techmeme（近 {days} 天）…")
    title, groups = news.run("tech", days)
    flat = []
    for src, items in groups.items():
        for it in items:
            it.setdefault("extra", src)
            flat.append(it)
    print(f"共 {len(flat)} 条")
    scheduler.merge_daily("techmeme", flat)

    # Also update news_scan.md
    out_path = ROOT / "news_scan.md"
    out_path.write_text(news._md(groups, title), encoding="utf-8")
    print(f"news_scan.md updated")
    print(f"Techmeme 回补完成")


def backfill_twitter(count: int = 200):
    """回补 Twitter 关注时间线（受 API 限制，只能抓最近的）。"""
    import twitter_news
    import scheduler
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n拉取 Twitter 关注时间线（最近 {count} 条）…")
    items = twitter_news.fetch_timeline(count=count)
    print(f"共 {len(items)} 条")
    scheduler.merge_daily("twitter", items)

    # Also update twitter_scan.md
    twitter_news.SCAN_FILE.write_text(twitter_news.to_markdown(items), encoding="utf-8")
    print(f"Twitter 回补完成")


if __name__ == "__main__":
    args = sys.argv[1:]
    days = int(args[0]) if args and args[0].isdigit() else 30
    source = args[1] if len(args) > 1 else ""

    if source == "tech":
        backfill_techmeme(days)
    elif source == "twitter":
        backfill_twitter()
    elif source == "all":
        backfill_techmeme(days)
        backfill_twitter()
    else:
        backfill_techmeme(days)
