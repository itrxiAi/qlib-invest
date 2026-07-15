"""
news_llm_filter.py — 用 GLM-4-Flash 对每小时拉取的新闻做重要消息提取 + 语义去重。

增量模式（按小时过滤）：
  1. 读取最新快照（news_scan.md / twitter_scan.md）
  2. 只取当前小时内的条目（定时任务每小时跑一次，上一小时的已处理过）
  3. 读取最近几天的 LLM digest 摘要作为语义去重参考
  4. 只把当前小时条目 + 历史摘要发给 GLM-4-Flash（token 极低）
  5. 输出 runs/news_digest/YYYY-MM-DD.md（增量追加）

用法：
  python news_llm_filter.py              # 正常运行（当前小时）
  python news_llm_filter.py --test       # 只解析不调 API（调试用）
  python news_llm_filter.py --backfill   # 补齐历史：按小时分组处理所有快照数据
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

ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")
ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4-long"

DIGEST_DIR = ROOT / "runs" / "news_digest"
SUMMARY_FILE = ROOT / "runs" / "news_summary.md"
CST = timezone(timedelta(hours=8))

SCAN_FILES = {
    "techmeme": ROOT / "news_scan.md",
    "twitter": ROOT / "twitter_scan.md",
}


def _today():
    return datetime.now(CST).strftime("%Y-%m-%d")


def _now_str():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M")


# ─── 解析快照文件 ───────────────────────────────────────────────────

def parse_techmeme(path):
    """解析 news_scan.md，返回 [{when, source, title, link}, ...]"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    items = []
    for m in re.finditer(
        r'- (\d{2}-\d{2} \d{2}:\d{2})\s+`([^`]+)`\s+\[([^\]]+)\]\(([^)]+)\)', text
    ):
        items.append({
            "when": m.group(1),
            "source": m.group(2),
            "title": m.group(3),
            "link": m.group(4),
        })
    return items


