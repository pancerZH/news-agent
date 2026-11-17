"""Key-free hard-data panel: US Treasury daily yield curve XML + yfinance prices and valuations."""

import xml.etree.ElementTree as ET
from datetime import date

from .collect import fetch

TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
                "?data=daily_treasury_yield_curve&field_tdr_date_value_month={month}")
TENORS = {"3M": "BC_3MONTH", "2Y": "BC_2YEAR", "10Y": "BC_10YEAR", "30Y": "BC_30YEAR"}
NS_D = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
NS_M = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"


def parse_treasury(data: bytes) -> list[dict]:
    rows = []
    for props in ET.fromstring(data).iter(f"{NS_M}properties"):
        stamp = props.findtext(f"{NS_D}NEW_DATE")
        if not stamp:
            continue
        row = {"date": stamp[:10]}
        for tenor, tag in TENORS.items():
            value = props.findtext(f"{NS_D}{tag}")
            row[tenor] = float(value) if value else None
        rows.append(row)
    return sorted(rows, key=lambda r: r["date"])


def treasury_curve(timeout: float = 20, today: date | None = None) -> dict:
    today = today or date.today()
    prev_month = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    rows = []
    for d in (prev_month, today):
        rows += parse_treasury(fetch(TREASURY_URL.format(month=d.strftime("%Y%m")), timeout))
    if not rows:
        raise ValueError("no Treasury yield rows")
    latest, prev = rows[-1], rows[-2] if len(rows) > 1 else rows[-1]

    def spread(row, a, b):
        return round(row[a] - row[b], 2) if row[a] is not None and row[b] is not None else None

    return {"date": latest["date"], "prev_date": prev["date"],
            "yields": {t: latest[t] for t in TENORS}, "prev": {t: prev[t] for t in TENORS},
            "spreads": {"10Y-2Y": spread(latest, "10Y", "2Y"), "10Y-3M": spread(latest, "10Y", "3M")}}


def _pct(closes, n: int) -> float | None:
    return round((closes.iloc[-1] / closes.iloc[-1 - n] - 1) * 100, 2) if len(closes) > n else None


def quotes(indices: list[str], tickers: list[str]) -> dict:
    import yfinance as yf

    symbols = indices + tickers
    close = yf.download(symbols, period="1y", interval="1d", auto_adjust=True, progress=False)["Close"]
    out = {"indices": [], "watchlist": []}
    for sym in symbols:
        if sym not in close:
            continue
        c = close[sym].dropna()
        if c.empty:
            continue
        row = {"symbol": sym, "price": round(float(c.iloc[-1]), 2), "date": str(c.index[-1].date()),
               "chg_1d": _pct(c, 1), "chg_5d": _pct(c, 5), "chg_1m": _pct(c, 21)}
        if sym in indices:
            out["indices"].append(row)
            continue
        row["drawdown_52w"] = round((c.iloc[-1] / c.max() - 1) * 100, 2)
        try:
            info = yf.Ticker(sym).info
        except Exception:
            info = {}
        row |= {"trailing_pe": info.get("trailingPE"), "forward_pe": info.get("forwardPE"),
                "revenue_growth": info.get("revenueGrowth")}
        out["watchlist"].append(row)
    return out


def snapshot(indices: list[str], tickers: list[str], timeout: float = 20) -> dict:
    snap = {}
    try:
        snap["treasury"] = treasury_curve(timeout)
    except Exception as e:
        snap["treasury"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        snap |= quotes(indices, tickers)
    except Exception as e:
        snap["quotes_error"] = f"{type(e).__name__}: {e}"
    return snap


def _fmt(v, suffix: str = "", digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}{suffix}"


def render(snap: dict | None) -> str:
    if not snap:
        return "_硬数据面板未启用或未生成。_\n"
    lines = []
    t = snap.get("treasury") or {}
    if "error" in t:
        lines.append(f"_国债曲线获取失败: {t['error']}_\n")
    elif t:
        lines += [f"**美国国债收益率曲线**（财政部，{t['date']}，对比 {t['prev_date']}）\n",
                  "| 期限 | 收益率 | 日变动 (bp) |", "| :--- | ---: | ---: |"]
        for tenor, y in t["yields"].items():
            p = t["prev"].get(tenor)
            bp = None if y is None or p is None else round((y - p) * 100)
            lines.append(f"| {tenor} | {_fmt(y, '%')} | {_fmt(bp, digits=0)} |")
        lines.append(f"\n利差：10Y-2Y = {_fmt(t['spreads']['10Y-2Y'], '%')}，10Y-3M = {_fmt(t['spreads']['10Y-3M'], '%')}\n")
    if snap.get("quotes_error"):
        lines.append(f"_行情获取失败: {snap['quotes_error']}_\n")
    if snap.get("indices"):
        lines += ["**指数 / 美元 / 波动率**\n", "| 标的 | 收盘 | 1D | 5D | 1M |", "| :--- | ---: | ---: | ---: | ---: |"]
        lines += [f"| {r['symbol']} | {_fmt(r['price'])} | {_fmt(r['chg_1d'], '%')} | {_fmt(r['chg_5d'], '%')} "
                  f"| {_fmt(r['chg_1m'], '%')} |" for r in snap["indices"]]
        lines.append("")
    if snap.get("watchlist"):
        lines += ["**关注池估值快照**\n", "| 标的 | 现价 | 1D | 1M | 距 52 周高点 | Trailing PE | Forward PE | 营收增速 |",
                  "| :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for r in snap["watchlist"]:
            growth = None if r.get("revenue_growth") is None else r["revenue_growth"] * 100
            lines.append(f"| {r['symbol']} | {_fmt(r['price'])} | {_fmt(r['chg_1d'], '%')} | {_fmt(r['chg_1m'], '%')} "
                         f"| {_fmt(r['drawdown_52w'], '%')} | {_fmt(r.get('trailing_pe'), digits=1)} "
                         f"| {_fmt(r.get('forward_pe'), digits=1)} | {_fmt(growth, '%', 1)} |")
        lines.append("")
    return "\n".join(lines) + "\n"
