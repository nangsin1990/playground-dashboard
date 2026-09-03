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
        if eps is None or roe is None:
            try:
                inc = t.income_stmt
                bs = t.balance_sheet
                ni = eq = None
                if inc is not None and not getattr(inc, "empty", True):
                    for label in inc.index:
                        low = str(label).lower()
                        if eps is None and ("diluted eps" in low or low == "eps"):
                            eps = _num(inc.loc[label].dropna().iloc[0])
                        if ni is None and low in ("net income", "net income common stockholders"):
                            ni = _num(inc.loc[label].dropna().iloc[0])
                    if eps is None and ni is not None:
                        shares = _first(info.get("sharesOutstanding"), fast.get("shares"))
                        if shares:
                            eps = ni / shares
                if roe is None and bs is not None and not getattr(bs, "empty", True):
                    for label in bs.index:
                        low = str(label).lower()
                        if "stockholder" in low and "equity" in low:
                            eq = _num(bs.loc[label].dropna().iloc[0])
                            break
                    if ni is None and inc is not None and not getattr(inc, "empty", True):
                        for label in inc.index:
                            if str(label).lower() in ("net income", "net income common stockholders"):
                                ni = _num(inc.loc[label].dropna().iloc[0])
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

        ok = any(v is not None for v in (pe, roe, eps, mcap, last_close))
        return {
            "ok": ok,
            "ticker": raw,
            "pe": None if pe is None else round(pe, 1),
            "forward_pe": None if fwd_pe is None else round(fwd_pe, 1),
            "roe": roe,
            "eps": None if eps is None else round(eps, 2),
            "market_cap": mcap,
            "mcap": _fmt_mcap(mcap),
            "mcap_unit": _mcap_unit(mcap),
            "div_yield": dy,
            "last_close": None if last_close is None else round(last_close, 4 if last_close < 1 else 2),
            "currency": info.get("currency") or fast.get("currency"),
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