def parse_twitter(path):
    """解析 twitter_scan.md，返回 [{when, user, text, likes, retweets, replies, url}, ...]"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    items = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(
            r'- (\d{2}:\d{2}) \[@([^\]]+)\] (.+?)\s+❤(\d+) 🔁(\d+) 💬(\d+)', line
        )
        if m:
            url = ""
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("http"):
                url = lines[i + 1].strip()
                i += 1
            items.append({
                "when": m.group(1),
                "user": m.group(2),
                "text": m.group(3),
                "likes": int(m.group(4)),
                "retweets": int(m.group(5)),
                "replies": int(m.group(6)),
                "url": url,
            })
        i += 1
    return items


# ─── 按小时过滤 ────────────────────────────────────────────────────

def _current_hour():
    """当前 CST 小时，如 '13'。"""
    return datetime.now(CST).strftime("%H")


def _today_md():
    """今天 MM-DD，如 '07-12'。"""
    return datetime.now(CST).strftime("%m-%d")


def filter_by_hour(tm_items, tw_items, hour=None, today_md=None):
    """保留当前小时及前一小时的条目。
    - Techmeme: 时间格式 'MM-DD HH:MM'，需匹配日期+小时
    - Twitter: 时间格式 'HH:MM'，只匹配小时（快照是实时的，日期默认当天）
    """
    now = datetime.now(CST)
    if hour is None:
        hour = now.strftime("%H")
    if today_md is None:
        today_md = now.strftime("%m-%d")

    prev = now - timedelta(hours=1)
    prev_hour = prev.strftime("%H")
    prev_md = prev.strftime("%m-%d")

    new_tm = [it for it in tm_items if
              it["when"].startswith(f"{today_md} {hour}:") or
              it["when"].startswith(f"{prev_md} {prev_hour}:")]
    new_tw = [it for it in tw_items if
              it["when"].startswith(f"{hour}:") or
              it["when"].startswith(f"{prev_hour}:")]
    return new_tm, new_tw


# ─── 读取历史 digest 摘要 ──────────────────────────────────────────

def load_history_summary():
    """读取资讯摘要快照，用于语义去重。"""
    if not SUMMARY_FILE.exists():
        return "（无历史）"
    text = SUMMARY_FILE.read_text(encoding="utf-8").strip()
    return text if text else "（无历史）"


def update_summary(result, date_str=None, days=2):
    """从 LLM 输出中提取新增重要消息，更新摘要快照。
    保留最近 days 天的条目，去掉过时的。
    backfill 模式（date_str 非 today）不做 cutoff，避免历史数据被过滤。"""
    # 提取新增重要消息
    new_items = []
    in_section = False
    for line in result.splitlines():
        if line.strip().startswith("## 新增重要消息"):
            in_section = True
            continue
        if line.strip().startswith("## ") or line.strip().startswith("---"):
            in_section = False
            continue
        if in_section and line.strip().startswith("- "):
            new_items.append(line.strip())

    if not new_items:
        return

    # 读取现有快照，按日期分组
    existing = {}
    if SUMMARY_FILE.exists():
        cur_date = None
        for line in SUMMARY_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("### "):
                cur_date = line[4:].strip()
                if cur_date not in existing:
                    existing[cur_date] = []
            elif line.strip().startswith("- ") and cur_date:
                existing[cur_date].append(line.strip())

    # 添加条目到指定日期（去重：跳过已存在的条目）
    target_date = date_str or _today()
    if target_date not in existing:
        existing[target_date] = []
    existing_set = set(existing[target_date])
    for item in new_items:
        if item not in existing_set:
            existing_set.add(item)
            existing[target_date].insert(0, item)

    # 只保留最近 days 天（仅正常模式，backfill 不过滤）
    today_str = _today()
    if date_str == today_str:
        cutoff = (datetime.now(CST) - timedelta(days=days)).strftime("%Y-%m-%d")
        kept = {d: items for d, items in existing.items() if d >= cutoff}
    else:
        kept = existing

    # 写入
    lines = []
    for d in sorted(kept.keys(), reverse=True):
        lines.append(f"### {d}")
        lines.extend(kept[d])
        lines.append("")
    SUMMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─── 调 GLM API ─────────────────────────────────────────────────────

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
FALLBACK_MODEL = "deepseek-reasoner"


def call_glm(prompt, system="你是投研新闻编辑助手，所有输出必须使用中文，英文公司名/股票代码保留英文", max_tokens=4000):
    """调用智谱 GLM API，失败后降级到 deepseek-reasoner。"""
    if not ZHIPU_API_KEY:
        raise RuntimeError("ZHIPU_API_KEY 未设置，请检查 .env")

    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
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
            resp = requests.post(ZHIPU_URL, headers=headers, json=payload, timeout=180)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt < 2:
                print(f"    LLM 第{attempt+1}次失败: {e}，重试中...", file=sys.stderr)
                time.sleep(3)
            else:
                print(f"    {MODEL} 3次重试失败，降级到 {FALLBACK_MODEL}", file=sys.stderr)
                return call_deepseek(prompt, system, max_tokens)


def call_deepseek(prompt, system, max_tokens=2000):
    """调用 DeepSeek API 作为 fallback。"""
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置，请检查 .env")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": FALLBACK_MODEL,
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
                print(f"    DeepSeek 第{attempt+1}次失败: {e}，重试中...", file=sys.stderr)
                time.sleep(3)
            else:
                raise


# ─── 构建 prompt ────────────────────────────────────────────────────

def build_prompt(tm_new, tw_new, history_summary):
    """构建 LLM prompt：只发新增条目 + 历史摘要，让 LLM 做语义去重。"""

    news_lines = []

    if tm_new:
        news_lines.append("## Techmeme 科技头条（新增）")
        for it in tm_new:
            news_lines.append(f"- [{it['when']}] {it['title']}")
    if tw_new:
        news_lines.append("\n## Twitter 快讯（新增，❤点赞 🔁转发）")
        for it in tw_new:
            news_lines.append(f"- [{it['when']}] @{it['user']} 🔁{it['retweets']} ❤{it['likes']} | {it['text']}")
    today_news = "\n".join(news_lines)

    # 精简历史摘要：去掉转发/点赞等噪音，只保留核心语义
    compact_history = _compact_history(history_summary)

    prompt = f"""你是投研新闻编辑。下面是本次新增的新闻条目，以及最近几天已提取的重要消息摘要。

## 分类规则（按优先级依次判断）

