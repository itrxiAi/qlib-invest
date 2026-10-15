"""cli.py — qlib-invest 确定性命令行（不含 LLM 调用）

由 skills/ 下的各 skill 调用；也可手动执行。在项目根目录运行：

  .venv/bin/python cli.py pull             # 拉 techmeme + twitter + market → runs/news_raw/
  .venv/bin/python cli.py filter           # news_raw → news_filtered（敏感词过滤）
  .venv/bin/python cli.py archive          # 拼 runs/tmp/analyze_*.md + 最新 triage → runs/news_deep/，并清理 briefing 过期行
  .venv/bin/python cli.py push             # 最新 news_deep 中 ≥3★ 条目推 Telegram
  .venv/bin/python cli.py clean-briefing   # 单独清理 briefing（追踪中事件超 BRIEFING_STALE_DAYS 天未更新的行删除）

环境：读取 .env（TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / QWEN_PROXY / DEEP_MIN_SCORE）
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# 数据根目录：INVEST_DATA_DIR 必填（数据与代码分离，插件升级不丢数据）。
# 用法：./invest --data <数据目录> <cmd>
_root = os.environ.get("INVEST_DATA_DIR")
if not _root:
    sys.exit("缺少 INVEST_DATA_DIR 环境变量。用法：./invest --data <数据目录> <cmd>")
ROOT = Path(_root).expanduser().resolve()
os.environ["INVEST_DATA_DIR"] = str(ROOT)
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("cli")

TMP_DIR = ROOT / "runs" / "tmp"
DEEP_DIR = ROOT / "runs" / "news_deep"
BRIEFING = ROOT / "runs" / "briefing.md"

CST = timezone(timedelta(hours=8))
MIN_SCORE = int(os.environ.get("DEEP_MIN_SCORE", "3"))
STALE_DAYS = int(os.environ.get("BRIEFING_STALE_DAYS", "3"))


def cmd_pull():
    import scheduler
    scheduler.pull_all()


def cmd_filter():
    import scheduler
    scheduler._filter_raw_for_llm()


def cmd_push():
    import scheduler
    scheduler._push_deep_analysis_tg()


def _parse_row_date(s):
    """解析表格行末列的时间：'2026-08-31 19:00' 或 '2026-08-18'。失败返回 None。"""
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def clean_briefing(path=BRIEFING, days=STALE_DAYS):
    """briefing 瘦身：
    1. 删除 >7 天的 "## 本轮/上轮新增（...）" section（旧格式遗留）
    2. 追踪中事件表里 "上次更新" >7 天的行删除
    3. 删除 "## 已归档" 整个 section（新格式契约已不含它）
    解析不出时间的行保守保留。
    """
    if not path.exists():
        log.info("briefing.md 不存在，跳过")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    cutoff = datetime.now(CST).replace(tzinfo=None) - timedelta(days=days)

    out = []
    removed_sections = 0
    removed_rows = 0
    i = 0
    in_tracking_table = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("## "):
            in_tracking_table = stripped.startswith("## 追踪中事件")
            # "## 已归档" 不再属于格式契约，整段移除
            if stripped.startswith("## 已归档"):
                i += 1
                while i < len(lines) and not lines[i].strip().startswith("## "):
                    i += 1
                removed_sections += 1
                continue
            # 旧格式的 ## 本轮/上轮新增（YYYY-MM-DD HH:MM）：过期整段跳过
            m = re.match(r"## (?:本轮|上轮)新增（(\d{4}-\d{2}-\d{2})[ \u00a0]?(\d{2}:\d{2})?）", stripped)
            if m:
                dt = _parse_row_date(f"{m.group(1)} {m.group(2) or '00:00'}")
                if dt and dt < cutoff:
                    i += 1
                    while i < len(lines) and not lines[i].strip().startswith("## "):
                        i += 1
                    removed_sections += 1
                    continue
            out.append(line)
            i += 1
            continue

        # 追踪中事件表的数据行：| 事件 | 状态 | 上次更新 |
        if in_tracking_table and stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) >= 3 and not re.match(r"^[-: ]+$", cells[0]) and cells[0] != "事件":
                dt = _parse_row_date(cells[-1])
                if dt and dt < cutoff:
                    removed_rows += 1
                    i += 1
                    continue

        out.append(line)
        i += 1

    # 去掉连续 3+ 空行
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    log.info(f"briefing 清理完成: 删过期本轮新增 {removed_sections} 段, 删过期追踪行 {removed_rows} 行")


def cmd_clean_briefing():
    clean_briefing()


def cmd_archive():
    """拼 tmp 产物 → news_deep 存档。契约与 invest-run skill 一致。"""
    triages = sorted(TMP_DIR.glob("triage_*.json"), reverse=True)
    if not triages:
        log.error("runs/tmp/ 下没有 triage_*.json")
        sys.exit(1)
    triage_path = triages[0]
    triage = json.loads(triage_path.read_text(encoding="utf-8"))
    log.info(f"使用 triage: {triage_path.name}")

    date = triage.get("date", datetime.now(CST).strftime("%Y-%m-%d"))
    hour = triage.get("hour", datetime.now(CST).strftime("%H"))

    # 按 triage 顺序收 analyze_{id}.md；星级由 score 生成
    results = []
    for item in triage.get("to_analyze", []):
        score = item.get("score", 0)
        if score < MIN_SCORE:
            continue
        f = TMP_DIR / f"analyze_{item['id']}.md"
        title = item.get("title", "")
        if f.exists():
            text = re.sub(r"^[★☆\s]+", "", f.read_text(encoding="utf-8").strip())
            results.append(f"{'★' * min(score, 5)} {text}")
        else:
            results.append(f"{'★' * min(score, 5)}☆ {title}（分析失败或无产出）")

    body = "\n\n".join(results) if results else "（无）"
    backup = "\n".join(f"- {b['title']} — {b['reason']}" for b in triage.get("backup", []))
    discarded = "\n".join(f"- {d['title']} — {d['reason']}" for d in triage.get("discarded", []))

    content = f"""# 投研深度分析 — {date} {hour}:00
_分析时段：{date} {hour}:00_

## 本轮综合判断

（见逐条分析）

## 逐条分析

{body}

## 备查清单（1-2 分，不深度分析）

{backup}

## 丢弃清单（标题 + 原因）

{discarded}
"""
    DEEP_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DEEP_DIR / f"{date}_{hour}.md"
    out_path.write_text(content, encoding="utf-8")
    log.info(f"存档: {out_path}（{len(results)} 条）")

    # 清理 tmp 与本 briefing 过期内容
    for f in TMP_DIR.glob("analyze_*.md"):
        f.unlink()
    triage_path.unlink()
    clean_briefing()
    print(str(out_path))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    cmds = {
        "pull": cmd_pull,
        "filter": cmd_filter,
        "archive": cmd_archive,
        "push": cmd_push,
        "clean-briefing": cmd_clean_briefing,
    }
    if cmd not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[cmd]()


if __name__ == "__main__":
    main()
