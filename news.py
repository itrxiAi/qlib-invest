"""
news.py — 全球科技资讯 + 公司新闻 + 分析师评级（数据源：Finnhub，结构化全球覆盖）。

源：Finnhub (https://finnhub.io) 免费层
  - /news?category=general   全球市场新闻（宏观+科技，实时）
  - /company-news?symbol=X   个股公司新闻（含供应链/产品消息）
  - /stock/recommendation    分析师评级趋势（强买/买/持有/卖/强卖）
  (目标价 /price-target 免费层 403，未用。)

正文：article_text(url) 抓网页正文；lead(url) 取开头几句作重点摘要；
      enrich(groups) 给每源前几条补摘要。Finnhub 给的是真实原文链接，可直接抓。

用法：
  python news.py                  # 全球新闻 + 龙头公司新闻 + 评级趋势
  python news.py tech 2           # 全球新闻 + 龙头公司新闻（近2天）
  python news.py research 2       # 只看分析师评级趋势
  python news.py "HBM" 2          # 关键词在全球新闻里过滤
  末尾加 full / -f                # 补抓前几条的「重点」摘要
  末尾加 clean / -c               # 科技相关性过滤（剔游戏娱乐）
"""
import os
import sys
import re
import time
import base64
import html
from email.utils import parsedate_to_datetime
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA}

# Finnhub 配置
FINNHUB_KEY = "d8d6fshr01qub2purmh0d8d6fshr01qub2purmhg"
FH = "https://finnhub.io/api/v1"
# 产业链龙头篮子（拆链锚点；company-news 逐只拉）
LEADERS = {
    "NVDA": "英伟达", "TSM": "台积电", "ASML": "ASML", "AVGO": "博通",
    "MU": "美光", "AMD": "AMD", "INTC": "英特尔", "ANET": "Arista",
    "VRT": "Vertiv", "COHR": "相干", "MRVL": "美满", "APH": "安费诺",
}

# 科技相关性过滤（投研用：留硬科技/产业链，剔游戏娱乐生活）
TECH_KEEP = (
    "chip semiconductor foundry wafer hbm dram nand tsmc nvidia amd intel gpu cpu "
    "asic tpu soc datacenter data center server cloud ai llm model anthropic openai "
    "deepmind gemini claude robot humanoid drone satellite spacex ev battery lithium "
    "sodium-ion motor axial flux laser optical photonics quantum lithography euv "
    "packaging cowos soic serdes power grid energy fuel cell solar chipset pcie ram "
    "memory network bandwidth zero-day 0-day vulnerability breach hack cyber malware "
    "patch software open-source database api apple silicon macos ios android linux "
    "raspberry pi compute inference training transistor node fab supply"
).split()
GAME_DROP = (
    "game games gaming gamer xbox playstation ps5 nintendo switch steam dlc trailer "
    "rpg fps shooter console halo fable zelda ocarina kingdom hearts digimon minecraft "
    "destiny ubisoft eurogamer kotaku gematsu polygon ign pinkbike bike spacesuit prada "
    "vaccine diabetes onesie movie film tv show season episode"
).split()
# 多词短语单列（含空格，需整体匹配）
GAME_DROP += ["final fantasy", "street fighter", "marvel rivals", "resident evil",
              "fire emblem", "stellar blade", "gears of war", "killer bean",
              "alien: isolation", "ps plus", "switch 2"]

_KEEP_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(k) for k in TECH_KEEP) + r")(?![a-z])")
_DROP_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(k) for k in GAME_DROP) + r")(?![a-z])")


def is_tech(item):
    """投研相关性判定（全词匹配）：命中硬科技词→留；否则命中游戏娱乐词→弃；其余默认留。"""
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    if _KEEP_RE.search(text):
        return True
    if _DROP_RE.search(text):
        return False
    return True


def filter_tech(groups):
    """对每个源做科技相关性过滤，原地保留命中项。"""
    for src in groups:
        groups[src] = [it for it in groups[src] if is_tech(it)]
    return groups


def _get(url, params=None, timeout=25):
    try:
        return requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    except Exception as e:
        print(f"  [warn] {url[:50]}... -> {e}", file=sys.stderr)
        return None


def _strip(t):
    """去 HTML 标签 + 去多余空白。"""
    t = re.sub(r"(?s)<[^>]+>", " ", t or "")
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _item(title, link, ts, source, extra="", summary=""):
    return {"title": html.unescape((title or "").strip()), "link": link or "",
            "ts": ts, "source": source, "extra": extra,
            "summary": _strip(summary)[:400]}


# ---------- 抓正文 ----------
def resolve(url):
    """把 Google News RSS 跳转链解析成真实原文 URL；其他链接原样返回。"""
    if "news.google.com" not in url:
        return url
    m = re.search(r"/articles/([A-Za-z0-9_\-]+)", url)
    if m:
        raw = m.group(1)
        for pad in ("", "=", "==", "==="):
            try:
                dec = base64.urlsafe_b64decode(raw + pad).decode("latin-1")
                u = re.search(r"https?://[^\s\"'<>\\]+", dec)
                if u:
                    return u.group(0)
            except Exception:
                continue
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        if "news.google.com" not in r.url:
            return r.url
    except Exception:
        pass
    return url