**第一步：去重判断** — 对每条新增新闻，检查是否与历史摘要中某条属于"同一事件"：
- 同一事件 = 涉及相同的主体（公司/国家/人物）+ 相同的话题（如同一军事行动、同一融资、同一政策）
- 同一事件的不同阶段报道（如"伊朗关闭海峡"→"伊朗不允许船只通过"→"伊朗警告严厉回应"）= 已知消息更新
- 同一事件的完全重复报道（内容几乎相同）= 噪音
- 历史摘要中已有且本次无任何新进展 = 不要输出

**第二步：新增判断** — 未匹配历史的新话题，判断是否有投研价值：
- 有价值：产能/价格/认证/订单/政策/地缘/大宗商品/融资/并购/财报等具体事实 → 新增重要消息
- 无价值：娱乐/社会/个人观点/转发感谢/无实质信息 → 噪音

**第三步：格式化** — 每条只写一句话"重点"（so what），保留关键数字和主体。

## 去重示例

示例1（同一事件不同阶段→已知更新）：
  历史：伊朗革命卫队海军宣布霍尔木兹海峡关闭
  新增：伊朗革命卫队海军不允许任何船只通过
  → 已知消息更新（同一事件的升级）

示例2（同一事件重复→噪音）：
  历史：特朗普表示伊朗没有空军、海军和军事力量
  新增：特朗普重申伊朗没有空军、海军和军事力量
  → 噪音（完全重复，无新信息）

示例3（新话题→新增）：
  历史：无相关
  新增：PixVerse完成C轮融资4.39亿美元，估值20亿美元
  → 新增重要消息

## 输出要求
1. 所有输出必须用中文撰写，英文公司名/股票代码保留英文（如 AAPL、SK Hynix、OpenAI）
2. 输出只能来自"本次新增新闻"的条目，历史摘要仅供去重参考，绝对不能从中复制
3. 每条新闻只能出现在一个分类中，不可重复
4. Twitter 消息参考转发数（🔁）和点赞数（❤）判断重要度，🔁100+ 或 ❤500+ 为高信号
5. 严格按以下格式输出，不要多余解释：

## 新增重要消息
- [来源] [时间] 重点内容一句话
- ...

## 已知消息更新（历史已有但本次有新进展）
- [来源] [时间] 重点内容一句话
- ...

## 噪音（一行带过，保留原标题关键词）
- [来源] [时间] 原标题简述
- ...

---

### 本次新增新闻
{today_news}

### 历史已提取的重要消息（用于语义去重，仅供参照，不要复制到输出）
{compact_history}
"""
    return prompt


def _compact_history(history_summary):
    """精简历史摘要：去掉转发/点赞等元数据，只保留核心语义。"""
    lines = history_summary.splitlines()
    compact = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("###"):
            compact.append(line)
            continue
        # 去掉 @user 🔁N ❤N 等元数据
        s = re.sub(r"@\S+\s*", "", s)
        s = re.sub(r"🔁\d+\s*", "", s)
        s = re.sub(r"❤\d+\s*", "", s)
        s = re.sub(r"\|\s*", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if s.startswith("- "):
            compact.append(s)
        else:
            compact.append(line)
    return "\n".join(compact)


# ─── 归档 ──────────────────────────────────────────────────────────

def save_digest(content, date_str=None, hour_label=None):
    """增量追加到 runs/news_digest/YYYY-MM-DD.md。"""
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    if date_str is None:
        date_str = _today()
    path = DIGEST_DIR / f"{date_str}.md"

    label = hour_label or _now_str()

    if path.exists():
        text = path.read_text(encoding="utf-8")
        text = text.rstrip() + f"\n\n---\n\n## {label} 增量提取\n\n" + content + "\n"
        path.write_text(text, encoding="utf-8")
    else:
        header = f"# 新闻 LLM 提取 — {date_str}\n\n"
        header += f"_生成于 {label}_\n\n"
        path.write_text(header + content + "\n", encoding="utf-8")

    return path


# ─── 主流程 ────────────────────────────────────────────────────────

RAW_DIR = ROOT / "runs" / "news_raw"


def _parse_raw_by_hour(path, source):
    """解析归档文件，按 ## HH:00 分节返回 {hour: [items]}。"""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    by_hour = {}
    cur_hour = None
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        m = re.match(r'^## (\d{2}):00$', s)
        if m:
            cur_hour = m.group(1)
            by_hour.setdefault(cur_hour, [])
            i += 1
            continue
        if cur_hour and s.startswith("- "):
            if source == "techmeme":
                m = re.match(r'- (?:(\d{2}-\d{2}) )?(\d{2}:\d{2})\s+`([^`]+)`\s+\[([^\]]+)\]\(([^)]+)\)', s)
                if m:
                    when = f"{m.group(1) or ''} {m.group(2)}".strip()
                    item = {"when": when, "source": m.group(3), "title": m.group(4), "link": m.group(5)}
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith("> "):
                        item["summary"] = lines[i + 1].strip()[2:]
                        i += 1
                    by_hour[cur_hour].append(item)
            elif source == "twitter":
                m = re.match(r'- (\d{2}:\d{2}) \[@([^\]]+)\] (.+?)\s+❤(\d+) 🔁(\d+) 💬(\d+)', s)
                if m:
                    url = ""
                    if i + 1 < len(lines) and lines[i + 1].strip().startswith("http"):
                        url = lines[i + 1].strip()
                        i += 1
                    by_hour[cur_hour].append({
                        "when": m.group(1), "user": m.group(2), "text": m.group(3),
                        "likes": int(m.group(4)), "retweets": int(m.group(5)),
                        "replies": int(m.group(6)), "url": url,
                    })
        i += 1
    return by_hour


