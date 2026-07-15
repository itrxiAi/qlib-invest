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
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n拉取 Techmeme（近 {days} 天）…")
    title, groups = news.run("tech", days)
    total = sum(len(v) for v in groups.values())
    print(f"共 {total} 条")

    by_day: dict[str, list] = {}
    for src, items in groups.items():
        for it in items:
            day = time.strftime("%Y-%m-%d", time.localtime(it["ts"])) if it["ts"] else "unknown"
            by_day.setdefault(day, []).append((src, it))

    for day, entries in sorted(by_day.items()):
        out = RAW_DIR / f"{day}_tech.md"
        lines = [f"# Techmeme 科技头条 — {day}\n", f"共 {len(entries)} 条\n"]
        for src, it in entries:
            when = time.strftime("%H:%M", time.localtime(it["ts"])) if it["ts"] else "--"
            tag = f" `{it['extra']}`" if it.get("extra") else ""
            t = (it["title"] or "(无标题)").replace("|", "\\|")
            link = it.get("link", "")
            lines.append(f"- {when}{tag} [{t}]({link})" if link else f"- {when}{tag} {t}")
            if it.get("summary"):
                lines.append(f"  > {it['summary']}")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  {day}: {len(entries)} 条 → {out.name}")

    print(f"Techmeme 回补完成，{len(by_day)} 天")


def backfill_twitter(count: int = 200):
    """回补 Twitter 关注时间线（受 API 限制，只能抓最近的）。"""
    import twitter_news
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n拉取 Twitter 关注时间线（最近 {count} 条）…")
    items = twitter_news.run(count=count, save=False)
    print(f"共 {len(items)} 条，按日拆分…")

    twitter_news.archive_by_day(items)
    print(f"Twitter 回补完成，{len(set(it['day'] for it in items))} 天")


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
