"""market_snapshot.py — 拉取关键市场数据生成快照，归档到 news_raw/。

数据源：syncer API（与 qlib-prod 共用）
覆盖标的：
  - Crypto: BTC/ETH/SOL（1h bars，最近 24h 涨跌）
  - Macro: UST10Y/UST2Y/TIPS10Y/VIX/DXY（daily bars，最近变动）

输出：runs/news_raw/YYYY-MM-DD_market.md
被 _filter_raw_for_llm 自动复制到 news_filtered/，供 news-sync skill 读取。
"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).parent
RAW_DIR = ROOT / "runs" / "news_raw"

API_URL = os.getenv("SYNCER_API_URL", "http://100.125.221.59:8000")
API_KEY = os.getenv("SYNCER_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY}
NO_PROXY = {"http": None, "https": None, "all": None}
TIMEOUT = 15

CST = timezone(timedelta(hours=8))

# Crypto: qlib symbol -> (binance symbol, display name)
CRYPTO_SYMBOLS = [
    ("BTC", "BTCUSDT", "BTC"),
    ("ETH", "ETHUSDT", "ETH"),
    ("SOL", "SOLUSDT", "SOL"),
    ("XAU", "XAUUSDT", "黄金"),
]

# Macro: (symbol, display name, unit, decimal places)
MACRO_SYMBOLS = [
    ("UST10Y", "美债10Y收益率", "%", 2),
    ("UST2Y",  "美债2Y收益率", "%", 2),
    ("TIPS10Y", "美债10Y实际利率", "%", 2),
    ("VIX",    "VIX 恐慌指数",   "", 2),
    ("DXY",    "美元指数",       "", 2),
]


def _get(url: str, params: dict) -> dict | list:
    resp = requests.get(url, params=params, headers=HEADERS,
                        timeout=TIMEOUT, proxies=NO_PROXY)
    resp.raise_for_status()
    return resp.json()


def _fetch_crypto_24h(bn_symbol: str) -> dict | None:
    """拉最近 25 根 1h bars，算 24h 涨跌幅。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=26)
    params = {
        "asset": "crypto", "symbol": bn_symbol, "freq": "h1",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    data = _get(f"{API_URL}/v1/bars", params)
    rows = data.get("bars", data) if isinstance(data, dict) else data
    if not rows or len(rows) < 2:
        return None
    df = rows[-25:]  # 最近 25 根
    first_close = float(df[0]["close"])
    last_close = float(df[-1]["close"])
    high = max(float(b["high"]) for b in df)
    low = min(float(b["low"]) for b in df)
    chg_pct = (last_close / first_close - 1) * 100
    return {
        "price": last_close,
        "chg_pct": chg_pct,
        "high": high,
        "low": low,
    }


def _fetch_macro_recent(symbol: str, days: int = 7) -> dict | None:
    """拉最近 N 天 daily bars，算变动。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days + 2)
    params = {
        "asset": "macro", "symbol": symbol, "freq": "d1",
        "start": start.strftime("%Y-%m-%dT00:00:00Z"),
        "end": end.strftime("%Y-%m-%dT23:59:59Z"),
    }
    data = _get(f"{API_URL}/v1/bars", params)
    rows = data.get("bars", data) if isinstance(data, dict) else data
    if not rows or len(rows) < 2:
        return None
    df = sorted(rows, key=lambda r: r["ts"])
    latest = float(df[-1]["close"])
    prev = float(df[-2]["close"])
    chg = latest - prev
    chg_pct = (latest / prev - 1) * 100 if prev != 0 else 0
    # 周变动（对比 5 个交易日前）
    week_ago_idx = max(0, len(df) - 6)
    week_ago = float(df[week_ago_idx]["close"])
    chg_week = latest - week_ago
    return {
        "latest": latest,
        "prev": prev,
        "chg": chg,
        "chg_pct": chg_pct,
        "chg_week": chg_week,
        "latest_date": df[-1]["ts"][:10],
    }


def run() -> str:
    """生成市场快照 markdown，归档到 news_raw/，返回文件路径。"""
    now_cst = datetime.now(CST)
    day = now_cst.strftime("%Y-%m-%d")
    when = now_cst.strftime("%H:%M")

    lines = [f"# 市场快照 — {day} {when}\n"]

    # ── Crypto ──
    lines.append("## 加密货币（24h）\n")
    for qlib_sym, bn_sym, name in CRYPTO_SYMBOLS:
        try:
            d = _fetch_crypto_24h(bn_sym)
            if d:
                arrow = "📈" if d["chg_pct"] >= 0 else "📉"
                lines.append(
                    f"- {name} ${d['price']:,.0f} {arrow} {d['chg_pct']:+.2f}% "
                    f"(24h 低 ${d['low']:,.0f} → 高 ${d['high']:,.0f})"
                )
            else:
                lines.append(f"- {name} 数据不可用")
        except Exception as e:
            lines.append(f"- {name} 拉取失败: {e}")

    # ── Macro ──
    lines.append("\n## 宏观指标（最近交易日）\n")
    for sym, name, unit, decimals in MACRO_SYMBOLS:
        try:
            d = _fetch_macro_recent(sym)
            if d:
                arrow = "↑" if d["chg"] >= 0 else "↓"
                week_arrow = "↑" if d["chg_week"] >= 0 else "↓"
                val_str = f"{d['latest']:.{decimals}f}{unit}"
                chg_str = f"{d['chg']:+.{decimals}f}{unit}"
                week_str = f"{d['chg_week']:+.{decimals}f}{unit}"
                lines.append(
                    f"- {name} {val_str} {arrow} 日变动 {chg_str}，周变动 {week_str} "
                    f"（截至 {d['latest_date']}）"
                )
            else:
                lines.append(f"- {name} 数据不可用")
        except Exception as e:
            lines.append(f"- {name} 拉取失败: {e}")

    # ── 利差 ──
    try:
        u10 = _fetch_macro_recent("UST10Y")
        u2 = _fetch_macro_recent("UST2Y")
        if u10 and u2:
            spread = u10["latest"] - u2["latest"]
            lines.append(f"\n## 利差\n")
            lines.append(f"- 10Y-2Y 利差 {spread:+.2f}% {'（倒挂）' if spread < 0 else ''}")
    except Exception:
        pass

    content = "\n".join(lines) + "\n"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{day}_market.md"
    path.write_text(content, encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    p = run()
    print(p)
    print()
    print(Path(p).read_text())
