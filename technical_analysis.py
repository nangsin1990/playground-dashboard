"""
technical_analysis.py — Per-ticker deep technical engine
Endpoints: /api/technicals?ticker=AAPL
           /api/sector_rs?ticker=AAPL&theme=Information+Technology
           /api/earnings?ticker=AAPL
           /api/dividends?ticker=AAPL
           /api/options_iv?ticker=AAPL

All heavy calcs live here — backend.py just calls fetch_* functions.
"""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional
import traceback

import numpy as np
import pandas as pd
import yfinance as yf

import data_engine as eng
from constants import CACHE_TTL_DATA, SECTOR_ETF_MAP, CORR_PERIOD_DAYS
from cache_utils import ttl_cache


# ── helpers ───────────────────────────────────────────────────────────────────
def _safe(v, decimals=2):
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, decimals)
    except Exception:
        return None


def _fetch_close(ticker: str, period: str = "18mo") -> Optional[pd.DataFrame]:
    try:
        df = yf.download(ticker, period=period, auto_adjust=True,
                         progress=False, timeout=25)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


# ── 1) Full Technical Snapshot ────────────────────────────────────────────────
@ttl_cache(CACHE_TTL_DATA)
def fetch_technicals(ticker: str) -> dict:
    try:
        df = _fetch_close(ticker)
        if df is None:
            return {"ok": False, "error": f"No data for {ticker}"}

        snap = eng.tech_snapshot(df)

        # Volume profile (20-day price distribution, 10 buckets)
        vp = _volume_profile(df, days=60, buckets=10)

        # Price vs VWAP history (last 20 bars)
        df2 = eng.add_technical_indicators(df)
        price_history = {
            "dates":  [d.strftime("%Y-%m-%d") for d in df2.index[-30:]],
            "close":  [_safe(v) for v in df2["Close"].tail(30)],
            "vwap":   [_safe(v) for v in df2["VWAP"].tail(30)],
            "bb_up":  [_safe(v) for v in df2["BB_UPPER"].tail(30)],
            "bb_mid": [_safe(v) for v in df2["BB_MID"].tail(30)],
            "bb_lo":  [_safe(v) for v in df2["BB_LOWER"].tail(30)],
        }

        return {
            "ok":            True,
            "ticker":        ticker.upper(),
            "updated":       datetime.now().strftime("%d/%m/%Y %H:%M"),
            **snap,
            "volume_profile": vp,
            "price_history":  price_history,
            "note":           "⏱ yfinance delayed ~15 min during market hours",
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "trace": traceback.format_exc()[-400:]}


# ── 2) Volume Profile ─────────────────────────────────────────────────────────
def _volume_profile(df: pd.DataFrame, days: int = 60, buckets: int = 10) -> dict:
    """Simplified volume profile: distribute volume into price buckets."""
    tail  = df.tail(days).copy()
    if len(tail) < 5:
        return {}
    lo, hi = float(tail["Low"].min()), float(tail["High"].max())
    if lo >= hi:
        return {}
    edges  = np.linspace(lo, hi, buckets + 1)
    labels = [round((edges[i] + edges[i+1]) / 2, 2) for i in range(buckets)]
    vols   = [0.0] * buckets
    for _, row in tail.iterrows():
        mid_price = (float(row["High"]) + float(row["Low"])) / 2
        idx = min(int((mid_price - lo) / (hi - lo) * buckets), buckets - 1)
        vols[idx] += float(row["Volume"])
    total = sum(vols) or 1
    pcts  = [round(v / total * 100, 1) for v in vols]
    return {
        "price_levels": labels,
        "volume_pct":   pcts,
        "poc_price":    labels[pcts.index(max(pcts))],   # Point of Control
        "price_lo":     round(lo, 2),
        "price_hi":     round(hi, 2),
        "days":         days,
    }


