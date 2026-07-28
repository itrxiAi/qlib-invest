"""
news_deep_analysis.py — 每8小时对过去8h的新闻做重要性评分 + 深度解析。

流程：
  1. 读取过去8h的 news_digest/YYYY-MM-DD.md
  2. 提取"新增重要消息"和"已知消息更新"条目
  3. LLM 评分(1-5) + 深度解析
  4. 评分≥4的推送到 Telegram

深度解析原则：
  - 事件定性：首次还是延续？升级还是降温？
  - 关键事实：数字和主体
  - 上下文关联：与已知事件的关联
  - 不确定性：什么还不清楚
  - 不猜利好利空，只帮你快速判断"值不值得细看"

用法：
  python news_deep_analysis.py              # 正常运行（过去8h）
  python news_deep_analysis.py --test       # 只解析不调 API、不推送
  python news_deep_analysis.py --hours 24   # 指定回看小时数
"""

import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

DIGEST_DIR = ROOT / "runs" / "news_digest"
CST = timezone(timedelta(hours=8))

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-reasoner"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _now_cst():
    return datetime.now(CST)


# ─── 读取过去 N 小时的 digest 条目 ──────────────────────────────────

def load_recent_digests(hours=8):
    """读取过去 hours 小时的 digest 条目。
    返回 [(timestamp_str, category, source, time_str, content), ...]
    category: "new" | "update" | "noise"
    """
    now = _now_cst()
    cutoff = now - timedelta(hours=hours)
    items = []

    # 可能跨天，检查今天和昨天的文件
    dates_to_check = []
    for h in range(hours + 1):
        dt = now - timedelta(hours=h)
        dates_to_check.append(dt.strftime("%Y-%m-%d"))
    dates_to_check = sorted(set(dates_to_check))

    for date_str in dates_to_check:
        path = DIGEST_DIR / f"{date_str}.md"
        if not path.exists():
            continue
        items.extend(_parse_digest_file(path, date_str, cutoff))

    return items


def _parse_digest_file(path, date_str, cutoff):
    """解析单个 digest 文件，提取条目。
    每个增量提取段以 '## YYYY-MM-DD HH:00 增量提取' 开头。
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    items = []

    # 解析文件头的生成时间
    file_gen_time = None
    for line in lines[:5]:
        m = re.match(r'_生成于 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})_', line.strip())
        if m:
            file_gen_time = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=CST)
            break

    cur_section_time = None
    cur_category = None  # "new" | "update" | "noise"

    for line in lines:
        s = line.strip()

        # 匹配增量提取段头：## 2026-07-16 03:00 增量提取
        m = re.match(r'^## (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}) 增量提取', s)
        if m:
            seg_date = m.group(1)
            seg_hour = int(m.group(2))
            seg_min = int(m.group(3))
            cur_section_time = datetime.strptime(
                f"{seg_date} {seg_hour:02d}:{seg_min:02d}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=CST)
            cur_category = None
            continue

        # 匹配分类标题
        if s.startswith("## 新增重要消息"):
            cur_category = "new"
            continue
        elif s.startswith("## 已知消息更新"):
            cur_category = "update"
            continue
        elif s.startswith("## 噪音"):
            cur_category = "noise"
            continue
        elif s.startswith("## ") or s.startswith("---"):
            cur_category = None
            continue

        # 匹配条目行
        if cur_category and s.startswith("- "):
            # 过滤掉时间早于 cutoff 的条目
            if cur_section_time and cur_section_time < cutoff:
                continue
            # 解析条目
            entry = _parse_entry(s, cur_category, cur_section_time)
            if entry:
                items.append(entry)

    return items


def _parse_entry(line, category, section_time):
    """解析单条 digest 条目。
    格式：- [Source] [HH:MM] 内容  或  - [Source] [MM-DD HH:MM] 内容
    """
    # 去掉前缀 "- "
    content = line[2:].strip()

    # 提取来源和时间：[Source] [Time] text 或 [Source Time] text
    m = re.match(r'\[([^\]]+)\]\s*\[([^\]]+)\]\s*(.+)', content)
    if m:
        source = m.group(1).strip()
        time_str = m.group(2).strip()
        text = m.group(3).strip()
    else:
        m1 = re.match(r'\[([^\]]+)\]\s*(.+)', content)
        if m1:
            source = m1.group(1).strip()
            text = m1.group(2).strip()
            tm = re.search(r'(\d{2}:\d{2})', source)
            time_str = tm.group(1) if tm else (section_time.strftime("%H:%M") if section_time else "")
        else:
            source = "unknown"
            time_str = section_time.strftime("%H:%M") if section_time else ""
            text = content

    ts_str = section_time.strftime("%Y-%m-%d %H:%M") if section_time else ""

    return {
        "ts": ts_str,
        "category": category,
        "source": source,
        "time": time_str,
        "text": text,
    }


def load_digest_by_date(date_str):
    """读取指定日期的全部 digest 条目（忽略 cutoff）。"""
    path = DIGEST_DIR / f"{date_str}.md"
    if not path.exists():
        return []
    # cutoff 设为极早时间，保留全部条目
    epoch = datetime(2000, 1, 1, tzinfo=CST)
    return _parse_digest_file(path, date_str, epoch)


# ─── LLM 调用 ──────────────────────────────────────────────────────

def call_llm(prompt, system="你是投研新闻深度分析编辑，所有输出必须使用中文", max_tokens=8000):
    """调用 DeepSeek API。"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    for attempt in range(3):
        try:
            resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=180)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < 2:
                print(f"    LLM 第{attempt+1}次失败: {e}，重试中...", file=sys.stderr)
                time.sleep(3)
            else:
                raise


