"""
run_deep_analysis.py — 编排脚本

流程：
1. 调 qwen-code CLI 跑 /news-sync skill（粗筛 → 输出 triage JSON）
2. 读 triage JSON，逐条调 qwen-code CLI 跑 /news-analyze skill
3. 拼接所有分析结果 → 写入 runs/news_deep/{date}_{hour}.md
4. 更新 briefing.md（调 qwen-code CLI 跑 briefing 更新）
5. 推送 TG

用法：
  .venv/bin/python run_deep_analysis.py          # 完整流程
  .venv/bin/python run_deep_analysis.py --triage runs/tmp/triage_2026-08-31_18.json  # 跳过 step 1
"""

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
log = logging.getLogger("deep_analysis")

ROOT = Path(__file__).parent
TMP_DIR = ROOT / "runs" / "tmp"
DEEP_DIR = ROOT / "runs" / "news_deep"
BRIEFING = ROOT / "runs" / "briefing.md"
CAUSAL_RULES = ROOT / "runs" / "causal_rules.md"

CLI = os.environ.get("QWEN_CLI", "node")
CLI_ARGS = os.environ.get("QWEN_CLI_ARGS", "").split()
MODEL = os.environ.get("QWEN_MODEL", "deepseek-chat")
PROXY = os.environ.get("QWEN_PROXY", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

CST = timezone(timedelta(hours=8))

MIN_SCORE = int(os.environ.get("DEEP_MIN_SCORE", "3"))  # 只分析≥此分数的条目
MAX_ITEMS = int(os.environ.get("DEEP_MAX_ITEMS", "15"))  # 最多分析条目数


def _cli_cmd(prompt, extra=None):
    cmd = [CLI] + CLI_ARGS + ["--prompt", prompt, "--output-format", "stream-json", "-y", "--model", MODEL]
    if PROXY:
        cmd += ["--proxy", PROXY]
    if extra:
        cmd += extra
    return cmd


def _run_cli(cmd, timeout=300):
    """跑 CLI，实时打印 stdout，返回完整输出。"""
    log.info(f"CLI: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout_lines = []
    try:
        for line in proc.stdout:
            stdout_lines.append(line)
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "assistant":
                        msg = obj.get("message", {})
                        for c in msg.get("content", []):
                            if c.get("type") == "text" and c.get("text"):
                                print(c["text"][:200])
                            elif c.get("type") == "tool_use":
                                print(f"  [tool] {c.get('name')}: {str(c.get('input', ''))[:100]}")
                    elif obj.get("type") == "result":
                        print(f"  [result] {obj.get('result', '')[:200]}")
                except json.JSONDecodeError:
                    pass
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        log.error(f"CLI 超时 ({timeout}s)")
        return None
    if proc.returncode != 0:
        stderr = proc.stderr.read() if proc.stderr else ""
        log.error(f"CLI 失败 returncode={proc.returncode}: {stderr[:500]}")
        return None
    return "".join(stdout_lines)


def step1_triage():
    """跑 news-sync skill 生成 triage JSON。"""
    log.info("=== Step 1: 粗筛 (news-sync) ===")
    output = _run_cli(_cli_cmd("/news-sync"), timeout=600)
    if not output:
        log.error("news-sync skill 失败")
        return None
    # 找 triage JSON 文件
    now = datetime.now(CST)
    triage_path = TMP_DIR / f"triage_{now.strftime('%Y-%m-%d')}_{now.strftime('%H')}.json"
    if not triage_path.exists():
        # 找最新的
        files = sorted(TMP_DIR.glob("triage_*.json"), reverse=True)
        if not files:
            log.error("未找到 triage JSON")
            return None
        triage_path = files[0]
    log.info(f"triage: {triage_path}")
    return triage_path


def step2_analyze(triage_path):
    """逐条调 news-analyze skill。"""
    log.info(f"=== Step 2: 逐条深度分析 ===")
    triage = json.loads(triage_path.read_text(encoding="utf-8"))
    all_items = triage.get("to_analyze", [])

    # 按分数降序排列，过滤最低分和数量上限
    all_items.sort(key=lambda x: x.get("score", 0), reverse=True)
    items = [it for it in all_items if it.get("score", 0) >= MIN_SCORE][:MAX_ITEMS]
    skipped = [it for it in all_items if it.get("score", 0) < MIN_SCORE]
    if skipped:
        log.info(f"跳过 {len(skipped)} 条 <{MIN_SCORE} 分条目")
    if len(all_items) > MAX_ITEMS:
        log.info(f"截断：{len(all_items)} → {len(items)} 条（上限 {MAX_ITEMS}）")
    if not items:
        log.info("无待分析条目")
        return []

    results = []
    for item in items:
        item_id = item["id"]
        title = item.get("title", "")
        score = item.get("score", 3)
        log.info(f"  [{item_id}/{len(items)}] {title} (score={score})")

        prompt = f"/news-analyze\n\n输入事件：\n```json\n{json.dumps(item, ensure_ascii=False)}\n```"

        output = _run_cli(_cli_cmd(prompt), timeout=180)
        if not output:
            results.append(f"★★{'★'*(score-2)}☆ {title}（分析失败）\n")
            continue

        # 从输出中提取分析文本，或从 analyze_{id}.md 读取
        analyze_file = TMP_DIR / f"analyze_{item_id}.md"
        if analyze_file.exists():
            text = analyze_file.read_text(encoding="utf-8").strip()
            results.append(text)
        else:
            # 从 stream-json 输出提取最后的 result
            for line in reversed(output.strip().splitlines()):
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "result":
                        results.append(obj.get("result", ""))
                        break
                except json.JSONDecodeError:
                    continue

    return results


def step3_archive(triage_path, results):
    """拼接结果写入 news_deep。"""
    log.info("=== Step 3: 存档 ===")
    triage = json.loads(triage_path.read_text(encoding="utf-8"))
    date = triage.get("date", datetime.now(CST).strftime("%Y-%m-%d"))
    hour = triage.get("hour", datetime.now(CST).strftime("%H"))

    DEEP_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = DEEP_DIR / f"{date}_{hour}.md"

    # 综合判断
    summary = "## 本轮综合判断\n\n"
    if results:
        # 从结果中提取关键信号
        summary += "（见逐条分析）\n"
    else:
        summary += "本轮无≥3分条目。\n"

    # 逐条分析
    body = "\n\n".join(results) if results else "（无）"

    # 备查 + 丢弃
    backup = triage.get("backup", [])
    discarded = triage.get("discarded", [])
    backup_text = "\n".join(f"- {b['title']} — {b['reason']}" for b in backup)
    discard_text = "\n".join(f"- {d['title']} — {d['reason']}" for d in discarded)

    content = f"""# 投研深度分析 — {date} {hour}:00
_分析时段：{date} {hour}:00_

{summary}

## 逐条分析

{body}

## 备查清单（1-2 分，不深度分析）

{backup_text}

## 丢弃清单（标题 + 原因）

{discard_text}
"""
    archive_path.write_text(content, encoding="utf-8")
    log.info(f"存档: {archive_path}")
    return archive_path


def step4_update_briefing(archive_path):
    """调 qwen-code CLI 更新 briefing.md。"""
    log.info("=== Step 4: 更新 briefing ===")
    prompt = f"""请更新 runs/briefing.md，基于本轮深度分析存档 {archive_path.name}。

用 edit 更新以下章节：
- 上次更新：改为本轮时间
- 核心判断：3-5 条当前最重要的判断
- 追踪中事件：表格（事件/状态/上次更新）
- 本轮新增：本轮关键资讯
- 已归档：不再活跃的旧事件

修正旧判断时在"本轮新增"中标注 [修正]，不要静默改。

先 read_file 读 {archive_path}，再 read_file 读 runs/briefing.md，然后 edit 更新。"""
    output = _run_cli(_cli_cmd(prompt), timeout=300)
    if not output:
        log.error("briefing 更新失败")
    else:
        log.info("briefing 已更新")


def step5_push_tg(archive_path):
    """推送深度分析到 TG。"""
    log.info("=== Step 5: TG 推送 ===")
    if not TG_TOKEN or not TG_CHAT:
        log.info("未配置 TG，跳过")
        return

    import requests
    text = archive_path.read_text(encoding="utf-8")
    blocks = re.findall(r'(★{3,5}.*?)(?=\n★|\n## |\Z)', text, re.DOTALL)
    if not blocks:
        log.info("无≥3分条目，跳过推送")
        return

    msg = f"📊 深度分析 — {archive_path.stem}\n\n" + "\n\n".join(blocks)
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    failed = 0
    for i in range(0, len(msg), 4000):
        r = requests.post(url, json={"chat_id": TG_CHAT, "text": msg[i:i+4000]}, timeout=10)
        if not r.ok:
            failed += 1
            log.error(f"TG 推送失败: {r.status_code} {r.text[:200]}")
    if failed:
        log.error(f"TG 推送: {failed} 段失败")
    else:
        log.info(f"已推送 {len(blocks)} 条≥3分分析到 TG")


def main():
    triage_path = None
    args = sys.argv[1:]
    if "--triage" in args:
        idx = args.index("--triage")
        triage_path = Path(args[idx + 1])
        log.info(f"跳过粗筛，使用已有 triage: {triage_path}")

    if not triage_path:
        triage_path = step1_triage()
        if not triage_path:
            log.error("粗筛失败，退出")
            sys.exit(1)

    results = step2_analyze(triage_path)
    archive_path = step3_archive(triage_path, results)
    step4_update_briefing(archive_path)
    step5_push_tg(archive_path)

    # 清理 tmp
    for f in TMP_DIR.glob("analyze_*.md"):
        f.unlink()
    if triage_path.is_relative_to(TMP_DIR):
        triage_path.unlink()
    log.info("已清理 runs/tmp/")

    log.info("=== 完成 ===")


if __name__ == "__main__":
    main()
