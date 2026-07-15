"""
astock.py — a-stock-data SKILL 端点的可复用封装。
仅提取 /拆链 工作流所需端点：研报列表、概念板块归属、行情估值、财务营收。
所有东财请求走 em_get() 节流防封。数据源与字段定义见 SKILL.md。
"""
import time
import random
import urllib.request

import requests

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# ── 东财防封：全局节流 + 会话复用 ──────────────────────────────
EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
EM_MIN_INTERVAL = 1.2
_em_last_call = [0.0]


def em_get(url, params=None, headers=None, timeout=20, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session。"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


# ── 研报层：东财 reportapi ─────────────────────────────────────
REPORT_API = "https://reportapi.eastmoney.com/report/list"


def eastmoney_reports(code, begin="2024-01-01", max_pages=2):
    """拉取指定股票的研报列表。返回 [{title, publishDate, orgSName, infoCode, ...}]"""
    out = []
    for page in range(1, max_pages + 1):
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": begin, "endTime": "2030-01-01",
            "pageNo": str(page), "qType": "0", "code": code,
        }
        r = em_get(REPORT_API, params=params,
                   headers={"Referer": "https://data.eastmoney.com/"})
        rows = (r.json() or {}).get("data") or []
        if not rows:
            break
        out.extend(rows)
        if page >= (r.json().get("TotalPage", 1) or 1):
            break
    return out


def report_count(code, begin="2024-01-01"):
    """近期研报覆盖篇数（没人看维度的代理指标之一）。"""
    return len(eastmoney_reports(code, begin=begin, max_pages=2))


def industry_reports(keywords, begin="2025-01-01", qtypes=("1", "2"), max_pages=3):
    """按关键词拉行业研报(qType=1)/策略(qType=2)，过滤标题/行业名命中关键词。
    用于「先读懂产业链」——不依赖预设标的。"""
    if isinstance(keywords, str):
        keywords = [keywords]
    seen, out = set(), []
    for qt in qtypes:
        for page in range(1, max_pages + 1):
            params = {
                "pageSize": "100", "beginTime": begin, "endTime": "2030-01-01",
                "pageNo": str(page), "qType": qt,
                "industry": "*", "industryCode": "*", "rating": "*", "ratingChange": "*",
            }
            r = em_get(REPORT_API, params=params,
                       headers={"Referer": "https://data.eastmoney.com/"})
            rows = (r.json() or {}).get("data") or []
            if not rows:
                break
            for x in rows:
                blob = (x.get("title", "") + x.get("industryName", "") + x.get("orgSName", ""))
                if any(k in blob for k in keywords):
                    key = x.get("infoCode") or x.get("title")
                    if key not in seen:
                        seen.add(key)
                        out.append(x)
    return out


PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"


def download_pdf(record, target_dir="./reports/pdf"):
    """下载研报 PDF，返回路径或 None。"""
    import re
    from pathlib import Path
    info_code = record.get("infoCode", "")
    if not info_code:
        return None
    date = (record.get("publishDate") or "")[:10]
    org = record.get("orgSName") or "未知"
    title = re.sub(r'[\\/:*?"<>|]', "_", record.get("title", ""))[:60]
    target = Path(target_dir) / f"{date}_{org}_{title}.pdf"
    if target.exists():
        return str(target)
    r = em_get(PDF_TPL.format(info_code=info_code),
               headers={"Referer": "https://data.eastmoney.com/"}, timeout=60)
    if r.status_code == 200 and len(r.content) >= 1024:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(r.content)
        return str(target)
    return None


def pdf_text(path, max_pages=40):
    """抽取 PDF 文本（pymupdf）。"""
    import fitz
    doc = fitz.open(path)
    txt = "\n".join(doc[i].get_text() for i in range(min(max_pages, len(doc))))
    doc.close()
    return txt


# ── 概念板块成分股（客观标的池，禁止凭记忆硬列）─────────────
def list_concept_boards(keywords):
    """列出全市场概念板块中命中关键词的板块。返回 [{code(BK), name}]"""
    if isinstance(keywords, str):
        keywords = [keywords]
    params = {
        "pn": "1", "pz": "500", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fs": "m:90+t:3",  # t:3 概念板块
        "fields": "f12,f14",
    }
    r = em_get("https://push2.eastmoney.com/api/qt/clist/get", params=params,
               headers={"Referer": "https://quote.eastmoney.com/"})
    diff = (r.json().get("data") or {}).get("diff") or []
    items = diff.values() if isinstance(diff, dict) else diff
    return [{"code": it.get("f12"), "name": it.get("f14")}
            for it in items if any(k in (it.get("f14") or "") for k in keywords)]


