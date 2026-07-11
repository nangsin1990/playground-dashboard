# FILENAME: rotation_rrg.py

"""
Relative Rotation Graph (RRG) Engine
=====================================
v4: Refactored to support 'market' parameter and 'GLOBAL' mode.
  - fetch_rotation(mode, market)
  - 'GLOBAL' mode uses RRG_GLOBAL_UNIVERSE and VT benchmark.
  - Default mode handles US Sectors / ETFs.
"""

from __future__ import annotations
from datetime import datetime

import numpy as np
import pandas as pd

import data_engine as eng
import data_io
import pipeline
from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_DATA,
    RRG_SMOOTHING, RRG_ROLL_MIN, RRG_TAIL_WEEKS, RRG_TAIL_STEP,
    RRG_CLAMP_LO, RRG_CLAMP_HI, RRG_ROC_SHIFT, RRG_MIN_HISTORY,
)

# Immutable User Data: คัดลอกมา 100%
from universe import BENCHMARK, RRG_US_SECTORS, RRG_US_THEMES, RRG_GLOBAL_UNIVERSE

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _rs_ratio_series(price: pd.Series, bench: pd.Series, span: int = RRG_SMOOTHING) -> pd.Series:
    aligned = pd.concat([price, bench], axis=1, join="inner")
    aligned.columns = ["p", "b"]
    rel = aligned["p"] / aligned["b"]
    smoothed = _ema(rel, span)
    roll_mean = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).mean()
    roll_std = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).std().replace(0, np.nan)
    normalised = 100 + (smoothed - roll_mean) / roll_std * 5
    return normalised

def _rs_momentum_series(rs_ratio: pd.Series, span: int = RRG_SMOOTHING) -> pd.Series:
    roc = rs_ratio - rs_ratio.shift(RRG_ROC_SHIFT)
    smoothed = _ema(roc, span)
    roll_mean = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).mean()
    roll_std = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).std().replace(0, np.nan)
    normalised = 100 + (smoothed - roll_mean) / roll_std * 5
    return normalised

def _quadrant(rs_ratio: float, rs_momentum: float) -> str:
    if rs_ratio >= 100 and rs_momentum >= 100: return "Leading"
    if rs_ratio >= 100 and rs_momentum < 100: return "Weakening"
    if rs_ratio < 100 and rs_momentum < 100: return "Lagging"
    return "Improving"

def _short_name(theme: str) -> str:
    # ... (no changes to this helper function)
    replacements = {
        "Information Technology": "IT", "Consumer Discretionary": "Con.Disc",
        "Consumer Staples": "Con.Sta", "Communication Services": "Comm",
        "Health Care": "Health", "ETF - Broad Market": "Broad ETF",
        "ETF - Sector Equity": "Sector ETF", "ETF - Fixed Income": "Bond ETF",
        "ETF - Commodity": "Cmd ETF", "ETF - International/EM": "EM ETF",
        "ETF - Leveraged/Inverse":"Lev ETF", "ETF - Volatility": "Vol ETF",
        "Semiconductors": "Semi", "Electronic Technology": "ElecTech",
    }
    return replacements.get(theme, theme.split(" ")[0])

def _process_group(group_name: str, tickers: list[str], combined_data: dict, bench_close: pd.Series, rs_ratings: pd.Series) -> dict | None:
    """Helper to compute RRG for a single group of tickers."""
    closes = [combined_data[t]["Close"] for t in tickers if t in combined_data]
    if not closes: return None

    group_idx = pd.concat(closes, axis=1).mean(axis=1).dropna()
    if len(group_idx) < RRG_MIN_HISTORY: return None

    common_idx = group_idx.index.intersection(bench_close.index)
    if len(common_idx) < RRG_MIN_HISTORY: return None

    ti, bi = group_idx.loc[common_idx], bench_close.loc[common_idx]

    try:
        rsr = _rs_ratio_series(ti, bi)
        rsm = _rs_momentum_series(rsr)
    except Exception:
        return None

    tail = []
    for w in range(RRG_TAIL_WEEKS, 0, -1):
        idx_pos = len(rsr) - w * RRG_TAIL_STEP
        if idx_pos < 0: continue
        r = float(rsr.iloc[idx_pos]) if not np.isnan(rsr.iloc[idx_pos]) else 100.0
        m = float(rsm.iloc[idx_pos]) if not np.isnan(rsm.iloc[idx_pos]) else 100.0
        tail.append([round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, r)), 2), round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, m)), 2)])

    curr_rsr = round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, float(rsr.iloc[-1]) if not np.isnan(rsr.iloc[-1]) else 100.0)), 2)
    curr_rsm = round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, float(rsm.iloc[-1]) if not np.isnan(rsm.iloc[-1]) else 100.0)), 2)
    tail.append([curr_rsr, curr_rsm])

    avg_rs = int(np.mean([rs_ratings.get(t, 0) for t in tickers if t in rs_ratings])) if not rs_ratings.empty else 0

    return {
        "theme": group_name,
        "short": _short_name(group_name),
        "market": "GLOBAL" if group_name in RRG_GLOBAL_UNIVERSE else "US",
        "rs_ratio": curr_rsr,
        "rs_momentum": curr_rsm,
        "quadrant": _quadrant(curr_rsr, curr_rsm),
        "tail": tail,
        "avg_rs": avg_rs,
        "count": len(tickers),
    }