# ── 3) Relative Strength vs Benchmark + Sector ────────────────────────────────
@ttl_cache(CACHE_TTL_DATA)
def fetch_sector_rs(ticker: str, theme: str = "") -> dict:
    try:
        ticker_df = _fetch_close(ticker)
        if ticker_df is None:
            return {"ok": False, "error": f"No data for {ticker}"}

        stock_close = ticker_df["Close"].squeeze()

        # vs SPY
        spy_df    = _fetch_close("SPY")
        spy_close = spy_df["Close"].squeeze() if spy_df is not None else None

        rs_spy = eng.rs_vs_benchmark(stock_close, spy_close) if spy_close is not None else {}

        # vs Sector ETF
        sector_etf = SECTOR_ETF_MAP.get(theme, "SPY")
        rs_sector  = {}
        if sector_etf != "SPY":
            sec_df = _fetch_close(sector_etf)
            if sec_df is not None:
                rs_sector = eng.sector_relative_strength(stock_close, sec_df["Close"].squeeze())

        # Build 63-day rolling alpha chart
        alpha_chart: dict = {}
        if spy_close is not None:
            aligned   = pd.concat([stock_close, spy_close], axis=1, join="inner")
            aligned.columns = ["stock", "spy"]
            aligned   = aligned.tail(130)
            rel       = aligned["stock"] / aligned["spy"]
            rel_norm  = rel / rel.iloc[0] * 100   # index to 100
            alpha_chart = {
                "dates":    [d.strftime("%Y-%m-%d") for d in aligned.index[-63:]],
                "stock":    [_safe(v) for v in (aligned["stock"] / aligned["stock"].iloc[0] * 100).tail(63)],
                "spy":      [_safe(v) for v in (aligned["spy"]   / aligned["spy"].iloc[0]   * 100).tail(63)],
                "rs_ratio": [_safe(v) for v in rel_norm.tail(63)],
            }

        return {
            "ok":          True,
            "ticker":      ticker.upper(),
            "theme":       theme,
            "sector_etf":  sector_etf,
            "vs_spy":      rs_spy,
            "vs_sector":   rs_sector,
            "alpha_chart": alpha_chart,
            "updated":     datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 4) Earnings Surprise Tracker ─────────────────────────────────────────────