def board_members(bk_code):
    """拉某概念板块(BK码)全部成分股。返回 [{code, name}]"""
    params = {
        "pn": "1", "pz": "500", "po": "1", "np": "1", "fltt": "2", "invt": "2",
        "fs": f"b:{bk_code}+f:!50", "fields": "f12,f14",
    }
    r = em_get("https://push2.eastmoney.com/api/qt/clist/get", params=params,
               headers={"Referer": "https://quote.eastmoney.com/"})
    diff = (r.json().get("data") or {}).get("diff") or []
    items = diff.values() if isinstance(diff, dict) else diff
    return [{"code": it.get("f12"), "name": it.get("f14")} for it in items]


# ── 信号层：东财 slist 概念板块归属 ───────────────────────────
def eastmoney_concept_blocks(code):
    """个股所属板块/概念（行业/概念/地域混合）+ 板块龙头股。"""
    market_code = 1 if code.startswith("6") else 0
    params = {
        "fltt": "2", "invt": "2",
        "secid": f"{market_code}.{code}",
        "spt": "3", "pi": "0", "pz": "200", "po": "1",
        "fields": "f12,f14,f3,f128",
    }
    try:
        r = em_get("https://push2.eastmoney.com/api/qt/slist/get", params=params,
                   headers={"Referer": "https://quote.eastmoney.com/"})
        diff = (r.json().get("data") or {}).get("diff") or {}
    except Exception as e:
        print(f"[WARN] 板块归属失败 {code}: {e}")
        return {"total": 0, "boards": [], "concept_tags": []}
    items = diff.values() if isinstance(diff, dict) else diff
    boards = [{"name": it.get("f14", ""), "code": it.get("f12", ""),
               "change_pct": it.get("f3", ""), "lead_stock": it.get("f128", "")}
              for it in items]
    return {"total": len(boards), "boards": boards,
            "concept_tags": [b["name"] for b in boards]}


# ── 行情层：腾讯财经 PE/PB/市值/换手率（不封IP）──────────────
def tencent_quote(codes):
    """批量行情。返回 {code: {name, price, pe_ttm, pb, mcap_yi, turnover_pct, ...}}"""
    prefixed = []
    for c in codes:
        if c.startswith(("6", "9")):
            prefixed.append(f"sh{c}")
        elif c.startswith("8"):
            prefixed.append(f"bj{c}")
        else:
            prefixed.append(f"sz{c}")
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
    result = {}
    for line in data.strip().split(";"):
        if "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        result[code] = {
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm": float(vals[39]) if vals[39] else 0,
            "mcap_yi": float(vals[44]) if vals[44] else 0,
            "pb": float(vals[46]) if vals[46] else 0,
        }
    return result


# ── 基础数据：mootdx 财务快照（营收，用于 CR3 集中度）─────────
def mootdx_income(code):
    """主营收入(元)。失败返回 None。"""
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std")
        fin = client.finance(symbol=code)
        if fin is None or len(fin) == 0:
            return None
        row = fin.iloc[0] if hasattr(fin, "iloc") else fin
        return float(row.get("income", 0)) or None
    except Exception as e:
        print(f"[WARN] 财务失败 {code}: {e}")
        return None