@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core", market: str | None = None) -> dict:
    """
    Refactored main function to handle different markets/views.
    - market='GLOBAL': Use RRG_GLOBAL_UNIVERSE vs 'VT'
    - market='US_SECTORS': Use RRG_US_SECTORS vs 'SPY'
    - market='US_THEMES': Use RRG_US_THEMES vs 'SPY'
    """
    # ==================== GLOBAL MODE ====================
    if market == 'GLOBAL':
        universe_map = RRG_GLOBAL_UNIVERSE
        benchmark_ticker = BENCHMARK['GLOBAL']
        all_tickers = list(universe_map.keys()) + [benchmark_ticker]

        raw_data = data_io.fetch_batch(tuple(all_tickers))

        bench_close = raw_data.get(benchmark_ticker, {}).get("Close")
        if bench_close is None or len(bench_close) < RRG_MIN_HISTORY:
            return {"ok": False, "error": f"Benchmark '{benchmark_ticker}' data is insufficient."}

        # For Global mode, we don't need per-market RS rating
        rs_ratings = pd.Series(dtype=int)

        rrg_rows = []
        for name, tickers in universe_map.items():
            row_data = _process_group(name, tickers, raw_data, bench_close, rs_ratings)
            if row_data:
                rrg_rows.append(row_data)

    # ==================== DEFAULT US MODE ====================
    else:
        active = pipeline.active_universe(mode)
        combined, ticker_meta, _ = pipeline.fetch_universe(active)
        if not combined:
            return {"ok": False, "error": "No data for US universe"}

        benchmark_ticker = BENCHMARK['US']
        bench_close = combined.get(benchmark_ticker, {}).get("Close")
        if bench_close is None or len(bench_close) < RRG_MIN_HISTORY:
            return {"ok": False, "error": f"Benchmark '{benchmark_ticker}' data is insufficient."}

        # Group tickers by theme (US only for RRG)
        theme_map: dict[str, list[str]] = {}
        for t, meta in ticker_meta.items():
            if meta.get("market") != "US": continue
            th = meta.get("theme", "Unknown")
            theme_map.setdefault(th, []).append(t)

        # RS Ratings for US market
        us_tickers = [t for t, m in ticker_meta.items() if m.get("market") == "US"]
        blended_us = pd.Series({t: eng.blended_return(combined[t]["Close"]) for t in us_tickers if t in combined})
        rs_us = eng.rs_rating_table(blended_us) if not blended_us.empty else pd.Series(dtype=int)

        universe_to_process = RRG_US_SECTORS if market == 'US_SECTORS' else RRG_US_THEMES

        rrg_rows = []
        for name, tickers in universe_to_process.items():
            row_data = _process_group(name, tickers, combined, bench_close, rs_us)
            if row_data:
                rrg_rows.append(row_data)

    if not rrg_rows:
        return {"ok": False, "error": f"Could not generate RRG data for market '{market}'"}

    q_order = {"Leading": 0, "Improving": 1, "Weakening": 2, "Lagging": 3}
    rrg_rows.sort(key=lambda x: (q_order.get(x["quadrant"], 4), -x["rs_ratio"]))

    return {
        "ok": True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "benchmark": benchmark_ticker,
        "rrg": rrg_rows,
        "note": f"RRG computed for {market or 'default'} view.",
    }