def _group_by_hour_raw(days_back=2, only_date=None, only_hour=None):
    """从 runs/news_raw/ 归档文件读取，按 (date, hour) 分组。
    只保留最近 days_back 天的数据；若指定 only_date 则只跑该日期；
    若指定 only_hour 则只跑该小时。
    返回 [(date_str, hour, tm_list, tw_list), ...]"""
    from collections import defaultdict
    groups = defaultdict(lambda: {"tm": [], "tw": []})

    cutoff = (datetime.now(CST) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    for path in sorted(RAW_DIR.glob("*_techmeme.md")):
        date_str = path.stem[:10]  # YYYY-MM-DD
        if only_date and date_str != only_date:
            continue
        if not only_date and date_str < cutoff:
            continue
        by_hour = _parse_raw_by_hour(path, "techmeme")
        for hour, items in by_hour.items():
            if only_hour and hour != only_hour:
                continue
            groups[(date_str, hour)]["tm"].extend(items)

    for path in sorted(RAW_DIR.glob("*_twitter.md")):
        date_str = path.stem[:10]
        if only_date and date_str != only_date:
            continue
        if not only_date and date_str < cutoff:
            continue
        by_hour = _parse_raw_by_hour(path, "twitter")
        for hour, items in by_hour.items():
            if only_hour and hour != only_hour:
                continue
            groups[(date_str, hour)]["tw"].extend(items)

    return sorted(groups.items())


def run(test=False, backfill=False, only_date=None, only_hour=None):
    """主入口：解析快照 → 按小时过滤 → 调 LLM → 归档。"""
    print(f"[{_now_str()}] 开始 LLM 新闻提取", file=sys.stderr)

    # 1. 解析快照（backfill 模式不需要，直接跳过）
    if backfill:
        hour_groups = _group_by_hour_raw(only_date=only_date, only_hour=only_hour)
        print(f"  补齐模式: {len(hour_groups)} 个小时分组（来源: runs/news_raw/）", file=sys.stderr)
        for (date_str, hour), items in hour_groups:
            tm_h = items["tm"]
            tw_h = items["tw"]
            cnt = len(tm_h) + len(tw_h)
            print(f"\n  [{date_str} {hour}:00] {cnt} 条", file=sys.stderr)

            if cnt == 0:
                continue

            history_summary = load_history_summary()
            prompt = build_prompt(tm_h, tw_h, history_summary)
            print(f"    Prompt: {len(prompt)} 字符", file=sys.stderr)

            if test:
                print(f"    [test mode] 跳过 API", file=sys.stderr)
                continue

            try:
                result = call_glm(prompt)
                print(f"    LLM 返回 {len(result)} 字符", file=sys.stderr)
            except Exception as e:
                print(f"    LLM 调用失败: {e}", file=sys.stderr)
                continue

            path = save_digest(result, date_str=date_str, hour_label=f"{date_str} {hour}:00")
            update_summary(result, date_str=date_str)
            print(f"    归档 → {path}", file=sys.stderr)
            print(result)
            time.sleep(1)  # 避免限流

        return

    # 正常模式：从 raw 归档读取今天的数据
    today_str = _today()
    today_md = _today_md()
    hour = _current_hour()
    prev_dt = datetime.now(CST) - timedelta(hours=1)
    prev_hour = prev_dt.strftime("%H")
    prev_md = prev_dt.strftime("%m-%d")

    tm_path = RAW_DIR / f"{today_str}_techmeme.md"
    tw_path = RAW_DIR / f"{today_str}_twitter.md"
    tm_by_hour = _parse_raw_by_hour(tm_path, "techmeme") if tm_path.exists() else {}
    tw_by_hour = _parse_raw_by_hour(tw_path, "twitter") if tw_path.exists() else {}

    # 跨天时也读昨天的文件
    if prev_hour > hour:  # e.g. hour=00, prev_hour=23
        prev_date = prev_dt.strftime("%Y-%m-%d")
        tm_prev_path = RAW_DIR / f"{prev_date}_techmeme.md"
        tw_prev_path = RAW_DIR / f"{prev_date}_twitter.md"
        if tm_prev_path.exists():
            tm_by_hour_prev = _parse_raw_by_hour(tm_prev_path, "techmeme")
            tm = tm_by_hour.get(hour, []) + tm_by_hour_prev.get(prev_hour, [])
        else:
            tm = tm_by_hour.get(hour, []) + tm_by_hour.get(prev_hour, [])
        if tw_prev_path.exists():
            tw_by_hour_prev = _parse_raw_by_hour(tw_prev_path, "twitter")
            tw = tw_by_hour.get(hour, []) + tw_by_hour_prev.get(prev_hour, [])
        else:
            tw = tw_by_hour.get(hour, []) + tw_by_hour.get(prev_hour, [])
    else:
        tm = tm_by_hour.get(hour, []) + tm_by_hour.get(prev_hour, [])
        tw = tw_by_hour.get(hour, []) + tw_by_hour.get(prev_hour, [])

    total = len(tm) + len(tw)
    print(f"  归档 {today_str} {hour}:00(+{prev_hour}): Techmeme {len(tm)} + Twitter {len(tw)} = {total} 条", file=sys.stderr)

    if total == 0:
        print("  当前小时无新增条目，跳过 LLM 调用", file=sys.stderr)
        return

    tm_new = tm
    tw_new = tw
    new_total = total

    # 检查上一小时是否已处理过
    digest_path = DIGEST_DIR / f"{today_str}.md"
    if digest_path.exists():
        digest_text = digest_path.read_text(encoding="utf-8")
        if f"{today_str} {prev_hour}:" in digest_text:
            print(f"  上一小时 {prev_hour}:00 已处理过，跳过", file=sys.stderr)
            return

    # 3. 加载历史摘要（用于语义去重）
    history_summary = load_history_summary()
    print(f"  历史摘要: {len(history_summary)} 字符", file=sys.stderr)

    # 4. 构建 prompt
    prompt = build_prompt(tm_new, tw_new, history_summary)
    print(f"  Prompt: {len(prompt)} 字符 (~{len(prompt)//3} tokens)", file=sys.stderr)

    if test:
        print("  [test mode] 跳过 API 调用", file=sys.stderr)
        print(prompt[:800] + "...", file=sys.stderr)
        return

    # 5. 调 GLM
    try:
        result = call_glm(prompt)
        print(f"  LLM 返回 {len(result)} 字符", file=sys.stderr)
    except Exception as e:
        print(f"  LLM 调用失败: {e}", file=sys.stderr)
        return

    # 6. 归档
    path = save_digest(result, hour_label=f"{today_str} {prev_hour}:00")
    update_summary(result, date_str=today_str)
    print(f"  归档 → {path}", file=sys.stderr)

    print(result)
    return result


if __name__ == "__main__":
    test_mode = "--test" in sys.argv
    backfill_mode = "--backfill" in sys.argv
    only_date = None
    only_hour = None
    for arg in sys.argv:
        if arg.startswith("--date="):
            only_date = arg.split("=", 1)[1]
        if arg.startswith("--hour="):
            only_hour = arg.split("=", 1)[1]
    run(test=test_mode, backfill=backfill_mode, only_date=only_date, only_hour=only_hour)