# ── 估值历史分位 + PEG + 业绩增速：东财 datacenter ─────────────
DATACENTER_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def valuation_percentile(code, years=5):
    """当前 PE_TTM / PB_MRQ + 在近 years 年「自身历史」中的分位(%)，含东财 PEG。
    分位越高 = 相对自身越贵（解决「绝对PE在A股偏高」的误判）。返回 dict 或 None。"""
    import datetime as _dt
    p = {"reportName": "RPT_VALUEANALYSIS_DET",
         "columns": "TRADE_DATE,PE_TTM,PB_MRQ,PS_TTM,PEG_CAR",
         "filter": f'(SECURITY_CODE="{code}")',
         "sortColumns": "TRADE_DATE", "sortTypes": "-1",
         "pageSize": "3000", "source": "WEB", "client": "WEB"}
    r = em_get(DATACENTER_API, params=p,
               headers={"Referer": "https://data.eastmoney.com/"})
    data = ((r.json() or {}).get("result") or {}).get("data") or []
    if not data:
        return None
    cutoff = (_dt.datetime.now() - _dt.timedelta(days=int(years * 365))).strftime("%Y-%m-%d")

    def _pct(field):
        cur = data[0].get(field)
        win = [x[field] for x in data
               if x.get(field) not in (None, "") and x[field] > 0
               and (x.get("TRADE_DATE") or "")[:10] >= cutoff]
        if not win or cur in (None, "") or cur <= 0:
            return cur, None
        rank = sum(1 for v in win if v <= cur) / len(win) * 100
        return cur, rank

    pe, pe_pct = _pct("PE_TTM")
    pb, pb_pct = _pct("PB_MRQ")
    return {"pe": pe, "pe_pct": pe_pct, "pb": pb, "pb_pct": pb_pct,
            "peg": data[0].get("PEG_CAR"), "as_of": (data[0].get("TRADE_DATE") or "")[:10],
            "hist_days": len(data)}


def growth(code):
    """最新定期报告的营收/归母净利同比(%)。返回 dict 或 None。"""
    p = {"reportName": "RPT_LICO_FN_CPD",
         "columns": "REPORTDATE,TOTAL_OPERATE_INCOME,YSTZ,PARENT_NETPROFIT,SJLTZ",
         "filter": f'(SECURITY_CODE="{code}")',
         "sortColumns": "REPORTDATE", "sortTypes": "-1",
         "pageSize": "1", "source": "WEB", "client": "WEB"}
    r = em_get(DATACENTER_API, params=p,
               headers={"Referer": "https://data.eastmoney.com/"})
    data = ((r.json() or {}).get("result") or {}).get("data") or []
    if not data:
        return None
    d = data[0]
    return {"report": (d.get("REPORTDATE") or "")[:10],
            "rev_yoy": d.get("YSTZ"), "profit_yoy": d.get("SJLTZ")}


# ── 行情序列：腾讯 K 线（前复权）+ 区间收益（alpha/beta 归因用）──
# 东财 push2his 在本网络间歇性 TLS/502 不可用，改用腾讯 ifzq 接口（稳定）。
TX_KLINE_API = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _tx_symbol(code):
    """code -> 腾讯前缀符号。6/9开头=sh，其余(0/3)=sz。指数同此规则。"""
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def kline(code, start="2026-01-01", end="2050-01-01", count=400, fq="qfq"):
    """前复权日线。返回 [{date, close}]（时间升序）。失败返回 []。"""
    sym = _tx_symbol(code)
    params = {"param": f"{sym},day,{start},{end},{count},{fq}"}
    rows = []
    for attempt in range(4):
        try:
            r = em_get(TX_KLINE_API, params=params,
                       headers={"Referer": "http://gu.qq.com/"})
            node = ((r.json() or {}).get("data") or {}).get(sym) or {}
            rows = node.get(f"{fq}day") or node.get("day") or []
            if rows:
                break
        except Exception:
            time.sleep(0.8 * (attempt + 1))
    out = []
    for row in rows:
        try:
            out.append({"date": row[0], "close": float(row[2])})
        except (IndexError, ValueError, TypeError):
            continue
    return out


def return_since(code, since_date):
    """从 since_date(含, 取首个>=该日的交易日)收盘到最新收盘的涨幅(%)。
    返回 {base_date, base, last_date, last, ret_pct} 或 None。"""
    ks = kline(code, start=since_date)
    if not ks:
        return None
    base = next((k for k in ks if k["date"] >= since_date), None)
    if not base:
        return None
    last = ks[-1]
    if not base["close"]:
        return None
    return {"base_date": base["date"], "base": base["close"],
            "last_date": last["date"], "last": last["close"],
            "ret_pct": (last["close"] / base["close"] - 1) * 100}


if __name__ == "__main__":
    print("研报数(中芯国际):", report_count("688981"))
    q = tencent_quote(["688981", "688012"])
    for c, v in q.items():
        print(f"  {c} {v['name']} PE={v['pe_ttm']} PB={v['pb']} 换手={v['turnover_pct']}% 市值={v['mcap_yi']}亿")
