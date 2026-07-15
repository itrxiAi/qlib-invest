"""
sec.py — SEC EDGAR 免费数据封装（海外龙头一手资料）。
能力：ticker→CIK、拉某公司最新 10-K/10-Q、抽正文文本、全文检索。
SEC 要求请求头带 User-Agent（含联系邮箱），否则 403；限速 ≤10 req/s。
文档：https://www.sec.gov/os/accessing-edgar-data
"""
import re
import time
import json

import requests

# SEC 要求声明身份；可改成你自己的邮箱
UA = "qlib-invest research tool contact@example.com"
HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}

_last = [0.0]
_MIN_INTERVAL = 0.2  # ≤10 req/s


def _get(url, params=None, timeout=30):
    wait = _MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    try:
        return requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    finally:
        _last[0] = time.time()


_TICKER_MAP = None


def ticker_to_cik(ticker):
    """ticker(如 NVDA) → 10位零填充 CIK。"""
    global _TICKER_MAP
    if _TICKER_MAP is None:
        r = _get("https://www.sec.gov/files/company_tickers.json")
        data = r.json()
        _TICKER_MAP = {v["ticker"].upper(): str(v["cik_str"]).zfill(10)
                       for v in data.values()}
    return _TICKER_MAP.get(ticker.upper())


def company_filings(cik, forms=("10-K", "20-F", "10-Q"), limit=10):
    """拉某公司近期申报列表。返回 [{form, date, accession, primary_doc, url}]"""
    r = _get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = r.json().get("filings", {}).get("recent", {})
    out = []
    for form, date, acc, doc in zip(
        recent.get("form", []), recent.get("filingDate", []),
        recent.get("accessionNumber", []), recent.get("primaryDocument", [])):
        if form in forms:
            acc_nodash = acc.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{doc}"
            out.append({"form": form, "date": date, "accession": acc,
                        "primary_doc": doc, "url": url})
            if len(out) >= limit:
                break
    return out


def filing_text(url, max_chars=400000):
    """下载申报文件(HTML)并抽成纯文本。"""
    r = _get(url)
    html = r.text
    html = re.sub(r"(?is)<script.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&#160;|&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text[:max_chars]


def fulltext_search(query, forms="10-K", date_from=None, date_to=None, size=10):
    """EDGAR 全文检索。query 用引号可精确匹配短语。
    返回 [{cik, name, form, date, accession, url}]"""
    params = {"q": query, "forms": forms}
    if date_from:
        params["dateRange"] = "custom"
        params["startdt"] = date_from
        params["enddt"] = date_to or time.strftime("%Y-%m-%d")
    r = _get("https://efts.sec.gov/LATEST/search-index", params=params)
    hits = (r.json().get("hits", {}) or {}).get("hits", [])
    out = []
    for h in hits[:size]:
        src = h.get("_source", {})
        cik = (src.get("cik") or [""])
        cik0 = str(cik[0]).zfill(10) if isinstance(cik, list) and cik else ""
        acc = (h.get("_id", "").split(":")[0])
        out.append({
            "name": (src.get("display_names") or [""])[0],
            "form": src.get("file_type") or src.get("root_form", ""),
            "date": src.get("file_date", ""),
            "accession": acc,
        })
    return out


if __name__ == "__main__":
    cik = ticker_to_cik("NVDA")
    print("NVDA CIK:", cik)
    fl = company_filings(cik, forms=("10-K",), limit=2)
    for f in fl:
        print(f"  {f['form']} {f['date']} -> {f['url']}")
    if fl:
        t = filing_text(fl[0]["url"])
        print("正文字数:", len(t))
        idx = t.lower().find("supply")
        print("supply 片段:", t[idx:idx + 200].replace("\n", " ") if idx > 0 else "未找到")
