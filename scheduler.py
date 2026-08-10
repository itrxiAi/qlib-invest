"""
scheduler.py — 常驻定时任务服务

每小时整点拉 Techmeme + Twitter，增量归档。
每12小时调 qwen-code CLI 跑 news-sync skill 深度分析。
部署：nohup .venv/bin/python scheduler.py >> runs/scheduler.log 2>&1 &
"""

import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("scheduler")

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "runs" / "news_raw"
CST = timezone(timedelta(hours=8))

def _day_from_ts(ts):
    """从 timestamp 获取 YYYY-MM-DD（CST）。"""
    return datetime.fromtimestamp(ts, tz=CST).strftime("%Y-%m-%d")


def pull_techmeme():
    log.info("开始拉取 Techmeme 科技头条")
    import news
    title, groups = news.run("tech", 1)
    out_path = ROOT / "news_scan.md"
    out_path.write_text(news._md(groups, title), encoding="utf-8")
    total = sum(len(v) for v in groups.values())
    log.info(f"Techmeme 完成，共 {total} 条 → {out_path}")
    # 展平 groups 为列表
    flat = []
    for src, items in groups.items():
        for it in items:
            it.setdefault("extra", src)
            flat.append(it)
    merge_daily("techmeme", flat)


def pull_twitter():
    log.info("开始拉取 Twitter 关注时间线")
    import twitter_news
    items = twitter_news.fetch_timeline(count=100)
    twitter_news.SCAN_FILE.write_text(twitter_news.to_markdown(items), encoding="utf-8")
    log.info(f"Twitter 完成，共 {len(items)} 条 → {twitter_news.SCAN_FILE}")
    merge_daily("twitter", items)


def _parse_existing(path, source):
    """解析已有归档文件，返回 [(hour, when_str, line_str), ...] 和 seen 集合。"""
    items = []
    seen = set()
    if not path.exists():
        return items, seen
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s.startswith("- "):
            i += 1
            continue

        if source == "techmeme":
            # 兼容 MM-DD HH:MM 和 HH:MM 两种格式
            m = re.match(r'- (?:(\d{2}-\d{2}) )?(\d{2}:\d{2})\s+`([^`]+)`\s+\[([^\]]+)\]\(([^)]+)\)', s)
            if m:
                md = m.group(1) or ""
                hm = m.group(2)
                hour = hm[:2]
                when = f"{md} {hm}" if md else hm
                link = m.group(5)
                seen.add(link)
                full_line = s
                # 检查是否有 summary 行
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("> "):
                    full_line += "\n" + lines[i + 1]
                    i += 1
                items.append((hour, when, full_line))
        elif source == "twitter":
            m = re.match(r'- (\d{2}:\d{2}) \[@([^\]]+)\] (.+?)\s+❤(\d+) 🔁(\d+) 💬(\d+)', s)
            if m:
                when = m.group(1)
                hour = when[:2]
                url = ""
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("http"):
                    url = lines[i + 1].strip()
                    i += 1
                seen.add(url or m.group(3)[:80])
                full_line = s
                if url:
                    full_line += "\n  " + url
                items.append((hour, when, full_line))
        i += 1
    return items, seen


def _format_item(source, it):
    """将新条目格式化为 (hour, when_str, line_str)。when_str 统一用 HH:MM 做排序 key。"""
    if source == "techmeme":
        dt = datetime.fromtimestamp(it["ts"], tz=CST) if it.get("ts") else datetime.now(CST)
        when = dt.strftime("%H:%M")
        hour = when[:2]
        md = dt.strftime("%m-%d")
        tag = f" `{it['extra']}`" if it.get("extra") else ""
        title_txt = (it.get("title") or "(无标题)").replace("|", "\\|")
        link = it.get("link", "")
        line = f"- {md} {when}{tag} [{title_txt}]({link})" if link else f"- {md} {when}{tag} {title_txt}"
        if it.get("summary"):
            line += f"\n  > {it['summary']}"
        return hour, when, line
    elif source == "twitter":
        dt = datetime.fromtimestamp(it["ts"], tz=CST)
        when = dt.strftime("%H:%M")
        hour = when[:2]
        txt = it["text"].replace("|", "\\|")
        line = f"- {when} [@{it['user']}] {txt}  ❤{it['likes']} 🔁{it['retweets']} 💬{it['replies']}"
        if it.get("url"):
            line += f"\n  {it['url']}"
        return hour, when, line
    return "00", "", ""


def _item_key(source, it):
    """生成去重 key。"""
    if source == "techmeme":
        return it.get("link", "")
    elif source == "twitter":
        return it.get("url") or it.get("text", "")[:80]
    return ""


_TITLES = {
    "techmeme": "Techmeme 科技头条",
    "twitter": "Twitter 关注时间线",
}


