"""fundamentals.py — P/E, ROE, EPS, Market Cap จาก yfinance (ฟรี)"""
from __future__ import annotations

import logging
import math
from datetime import datetime

import yfinance as yf

from cache_utils import ttl_cache
from constants import CACHE_TTL_FUND

log = logging.getLogger("playground.fundamentals")


def _num(v):
    try:
        if v is None:
            return None
        x = float(v)
        if not math.isfinite(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _first(*vals):
    for v in vals:
        x = _num(v)
        if x is not None:
            return x
    return None


def _roe_pct(v):
    x = _num(v)
    if x is None:
        return None
    if abs(x) <= 2:
        x *= 100
    return round(x, 1)


def _fmt_mcap(v):
    x = _num(v)
    if x is None:
        return None
    abs_x = abs(x)
    if abs_x >= 1e12:
        return round(x / 1e12, 2)
    if abs_x >= 1e9:
        return round(x / 1e9, 2)
    if abs_x >= 1e6:
        return round(x / 1e6, 2)
    return round(x, 0)


def _mcap_unit(v):
    x = _num(v)
    if x is None:
        return None
    abs_x = abs(x)
    if abs_x >= 1e12:
        return "T"
    if abs_x >= 1e9:
        return "B"
    if abs_x >= 1e6:
        return "M"
    return ""


def _as_dict(obj):
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    try:
        return dict(obj)
    except Exception:
        out = {}
        for k in dir(obj):
            if k.startswith("_"):
                continue
            try:
                out[k] = getattr(obj, k)
            except Exception:
                pass
        return out


def _txt(v, limit: int = 280):
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


_INCOME_KEYS = [
    ("revenue", "รายได้", ("total revenue", "operating revenue", "revenue")),
    ("gross", "กำไรขั้นต้น", ("gross profit",)),
    ("operating", "กำไรจากการดำเนินงาน", ("operating income", "operating profit")),
    ("net_income", "กำไรสุทธิ", ("net income", "net income common stockholders")),
    ("eps", "EPS", ("diluted eps", "basic eps", "eps")),
]
_BALANCE_KEYS = [
    ("cash", "เงินสด", ("cash and cash equivalents", "cash cash equivalents and short term investments", "cash")),
    ("assets", "สินทรัพย์รวม", ("total assets",)),
    ("debt", "หนี้รวม", ("total debt", "long term debt")),
    ("equity", "ส่วนผู้ถือหุ้น", ("stockholders equity", "common stock equity", "total equity gross minority interest")),
]
_CASH_KEYS = [
    ("ocf", "กระแสจากกิจการ", ("operating cash flow", "cash flow from continuing operating activities")),
    ("capex", "ลงทุนทรัพย์สิน", ("capital expenditure",)),
    ("fcf", "กระแสเงินสดอิสระ", ("free cash flow",)),
]


def _pick_row(df, needles: tuple[str, ...]):
    for label in df.index:
        low = str(label).lower()
        if any(n == low or n in low for n in needles):
            return label
    return None


def _stmt_pack(df, spec, max_cols: int = 4) -> dict:
    if df is None or getattr(df, "empty", True):
        return {"columns": [], "rows": []}
    cols = list(df.columns)[:max_cols]
    columns = []
    for c in cols:
        try:
            columns.append(str(pd_to_date(c)))
        except Exception:
            columns.append(str(c)[:10])
    rows = []
    used = set()
    for key, label_th, needles in spec:
        loc = _pick_row(df, needles)
        if loc is None or loc in used:
            continue
        used.add(loc)
        vals = []
        for c in cols:
            try:
                vals.append(_num(df.loc[loc, c]))
            except Exception:
                vals.append(None)
        rows.append({"key": key, "label": label_th, "values": vals})
    return {"columns": columns, "rows": rows}


def pd_to_date(c):
    import pandas as pd
    ts = pd.Timestamp(c)
    if pd.isna(ts):
        return str(c)[:10]
    return ts.strftime("%Y-%m-%d")


@ttl_cache(CACHE_TTL_FUND)
def fetch_fundamentals(ticker: str) -> dict:
    raw = (ticker or "").strip()
    if not raw:
        return {"ok": False, "ticker": raw, "error": "no ticker"}
    try:
        t = yf.Ticker(raw)
        info = {}
        for getter in (
            lambda: t.get_info(),
            lambda: t.info,
        ):
            try:
                got = getter() or {}
                if got:
                    info.update(_as_dict(got))
            except Exception:
                continue
        fast = {}
        try:
            fast = _as_dict(getattr(t, "fast_info", None) or {})
        except Exception:
            fast = {}

        last_close = _first(
            info.get("currentPrice"),
            info.get("regularMarketPrice"),
            info.get("previousClose"),
            fast.get("last_price"),
            fast.get("lastPrice"),
        )
        if last_close is None:
            try:
                hist = t.history(period="5d", auto_adjust=True)
                if hist is not None and not hist.empty and "Close" in hist.columns:
                    last_close = _num(hist["Close"].dropna().iloc[-1])
            except Exception:
                pass

        eps = _first(info.get("trailingEps"), info.get("epsTrailingTwelveMonths"), info.get("forwardEps"))
        pe = _first(info.get("trailingPE"), info.get("priceEarningsRatio"))
        if pe is None and last_close and eps and eps != 0:
            pe = last_close / eps
        fwd_pe = _first(info.get("forwardPE"))
        roe = _roe_pct(_first(info.get("returnOnEquity"), info.get("returnOnEquityTTM")))
        if eps is None or roe is None:
            try:
                inc = t.income_stmt
                bs = t.balance_sheet
                ni = eq = None
                if inc is not None and not getattr(inc, "empty", True):
                    for label in inc.index:
                        low = str(label).lower()
                        if eps is None and ("diluted eps" in low or low == "eps"):
                            series = inc.loc[label].dropna()
                            if len(series):
                                eps = _num(series.iloc[0])
                        if ni is None and low in ("net income", "net income common stockholders"):
                            series = inc.loc[label].dropna()
                            if len(series):
                                ni = _num(series.iloc[0])
                    if eps is None and ni is not None:
                        shares = _first(info.get("sharesOutstanding"), fast.get("shares"))
                        if shares:
                            eps = ni / shares
                if roe is None and bs is not None and not getattr(bs, "empty", True):
                    for label in bs.index:
                        low = str(label).lower()
                        if "stockholder" in low and "equity" in low:
                            series = bs.loc[label].dropna()
                            if len(series):
                                eq = _num(series.iloc[0])
                            break
                    if ni is None and inc is not None and not getattr(inc, "empty", True):
                        for label in inc.index:
                            if str(label).lower() in ("net income", "net income common stockholders"):
                                series = inc.loc[label].dropna()
                                if len(series):
                                    ni = _num(series.iloc[0])
                                break
                    if ni is not None and eq:
                        roe = _roe_pct(ni / eq)
            except Exception:
                pass
        if pe is None and last_close and eps and eps != 0:
            pe = last_close / eps
        mcap = _first(
            info.get("marketCap"),
            fast.get("market_cap"),
            fast.get("marketCap"),
        )
        dy = _first(info.get("dividendYield"), info.get("trailingAnnualDividendYield"))
        if dy is not None and dy <= 1:
            dy = round(dy * 100, 2)
        elif dy is not None:
            dy = round(dy, 2)

        peg = _first(info.get("pegRatio"), info.get("trailingPegRatio"))
        ps = _first(info.get("priceToSalesTrailing12Months"), info.get("priceToSales"))
        pb = _first(info.get("priceToBook"))
        ev_ebitda = _first(info.get("enterpriseToEbitda"))
        ev = _first(info.get("enterpriseValue"))

        company = {
            "name": _txt(info.get("longName") or info.get("shortName") or fast.get("shortName"), 80),
            "sector": _txt(info.get("sector"), 48),
            "industry": _txt(info.get("industry"), 64),
            "country": _txt(info.get("country"), 32),
            "exchange": _txt(info.get("exchange") or info.get("fullExchangeName") or fast.get("exchange"), 24),
            "website": _txt(info.get("website"), 80),
            "employees": None if _first(info.get("fullTimeEmployees")) is None else int(_first(info.get("fullTimeEmployees"))),
            "summary": _txt(info.get("longBusinessSummary"), 420),
        }

        statements = {"income": {"columns": [], "rows": []}, "balance": {"columns": [], "rows": []}, "cash": {"columns": [], "rows": []}}
        try:
            statements["income"] = _stmt_pack(getattr(t, "income_stmt", None), _INCOME_KEYS)
        except Exception:
            pass
        try:
            statements["balance"] = _stmt_pack(getattr(t, "balance_sheet", None), _BALANCE_KEYS)
        except Exception:
            pass
        try:
            statements["cash"] = _stmt_pack(getattr(t, "cashflow", None), _CASH_KEYS)
        except Exception:
            pass

        ok = any(v is not None for v in (pe, roe, eps, mcap, last_close)) or any(company.values()) or any(statements[k]["rows"] for k in statements)
        return {
            "ok": ok,
            "ticker": raw,
            "pe": None if pe is None else round(pe, 1),
            "forward_pe": None if fwd_pe is None else round(fwd_pe, 1),
            "peg": None if peg is None else round(peg, 2),
            "ps": None if ps is None else round(ps, 2),
            "pb": None if pb is None else round(pb, 2),
            "ev_ebitda": None if ev_ebitda is None else round(ev_ebitda, 1),
            "enterprise_value": ev,
            "ev": _fmt_mcap(ev),
            "ev_unit": _mcap_unit(ev),
            "roe": roe,
            "eps": None if eps is None else round(eps, 2),
            "market_cap": mcap,
            "mcap": _fmt_mcap(mcap),
            "mcap_unit": _mcap_unit(mcap),
            "div_yield": dy,
            "last_close": None if last_close is None else round(last_close, 4 if last_close < 1 else 2),
            "currency": info.get("currency") or fast.get("currency"),
            "company": company,
            "statements": statements,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
    except Exception as e:
        log.debug("fundamentals failed %s: %s", raw, e)
        return {"ok": False, "ticker": raw, "error": str(e)}


def attach_fundamentals(rows: list[dict], limit_fetch: int = 80) -> list[dict]:
    if not rows:
        return rows
    seen = 0
    for r in rows:
        if seen >= limit_fetch:
            break
        t = r.get("full_ticker") or r.get("ticker") or r.get("symbol")
        if not t:
            continue
        pack = fetch_fundamentals(str(t))
        seen += 1
        if not pack.get("ok"):
            continue
        r["pe"] = pack.get("pe")
        r["forward_pe"] = pack.get("forward_pe")
        r["roe"] = pack.get("roe")
        r["eps"] = pack.get("eps")
        r["market_cap"] = pack.get("market_cap")
        r["mcap"] = pack.get("mcap")
        r["mcap_unit"] = pack.get("mcap_unit")
        r["div_yield"] = pack.get("div_yield")
    return rows
