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
from datetime import datetime, timedelta, timezone
from typing import Optional
import traceback

import numpy as np
import pandas as pd
import yfinance as yf

import data_engine as eng
from constants import CACHE_TTL_DATA, SECTOR_ETF_MAP
from cache_utils import ttl_cache


def _resolve_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return t
    try:
        from universe import UNIVERSE
        for names in UNIVERSE.values():
            if t in names:
                return t
        hits = []
        for names in UNIVERSE.values():
            for k in names:
                if k.upper() == t or k.split(".")[0].upper() == t:
                    hits.append(k)
        if hits:
            return hits[0]
    except Exception:
        pass
    return t


def _theme_of(ticker: str) -> str:
    try:
        from universe import UNIVERSE
        for names in UNIVERSE.values():
            if ticker in names:
                return names[ticker][1]
            short = ticker.split(".")[0].upper()
            for k, meta in names.items():
                if k.upper() == ticker or k.split(".")[0].upper() == short:
                    return meta[1]
    except Exception:
        pass
    return ""


# ── helpers ───────────────────────────────────────────────────────────────────
def _safe(v, decimals=2):
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, decimals)
    except Exception:
        return None


def _normalize_ohlcv(df: pd.DataFrame, min_bars: int = 30) -> Optional[pd.DataFrame]:
    if df is None or getattr(df, "empty", True):
        return None
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        names = [str(x).lower() for x in out.columns.get_level_values(0)]
        if "close" in names:
            out.columns = out.columns.get_level_values(0)
        else:
            try:
                out = out.droplevel(-1, axis=1)
            except Exception:
                out.columns = out.columns.get_level_values(0)
    if getattr(out.columns, "duplicated", lambda: [])().any():
        out = out.loc[:, ~out.columns.duplicated()]
    lower_map = {str(c).strip().lower().replace(" ", ""): c for c in out.columns}
    want = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume", "adjclose": "Close"}
    norm = pd.DataFrame(index=out.index)
    for key, dest in want.items():
        src = lower_map.get(key)
        if src is not None and dest not in norm.columns:
            norm[dest] = pd.to_numeric(out[src], errors="coerce")
    need = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in norm.columns for c in need):
        return None
    norm = norm[need].dropna(how="all")
    if len(norm) < min_bars:
        return None
    return norm


def fetch_ohlcv(ticker: str, period: str = "18mo", interval: str = "1d") -> Optional[pd.DataFrame]:
    """Public OHLCV loader used by Stock Deep Dive and Gold Command Center."""
    resolved = _resolve_ticker(ticker)
    if interval == "1d":
        try:
            import data_io
            hit = data_io.lookup_ticker(resolved) or data_io.lookup_ticker(ticker)
            if hit is not None:
                norm = _normalize_ohlcv(hit)
                if norm is not None:
                    return norm
        except Exception:
            pass
    try:
        df = yf.download(
            resolved,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            timeout=25,
            threads=False,
            group_by="column",
        )
        return _normalize_ohlcv(df)
    except Exception:
        return None


def _fetch_close(ticker: str, period: str = "18mo") -> Optional[pd.DataFrame]:
    return fetch_ohlcv(ticker, period=period, interval="1d")


# ── 1) Full Technical Snapshot ────────────────────────────────────────────────
@ttl_cache(CACHE_TTL_DATA)
def fetch_technicals(ticker: str) -> dict:
    try:
        ticker = _resolve_ticker(ticker)
        df = _fetch_close(ticker)
        if df is None:
            return {"ok": False, "error": f"No data for {ticker}"}

        df = eng.add_technical_indicators(df)
        try:
            snap = eng.tech_snapshot(df) or {}
        except Exception as e:
            snap = {"price": None, "error_snap": str(e)}
        try:
            vp = _volume_profile(df, days=60, buckets=10)
        except Exception:
            vp = {}
        try:
            for col in ("Open","High","Low","Close","Volume"):
                if col in df.columns and hasattr(df[col], "columns"):
                    df[col] = df[col].iloc[:, 0]
        except Exception:
            pass
        hourly = None
        try:
            raw_h = yf.download(ticker, period="60d", interval="60m", auto_adjust=True, progress=False, timeout=20)
            hourly = _normalize_ohlcv(raw_h, min_bars=8)
        except Exception:
            hourly = None
        tail = df.tail(30)
        price_history = {
            "dates":  [d.strftime("%Y-%m-%d") for d in tail.index],
            "open":   [_safe(v) for v in tail["Open"]],
            "high":   [_safe(v) for v in tail["High"]],
            "low":    [_safe(v) for v in tail["Low"]],
            "close":  [_safe(v) for v in tail["Close"]],
            "volume": [_safe(v) for v in tail["Volume"]],
            "vwap":   [_safe(v) for v in tail["VWAP"]],
            "bb_up":  [_safe(v) for v in tail["BB_UPPER"]],
            "bb_mid": [_safe(v) for v in tail["BB_MID"]],
            "bb_lo":  [_safe(v) for v in tail["BB_LOWER"]],
        }

        return {
            "ok":            True,
            "ticker":        ticker.upper(),
            "updated":       datetime.now().strftime("%d/%m/%Y %H:%M"),
            **snap,
            "volume_profile": vp,
            "price_history":  price_history,
            "pivots":         eng.pivot_pack(df, hourly=hourly),
            "note":           "⏱ yfinance delayed ~15 min during market hours",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── 2) Volume Profile ─────────────────────────────────────────────────────────
def volume_profile(df: pd.DataFrame, days: int = 60, buckets: int = 10) -> dict:
    return _volume_profile(df, days=days, buckets=buckets)