def merge_daily(source, new_items):
    """通用增量归档：按日文件、按小时分组、去重、重写。

    source: "techmeme" | "twitter"
    new_items: 原始条目列表（dict）
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # 按日分组
    by_day = {}
    for it in new_items:
        day = _day_from_ts(it["ts"]) if it.get("ts") else datetime.now(CST).strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(it)

    for day, entries in sorted(by_day.items()):
        path = RAW_DIR / f"{day}_{source}.md"

        # 解析已有条目
        existing_items, seen = _parse_existing(path, source)

        # 添加新条目
        new_count = 0
        for it in entries:
            key = _item_key(source, it)
            if key in seen:
                continue
            seen.add(key)
            hour, when, line = _format_item(source, it)
            existing_items.append((hour, when, line))
            new_count += 1

        if new_count == 0 and path.exists():
            log.info(f"  {source} 归档 {day}: 无新增")
            continue

        # 按小时分组重写
        existing_items.sort(key=lambda x: x[1])
        by_hour = {}
        for hour, when, line in existing_items:
            by_hour.setdefault(hour, []).append(line)

        title = _TITLES.get(source, source)
        lines = [f"# {title} — {day}\n", f"共 {len(existing_items)} 条\n"]
        for hour in sorted(by_hour.keys()):
            lines.append(f"\n## {hour}:00\n")
            for line in by_hour[hour]:
                lines.append(line)

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info(f"  {source} 归档 {day}: +{new_count} 条（共 {len(existing_items)} 条）→ {path.name}")


def pull_all():
    """Techmeme + Twitter 一起拉，增量归档。"""
    try:
        pull_techmeme()
    except Exception as e:
        log.exception(f"Techmeme 拉取失败: {e}")
        _send_tg_alert(f"Techmeme 拉取失败: {e}")
    for attempt in range(3):
        try:
            pull_twitter()
            break
        except Exception as e:
            if attempt < 2:
                log.warning(f"Twitter 第{attempt+1}次拉取失败: {e}，重试中...")
                time.sleep(30)
            else:
                log.exception(f"Twitter 拉取失败: {e}")
                _send_tg_alert(f"Twitter 拉取失败 (3次重试后): {e}")



def _send_tg_alert(msg):
    """发送 TG 告警通知。"""
    import os
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"⚠️ invest scheduler 异常\n\n{msg}"},
            timeout=10,
        )
    except Exception:
        pass


def _send_tg_message(text):
    """发送 TG 消息。"""
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("TG 配置缺失，跳过推送")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        if resp.status_code != 200:
            log.error(f"TG 推送失败: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.exception(f"TG 推送异常: {e}")


def _push_deep_analysis_tg():
    """推送最新的 news_deep 存档到 TG（仅≥4分条目）。"""
    deep_dir = ROOT / "runs" / "news_deep"
    if not deep_dir.exists():
        return
    files = sorted(deep_dir.glob("*.md"), reverse=True)
    if not files:
        return
    text = files[0].read_text(encoding="utf-8")
    # 提取≥4分的条目（★4或★5）
    import re
    blocks = re.findall(r'(★{4,5}.*?)(?=\n★|\n## |\Z)', text, re.DOTALL)
    if not blocks:
        log.info("无≥4分条目，跳过 TG 推送")
        return
    msg = f"📊 深度分析 — {files[0].stem}\n\n" + "\n\n".join(blocks)
    # TG 消息上限 4096 字符，分段发送
    for i in range(0, len(msg), 4000):
        _send_tg_message(msg[i:i+4000])
    log.info(f"已推送 {len(blocks)} 条≥4分分析到 TG")


def run_news_sync():
    """每12小时调 qwen-code CLI 跑 news-sync skill，完成后推送 TG。"""
    cli = os.environ.get("QWEN_CLI", "node")
    cli_args = os.environ.get("QWEN_CLI_ARGS", "").split()
    model = os.environ.get("QWEN_MODEL", "deepseek-v4-flash")
    cmd = [cli] + cli_args + ["--prompt", "/news-sync", "--output-format", "stream-json", "-y", "--model", model]
    log.info(f"启动 news-sync skill: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, cwd=str(ROOT), timeout=600, check=True)
        log.info("news-sync skill 完成")
        _push_deep_analysis_tg()
    except Exception as e:
        log.exception(f"news-sync skill 失败: {e}")
        _send_tg_alert(f"news-sync skill 失败: {e}")


def main():
    sched = BackgroundScheduler(timezone="Asia/Shanghai")

    # 每小时拉新闻
    sched.add_job(
        pull_all,
        CronTrigger(minute=0),
        id="news_hourly",
        misfire_grace_time=600,
    )

    # 每12小时深度分析（08:00, 20:00 CST）
    sched.add_job(
        run_news_sync,
        CronTrigger(hour="8,20", minute=0),
        id="news_sync_12h",
        misfire_grace_time=600,
    )

    sched.start()
    log.info("调度器已启动：每小时拉新闻，每12h(08/20)跑 news-sync skill。Ctrl-C 退出。")

    try:
        import time
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()
        log.info("已停止")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("pull", "sync", "run"):
        task = sys.argv[1]
        if task == "pull":
            pull_all()
        elif task == "sync":
            run_news_sync()
        elif task == "run":
            pull_all()
            run_news_sync()
    else:
        main()