# ─── 构建 prompt ────────────────────────────────────────────────────

def build_prompt(items):
    """构建深度分析 prompt。"""

    # 按类别分组条目
    new_lines = []
    update_lines = []
    noise_lines = []

    for it in items:
        line = f"- [{it['source']}] [{it['time']}] {it['text']}"
        if it["category"] == "new":
            new_lines.append(line)
        elif it["category"] == "update":
            update_lines.append(line)
        else:
            noise_lines.append(line)

    sections = []
    if new_lines:
        sections.append("### 新增重要消息\n" + "\n".join(new_lines))
    if update_lines:
        sections.append("### 已知消息更新\n" + "\n".join(update_lines))
    if noise_lines:
        sections.append("### 噪音\n" + "\n".join(noise_lines))

    all_news = "\n\n".join(sections)

    prompt = f"""你是投研新闻深度分析编辑。下面是过去数小时内已过滤的新闻条目。

## 任务

**第一步：事件聚合**
将同一事件的多条报道合并为"事件簇"。判断标准：
- 同一主体（国家/公司/人物）+ 同一话题 = 同一事件簇
- 不同阶段报道（如"打击→扩大打击→讨论夺取岛屿"）归为同一事件簇的"进展线"
- 完全重复的报道只保留一条

**第二步：对每个事件簇评分和深度分析**

## 评分标准（1-5分，针对事件簇而非单条）

- 5分：重大突发 — 战争/停火、紧急加息/降息、重大黑天鹅、系统性风险
- 4分：重要事实 — 超预期数据、重大融资/并购落地、政策出台、关键人物突发表态
- 3分：有价值但非紧急 — 外交进展、行业趋势、官员常规表态、已知事件延续
- 2分：例行更新 — 指数日常数值、常规会议、重复表述
- 1分：噪音

## 深度分析要求（每个事件簇输出一条，不逐条输出）

1. **定性**：这是最重要的部分。给出增量判断，不是复述原文。比如"从威慑转向行动"而不是"某某发表声明"。**≤30字**。
2. **增量**：综合多条报道得出的新信息/新结论。原文里不明显的推论。**≤60字**。
3. **关联**：跨事件簇的关联。比如"A事件 + B事件 = C风险上升"。无关联则不写。**≤30字**。

**不要复述原文。不要预测利好利空。不要说"利好XX/利空XX"。**
**核心目标是帮读者快速判断"这事多大，值不值得花时间细看"。**

## 输出格式

只输出评分≥3的事件簇（1-2分不输出）。每个事件簇一条：

★★★★★ 事件标题（你总结的，不是原文标题）
  定性：增量判断（这是重点）
  增量：综合多条报道得出的新结论
  关联：（可选，跨事件簇）

★★★★☆ ...

★★★☆☆ ...

---

### 待分析新闻

{all_news}
"""
    return prompt


# ─── Telegram 推送 ─────────────────────────────────────────────────