def _volume_profile(df: pd.DataFrame, days: int = 60, buckets: int = 10) -> dict:
    """Simplified volume profile: distribute volume into price buckets."""
    tail  = df.tail(days).copy()
    if len(tail) < 5:
        return {}
    lo, hi = float(tail["Low"].min()), float(tail["High"].max())
    if not np.isfinite(lo) or not np.isfinite(hi) or lo >= hi:
        return {}
    edges  = np.linspace(lo, hi, buckets + 1)
    labels = [round((edges[i] + edges[i+1]) / 2, 2) for i in range(buckets)]
    vols   = [0.0] * buckets
    for _, row in tail.iterrows():
        try:
            hi_px = float(row["High"])
            lo_px = float(row["Low"])
            vol = float(row["Volume"])
        except (TypeError, ValueError):
            continue
        if not np.isfinite(hi_px) or not np.isfinite(lo_px) or not np.isfinite(vol):
            continue
        mid_price = (hi_px + lo_px) / 2
        if not np.isfinite(mid_price):
            continue
        idx = min(int((mid_price - lo) / (hi - lo) * buckets), buckets - 1)
        vols[idx] += vol
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
        ticker = _resolve_ticker(ticker)
        ticker_df = _fetch_close(ticker)
        if ticker_df is None:
            return {"ok": False, "error": f"No data for {ticker}"}

        stock_close = ticker_df["Close"].squeeze()
        theme = (theme or "").strip() or _theme_of(ticker)

        spy_df    = _fetch_close("SPY")
        spy_close = spy_df["Close"].squeeze() if spy_df is not None else None

        rs_spy = eng.rs_vs_benchmark(stock_close, spy_close) if spy_close is not None else {}

        sector_etf = SECTOR_ETF_MAP.get(theme, "SPY")
        rs_sector  = {}
        if sector_etf != "SPY":
            sec_df = _fetch_close(sector_etf)
            if sec_df is not None:
                rs_sector = eng.sector_relative_strength(stock_close, sec_df["Close"].squeeze())

        alpha_chart: dict = {}
        if spy_close is not None:
            aligned = pd.concat([stock_close, spy_close], axis=1, join="inner").dropna()
            aligned.columns = ["stock", "spy"]
            aligned = aligned.tail(63)
            if len(aligned) >= 2 and float(aligned["stock"].iloc[0]) and float(aligned["spy"].iloc[0]):
                stock_ix = aligned["stock"] / float(aligned["stock"].iloc[0]) * 100
                spy_ix = aligned["spy"] / float(aligned["spy"].iloc[0]) * 100
                rel = aligned["stock"] / aligned["spy"]
                rel_ix = rel / float(rel.iloc[0]) * 100
                alpha_chart = {
                    "dates":    [pd.Timestamp(d).strftime("%Y-%m-%d") for d in aligned.index],
                    "stock":    [_safe(v) for v in stock_ix],
                    "spy":      [_safe(v) for v in spy_ix],
                    "rs_ratio": [_safe(v) for v in rel_ix],
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
        ticker = _resolve_ticker(ticker)
        t = yf.Ticker(ticker)
        hist = t.earnings_history
        cal  = t.earnings_dates

        rows = []
        if hist is not None and not getattr(hist, "empty", True):
            hist_df = hist.reset_index()
            for _, row in hist_df.tail(8).iterrows():
                eps_est    = _safe(row.get("EPS Estimate"), 3)
                eps_actual = _safe(row.get("Reported EPS"), 3)
                surprise   = None
                if eps_est is not None and eps_actual is not None and eps_est != 0:
                    surprise = round((eps_actual - eps_est) / abs(eps_est) * 100, 1)
                dt = row.get("Earnings Date", "")
                if hasattr(dt, "strftime"):
                    dt = dt.strftime("%Y-%m-%d")
                rows.append({
                    "date":       str(dt)[:10],
                    "eps_est":    eps_est,
                    "eps_actual": eps_actual,
                    "surprise":   surprise,
                    "beat":       (surprise > 0) if surprise is not None else None,
                })
            rows = rows[::-1]

        next_date = None
        if cal is not None and not getattr(cal, "empty", True):
            idx = pd.DatetimeIndex(pd.to_datetime(cal.index, utc=True, errors="coerce"))
            now = pd.Timestamp.now(tz="UTC")
            future = cal.loc[idx > now] if len(idx) == len(cal) else cal
            if future is not None and not future.empty:
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
        ticker = _resolve_ticker(ticker)
        t       = yf.Ticker(ticker)
        actions = t.actions

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
        try:
            info2     = t.info
            div_yield = _safe(info2.get("dividendYield"), 4)
            if div_yield is not None and div_yield > 1:
                div_yield = round(div_yield / 100.0, 4)
            div_rate  = _safe(info2.get("dividendRate"), 4)
            raw_ex = info2.get("exDividendDate")
            ex_div = None
            if raw_ex:
                try:
                    if isinstance(raw_ex, (int, float)):
                        ex_div = datetime.fromtimestamp(int(raw_ex), tz=timezone.utc).strftime("%Y-%m-%d")
                    else:
                        ex_div = str(pd.Timestamp(raw_ex))[:10]
                except Exception:
                    ex_div = str(raw_ex)[:10]
        except Exception:
            div_yield = None
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
        ticker = _resolve_ticker(ticker)
        t     = yf.Ticker(ticker)
        exps  = t.options

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

                if call_iv is not None and put_iv is not None:
                    avg_iv = _safe((call_iv + put_iv) / 2, 4)
                else:
                    avg_iv = call_iv if call_iv is not None else put_iv

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