def article_text(url, max_chars=20000):
    """下载网页并抽成纯文本正文。返回 (真实URL, 正文)。"""
    real = resolve(url)
    r = _get(real, timeout=25)
    if not (r and r.ok):
        return real, ""
    h = r.text
    h = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav|aside).*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = html.unescape(h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n+", "\n", h)
    return real, h.strip()[:max_chars]


def lead(url, chars=400):
    """取正文开头几句作「重点」摘要（跳过太短的导航/版权行）。"""
    _, text = article_text(url, max_chars=8000)
    for para in text.split("\n"):
        p = para.strip()
        if len(p) >= 80 and " " in p:
            return p[:chars]
    return text[:chars]


def enrich(groups, per_source=6, chars=300):
    """对每个源的前 N 条补抓「重点」摘要（仅在原本无 summary 时）。"""
    for items in groups.values():
        for it in items[:per_source]:
            if it.get("summary"):
                continue
            try:
                it["summary"] = lead(it["link"], chars)
            except Exception:
                pass
    return groups


# ---------- Finnhub 源 ----------
def _fh(path, **params):
    params["token"] = FINNHUB_KEY
    r = _get(f"{FH}/{path}", params=params, timeout=20)
    if not (r and r.ok):
        return None
    try:
        return r.json()
    except Exception:
        return None


def market_news(category="general", n=50):
    """全球市场新闻（category: general/forex/crypto/merger）。"""
    data = _fh("news", category=category) or []
    out = []
    for a in data[:n]:
        out.append(_item(a.get("headline"), a.get("url"), a.get("datetime", 0),
                         a.get("source", ""), summary=a.get("summary", "")))
    return out


def company_news(symbol, days=2, n=10):
    """单只个股近 N 日公司新闻（含供应链/产品消息）。"""
    to = time.strftime("%Y-%m-%d")
    frm = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    data = _fh("company-news", symbol=symbol, **{"from": frm, "to": to}) or []
    out = []
    for a in data[:n]:
        out.append(_item(a.get("headline"), a.get("url"), a.get("datetime", 0),
                         a.get("source", ""), summary=a.get("summary", "")))
    return out


def recommendation(symbol):
    """最新一期分析师评级分布。返回 dict 或 None。"""
    data = _fh("stock/recommendation", symbol=symbol) or []
    return data[0] if data else None


# ---------- Techmeme 源（编辑精选 + 同事件聚类，自带重要性排序） ----------
# River = 全量流，按日期分段、含多天，远多于 feed.xml 的 15 条。
TECHMEME_RIVER = "https://www.techmeme.com/river"
_TM_TZ = ZoneInfo("America/New_York")  # Techmeme River 日期/时间按美东计
_RITEM_RE = re.compile(r'<tr class="ritem">(.*?)</tr>', re.S)
_TIME_RE = re.compile(r"<td>([^<]*?(?:AM|PM))", re.I)
_CITE_RE = re.compile(r"<cite>(.*?)</cite>", re.S)
_ART_RE = re.compile(
    r'<a href="(https?://(?!www\.techmeme|techmeme)[^"]+)"[^>]*>(.*?)</a>', re.S)
_PML_RE = re.compile(r'pml="([^"]+)"')


def techmeme_news(days=2, n=80):
    """Techmeme River：按重要性聚类的全球科技要闻，覆盖多天。
    每条 = 一句话事实标题 + 媒体署名 + 真实原文链接 + 聚类页。"""
    r = _get(TECHMEME_RIVER, timeout=25)
    if not (r and r.ok):
        return []
    t = r.text
    cutoff = time.time() - days * 86400
    out = []
    # 按日期表头分段，给每段内的条目带上日期
    parts = re.split(r"<H2>([^<]+)</H2>", t)
    for k in range(1, len(parts), 2):
        day_label = parts[k].strip()
        try:
            day = datetime.strptime(day_label, "%B %d, %Y")
        except ValueError:
            continue  # 跳过 Sponsor Posts / Featured Podcasts 等非日期段
        for row in _RITEM_RE.findall(parts[k + 1]):
            a = _ART_RE.search(row)
            if not a:
                continue
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", a.group(2))).strip()
            link = a.group(1)
            # 媒体署名（cite 里最后一个 <A> 文本，或纯文本）
            pub = ""
            cm = _CITE_RE.search(row)
            if cm:
                names = re.findall(r">([^<]+)</A>", cm.group(1))
                pub = (names[-1] if names else re.sub(r"<[^>]+>", "", cm.group(1))).strip(" :")
            # 时间：日期+时刻按美东解析，转 epoch（显示时再换本地时区）
            tm = _TIME_RE.search(row)
            dt = day.replace(tzinfo=_TM_TZ)
            if tm:
                try:
                    hm = datetime.strptime(tm.group(1).strip(), "%I:%M %p")
                    dt = dt.replace(hour=hm.hour, minute=hm.minute)
                except ValueError:
                    pass
            ts = dt.timestamp()
            if ts < cutoff:
                continue
            # 聚类页（pml 如 260610p71 → /260610/p71）
            cluster = ""
            pm = _PML_RE.search(row)
            if pm and "p" in pm.group(1):
                d, p = pm.group(1).split("p", 1)
                cluster = f"https://www.techmeme.com/{d}/p{p}"
            out.append(_item(title, link, ts, "Techmeme",
                             extra=pub,
                             summary=f"Techmeme 聚类页: {cluster}" if cluster else ""))
    out.sort(key=lambda x: -x["ts"])
    return out[:n]


# ---------- 聚合 ----------
def hot_tech(days=2, n=50):
    """Techmeme River 头条（按重要性聚类，覆盖近 N 日）。"""
    return {f"科技头条(Techmeme·近{days}日)": techmeme_news(days=days, n=n)}


def research(days=2):
    """各龙头最新分析师评级分布，整理成条目。"""
    out = []
    for sym, name in LEADERS.items():
        d = recommendation(sym)
        if not d:
            continue
        s = (f"强买{d.get('strongBuy',0)} 买{d.get('buy',0)} 持有{d.get('hold',0)} "
             f"卖{d.get('sell',0)} 强卖{d.get('strongSell',0)}")
        tot = sum(d.get(k, 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
        out.append(_item(f"{name}({sym}) 评级 [{d.get('period','')}]  {s}",
                         "https://finnhub.io/", time.time(),
                         "Finnhub", extra=f"{tot}家", summary=s))
    return {"分析师评级趋势(Finnhub)": out}


def scan_query(query, days=2):
    """Finnhub 无全文搜索：在全球新闻里按关键词本地过滤。"""
    kw = query.lower()
    items = [it for it in market_news("general", n=100)
             if kw in (it["title"] + " " + it["summary"]).lower()]
    return {f"全球新闻含「{query}」": items}


def _md(groups, title):
    """结果转 Markdown（标题为可点击链接）。"""
    lines = [f"# {title}\n", f"_生成于 {time.strftime('%Y-%m-%d %H:%M')}_\n"]
    for src, items in groups.items():
        lines.append(f"\n## {src}  ({len(items)} 条)\n")
        for it in items:
            when = time.strftime("%m-%d %H:%M", time.localtime(it["ts"])) if it["ts"] else "--"
            tag = f" `{it['extra']}`" if it["extra"] else ""
            title_txt = it["title"].replace("|", "\\|") or "(无标题)"
            link = it["link"]
            lines.append(f"- {when}{tag} [{title_txt}]({link})" if link
                         else f"- {when}{tag} {title_txt}")
            if it.get("summary"):
                lines.append(f"  > {it['summary']}")
    return "\n".join(lines) + "\n"


def _print(groups):
    for src, items in groups.items():
        print(f"\n{'='*78}\n## {src}  ({len(items)} 条)")
        for it in items:
            when = time.strftime("%m-%d %H:%M", time.localtime(it["ts"])) if it["ts"] else "  --  "
            tag = f" [{it['extra']}]" if it["extra"] else ""
            print(f"  {when}{tag}  {it['title'][:90]}")
            if it.get("summary"):
                print(f"        · {it['summary'][:120]}")


def run(mode, days):
    """运行并返回 (总标题, groups字典)。"""
    if mode == "tech":
        return f"近 {days} 日最热科技资讯", hot_tech(days)
    if mode == "research":
        return f"近 {days} 日主流机构研报/评级动向", research(days)
    if mode == "all":
        g = hot_tech(days)
        g.update(research(days))
        return f"近 {days} 日最热科技 + 机构研报动向", g
    return f"关键词「{mode}」近 {days} 日全平台扫", scan_query(mode, days)


if __name__ == "__main__":
    args = sys.argv[1:]
    full = clean = False
    while args and args[-1] in ("full", "-f", "clean", "-c"):
        if args[-1] in ("full", "-f"):
            full = True
        else:
            clean = True
        args = args[:-1]
    days = 2
    if args and args[-1].isdigit():
        days = int(args[-1])
        args = args[:-1]
    mode = args[0] if args else "all"

    title, groups = run(mode, days)
    if clean:
        before = sum(len(v) for v in groups.values())
        filter_tech(groups)
        after = sum(len(v) for v in groups.values())
        print(f"... 科技相关性过滤: {before} → {after} 条 ...")
    if full:
        print("... 补抓重点摘要中（前几条/源）...")
        enrich(groups)
    print(f"\n######## {title} ########")
    _print(groups)

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news_scan.md")
    with open(out_path, "w") as fh:
        fh.write(_md(groups, title))
    print(f"\n→ 已存 Markdown: {out_path}")