def push_telegram(content, min_score=4):
    """推送深度分析到 Telegram（全部内容，不按评分过滤）。"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未配置，跳过推送", file=sys.stderr)
        return False

    now = _now_cst().strftime("%H:%M")
    hour = _now_cst().hour
    if hour < 8:
        period = "早盘"
    elif hour < 16:
        period = "午盘"
    else:
        period = "晚报"

    text = f"📊 投研深度分析 {period} {now}\n\n" + content.strip()

    # Telegram 消息限制 4096 字符，分段发送
    max_len = 4000
    chunks = []
    cur = ""
    for line in text.splitlines():
        if len(cur) + len(line) + 1 > max_len:
            chunks.append(cur)
            cur = line
        else:
            cur += "\n" + line if cur else line
    if cur:
        chunks.append(cur)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    success = True
    for i, chunk in enumerate(chunks):
        try:
            resp = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
            }, timeout=15)
            if resp.status_code != 200:
                print(f"  TG推送第{i+1}段失败: {resp.status_code} {resp.text}", file=sys.stderr)
                success = False
            else:
                print(f"  TG推送第{i+1}/{len(chunks)}段成功", file=sys.stderr)
        except Exception as e:
            print(f"  TG推送异常: {e}", file=sys.stderr)
            success = False

    return success


# ─── 归档 ──────────────────────────────────────────────────────────

DEEP_DIR = ROOT / "runs" / "news_deep"


def save_deep_analysis(content, hours=8):
    """归档深度分析结果。"""
    DEEP_DIR.mkdir(parents=True, exist_ok=True)
    now = _now_cst()
    date_str = now.strftime("%Y-%m-%d")
    hour_str = now.strftime("%H%M")
    path = DEEP_DIR / f"{date_str}_{hour_str}.md"

    header = f"# 投研深度分析 — {now.strftime('%Y-%m-%d %H:%M')}\n"
    header += f"_回看过去 {hours} 小时_\n\n"

    path.write_text(header + content + "\n", encoding="utf-8")
    return path


# ─── 主流程 ────────────────────────────────────────────────────────

def run(hours=8, test=False, min_score=4, date=None):
    """主入口：读取过去 hours 小时 digest → LLM 评分+深度解析 → 推送 TG。
    date: 指定日期 'YYYY-MM-DD'，则读取该日全部条目（忽略 hours）。
    """
    now = _now_cst()
    if date:
        print(f"[{now.strftime('%Y-%m-%d %H:%M')}] 开始深度分析（指定日期 {date}）", file=sys.stderr)
        items = load_digest_by_date(date)
    else:
        print(f"[{now.strftime('%Y-%m-%d %H:%M')}] 开始深度分析（过去 {hours}h）", file=sys.stderr)
        items = load_recent_digests(hours=hours)
    new_count = sum(1 for it in items if it["category"] == "new")
    update_count = sum(1 for it in items if it["category"] == "update")
    noise_count = sum(1 for it in items if it["category"] == "noise")
    print(f"  读取 {len(items)} 条（新增 {new_count} / 更新 {update_count} / 噪音 {noise_count}）", file=sys.stderr)

    if not items:
        print("  无条目，跳过", file=sys.stderr)
        return None

    # 2. 构建 prompt
    prompt = build_prompt(items)
    print(f"  Prompt: {len(prompt)} 字符", file=sys.stderr)

    if test:
        print("  [test mode] 跳过 API 调用", file=sys.stderr)
        print(prompt[:1000] + "...", file=sys.stderr)
        return prompt

    # 3. 调 LLM
    try:
        result = call_llm(prompt)
        print(f"  LLM 返回 {len(result)} 字符", file=sys.stderr)
    except Exception as e:
        print(f"  LLM 调用失败: {e}", file=sys.stderr)
        return None

    # 4. 归档
    path = save_deep_analysis(result, hours=hours)
    print(f"  归档 → {path}", file=sys.stderr)

    # 5. 推送 TG
    push_telegram(result, min_score=min_score)

    print(result)
    return result


if __name__ == "__main__":
    hours = 8
    test_mode = False
    min_score = 4
    date = None

    for arg in sys.argv[1:]:
        if arg == "--test":
            test_mode = True
        elif arg.startswith("--hours="):
            hours = int(arg.split("=", 1)[1])
        elif arg.startswith("--min-score="):
            min_score = int(arg.split("=", 1)[1])
        elif arg.startswith("--date="):
            date = arg.split("=", 1)[1]

    run(hours=hours, test=test_mode, min_score=min_score, date=date)