@ttl_cache(CACHE_TTL_DATA * 2)   # cache 30 min (earnings don't change intraday)
def fetch_earnings(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        hist = t.earnings_history   # EPS actual vs estimate
        cal  = t.earnings_dates     # upcoming + recent earnings dates

        rows = []
        if hist is not None and not hist.empty:
            hist_df = hist.reset_index()
            for _, row in hist_df.tail(8).iterrows():
                eps_est    = _safe(row.get("EPS Estimate"), 3)
                eps_actual = _safe(row.get("Reported EPS"), 3)
                surprise   = None
                if eps_est and eps_actual and eps_est != 0:
                    surprise = round((eps_actual - eps_est) / abs(eps_est) * 100, 1)
                rows.append({
                    "date":       str(row.get("Earnings Date", ""))[:10],
                    "eps_est":    eps_est,
                    "eps_actual": eps_actual,
                    "surprise":   surprise,
                    "beat":       (surprise > 0) if surprise is not None else None,
                })
            rows = rows[::-1]   # chronological

        # Next earnings date
        next_date = None
        if cal is not None and not cal.empty:
            future = cal[cal.index > pd.Timestamp.now(tz="UTC")]
            if not future.empty:
                next_date = str(future.index[0])[:10]

        # Beat rate
        beats     = [r for r in rows if r["beat"] is True]
        beat_rate = round(len(beats) / len(rows) * 100, 0) if rows else None

        return {
            "ok":        True,
            "ticker":    ticker.upper(),
            "history":   rows,
            "next_date": next_date,
            "beat_rate": beat_rate,
            "updated":   datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 5) Dividend + Split History ───────────────────────────────────────────────
@ttl_cache(CACHE_TTL_DATA * 2)
def fetch_dividends(ticker: str) -> dict:
    try:
        t       = yf.Ticker(ticker)
        actions = t.actions   # combined dividends + splits

        divs   = []
        splits = []

        if actions is not None and not actions.empty:
            if "Dividends" in actions.columns:
                div_df = actions[actions["Dividends"] > 0]["Dividends"].tail(12)
                for dt, v in div_df.items():
                    divs.append({"date": str(dt)[:10], "amount": _safe(v, 4)})
                divs = divs[::-1]

            if "Stock Splits" in actions.columns:
                spl_df = actions[actions["Stock Splits"] > 0]["Stock Splits"].tail(5)
                for dt, v in spl_df.items():
                    splits.append({"date": str(dt)[:10], "ratio": _safe(v, 2)})
                splits = splits[::-1]

        # Annualised dividend yield from info
        info       = t.fast_info
        div_yield  = _safe(getattr(info, "last_volume", None))   # fallback
        try:
            info2     = t.info
            div_yield = _safe(info2.get("dividendYield"), 4)
            div_rate  = _safe(info2.get("dividendRate"), 4)
            ex_div    = str(info2.get("exDividendDate", ""))[:10] if info2.get("exDividendDate") else None
        except Exception:
            div_rate = None
            ex_div   = None

        return {
            "ok":         True,
            "ticker":     ticker.upper(),
            "dividends":  divs,
            "splits":     splits,
            "div_yield":  div_yield,
            "div_rate":   div_rate,
            "ex_div":     ex_div,
            "updated":    datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 6) Options Implied Volatility ─────────────────────────────────────────────
@ttl_cache(CACHE_TTL_DATA)
def fetch_options_iv(ticker: str) -> dict:
    try:
        t     = yf.Ticker(ticker)
        exps  = t.options   # list of expiry dates

        if not exps:
            return {"ok": False, "error": "No options data available", "ticker": ticker}

        # Get nearest + next expiry
        results = []
        for exp in exps[:3]:
            try:
                chain = t.option_chain(exp)
                calls = chain.calls
                puts  = chain.puts

                # ATM straddle: find strike closest to current price
                info      = t.fast_info
                cur_price = float(getattr(info, "last_price", 0) or 0)

                if cur_price > 0 and not calls.empty:
                    atm_idx = (calls["strike"] - cur_price).abs().idxmin()
                    atm_call = calls.loc[atm_idx]
                    call_iv  = _safe(atm_call.get("impliedVolatility"), 4)
                else:
                    call_iv = None

                if cur_price > 0 and not puts.empty:
                    atm_idx = (puts["strike"] - cur_price).abs().idxmin()
                    atm_put  = puts.loc[atm_idx]
                    put_iv   = _safe(atm_put.get("impliedVolatility"), 4)
                else:
                    put_iv = None

                avg_iv = _safe(((call_iv or 0) + (put_iv or 0)) / 2) if call_iv and put_iv else (call_iv or put_iv)

                # Expected move = IV * price * sqrt(DTE/365)
                from datetime import date
                dte        = (pd.Timestamp(exp).date() - date.today()).days
                exp_move   = None
                if avg_iv and cur_price and dte > 0:
                    exp_move = round(cur_price * avg_iv * (dte / 365) ** 0.5, 2)

                results.append({
                    "expiry":    exp,
                    "dte":       dte,
                    "call_iv":   call_iv,
                    "put_iv":    put_iv,
                    "avg_iv":    avg_iv,
                    "exp_move":  exp_move,
                    "exp_move_pct": round(exp_move / cur_price * 100, 1) if exp_move and cur_price else None,
                })
            except Exception:
                continue

        if not results:
            return {"ok": False, "error": "Could not parse options chain", "ticker": ticker}

        # Term structure: IV per expiry
        term = [{"expiry": r["expiry"], "dte": r["dte"], "iv": r["avg_iv"]} for r in results if r.get("avg_iv")]

        return {
            "ok":            True,
            "ticker":        ticker.upper(),
            "expirations":   results,
            "term_structure":term,
            "nearest_iv":    results[0].get("avg_iv") if results else None,
            "nearest_exp_move": results[0].get("exp_move") if results else None,
            "nearest_exp_move_pct": results[0].get("exp_move_pct") if results else None,
            "updated":       datetime.now().strftime("%d/%m/%Y %H:%M"),
            "note":          "IV from ATM straddle. Expected move = IV × Price × √(DTE/365)",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
