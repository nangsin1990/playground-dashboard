# FILE: rotation_rrg.py

"""
Relative Rotation Graph (RRG) Engine
=====================================
v5: Refactored to align with central pipeline and simplify market handling.
  - Removes self-contained data fetching.
  - Relies on pipeline.fetch_universe for all data.
  - Correctly handles 'GLOBAL', 'US', and other market modes.
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
    CACHE_TTL_DATA, RRG_SMOOTHING, RRG_ROLL_MIN, RRG_TAIL_WEEKS, RRG_TAIL_STEP,
    RRG_CLAMP_LO, RRG_CLAMP_HI, RRG_ROC_SHIFT, RRG_MIN_HISTORY,
)
from universe import UNIVERSE, BENCHMARK, RRG_US_SECTOR_MAP, RRG_GLOBAL_UNIVERSE

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

@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core", market: str = "GLOBAL") -> dict:
    """
    [FIXED] Simplified RRG function relying on the main pipeline.
    Handles 'GLOBAL', 'US', 'TH', etc. based on market parameter.
    """
    # --- 1. Get all data from central pipeline ---
    active = pipeline.active_universe(mode)
    # Add all required tickers for all RRG modes to ensure they are fetched
    required_tickers = set(BENCHMARK.values())
    for group in [RRG_US_SECTOR_MAP, RRG_GLOBAL_UNIVERSE]:
        for item in group.values():
            if isinstance(item, str):
                required_tickers.add(item)
            elif isinstance(item, list):
                required_tickers.update(item)

    # Temporarily add these tickers to the active universe for fetching
    # Note: This is a simplified way. A better approach would be to have pipeline manage this.
    active['__TEMP_RRG__'] = list(required_tickers)

    combined, ticker_meta, _ = pipeline.fetch_universe(active)
    del active['__TEMP_RRG__'] # Clean up

    if not combined:
        return {"ok": False, "error": "No data from pipeline"}

    # --- 2. Determine Universe & Benchmark based on 'market' param ---
    benchmark_ticker = BENCHMARK.get(market, BENCHMARK.get("US", "SPY"))

    # Get benchmark data
    bench_close = combined.get(benchmark_ticker, {}).get("Close")
    if bench_close is None:
        try:
            bench_df = data_io.fetch_batch((benchmark_ticker,))
            bench_close = bench_df[benchmark_ticker]["Close"]
        except Exception:
             return {"ok": False, "error": f"Benchmark '{benchmark_ticker}' data could not be fetched."}
    if len(bench_close) < RRG_MIN_HISTORY:
        return {"ok": False, "error": f"Benchmark '{benchmark_ticker}' data is insufficient."}


    if market == 'GLOBAL':
        universe_map = {name: data['tickers'] for name, data in RRG_GLOBAL_UNIVERSE.items()}
        current_market_tickers = {t for tickers in universe_map.values() for t in tickers}
    else: # Default to US Sectors
        universe_map = {sector: [etf] for sector, etf in RRG_US_SECTOR_MAP.items()}
        current_market_tickers = {m[0] for m in UNIVERSE.get(market, UNIVERSE["US"]).values()}

    # --- 3. Compute Per-Market RS Rating ---
    # This is less critical for RRG groups but good to have
    market_tickers_for_rs = [t for t, m in ticker_meta.items() if m.get("market") == market]
    blended_rs = pd.Series({t: eng.blended_return(combined[t]["Close"]) for t in market_tickers_for_rs if t in combined})
    rs_ratings = eng.rs_rating_table(blended_rs) if not blended_rs.empty else pd.Series(dtype=int)

    # --- 4. Process RRG for each group ---
    rrg_rows = []
    for group_name, tickers in universe_map.items():
        # Get data for tickers in the current group
        group_data = {t: combined[t] for t in tickers if t in combined}
        if not group_data:
            continue

        # Create an average index for the group
        group_closes = [df["Close"] for df in group_data.values()]
        if not group_closes:
            continue

        group_idx = pd.concat(group_closes, axis=1).mean(axis=1).dropna()
        if len(group_idx) < RRG_MIN_HISTORY:
            continue

        common_idx = group_idx.index.intersection(bench_close.index)
        if len(common_idx) < RRG_MIN_HISTORY:
            continue

        ti, bi = group_idx.loc[common_idx], bench_close.loc[common_idx]

        try:
            rsr = _rs_ratio_series(ti, bi)
            rsm = _rs_momentum_series(rsr)
        except Exception:
            continue

        # Create tail data
        tail = []
        for w in range(RRG_TAIL_WEEKS, 0, -1):
            idx_pos = len(rsr) - w * RRG_TAIL_STEP
            if idx_pos < 0: continue
            r = float(rsr.iloc[idx_pos]) if pd.notna(rsr.iloc[idx_pos]) else 100.0
            m = float(rsm.iloc[idx_pos]) if pd.notna(rsm.iloc[idx_pos]) else 100.0
            tail.append([round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, r)), 2), round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, m)), 2)])

        curr_rsr = round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, float(rsr.iloc[-1]) if pd.notna(rsr.iloc[-1]) else 100.0)), 2)
        curr_rsm = round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, float(rsm.iloc[-1]) if pd.notna(rsm.iloc[-1]) else 100.0)), 2)
        tail.append([curr_rsr, curr_rsm])

        avg_rs = int(np.mean([rs_ratings.get(t, 0) for t in tickers if t in rs_ratings])) if not rs_ratings.empty else 0

        rrg_rows.append({
            "theme": group_name,
            "short": _short_name(group_name),
            "market": market,
            "rs_ratio": curr_rsr,
            "rs_momentum": curr_rsm,
            "quadrant": _quadrant(curr_rsr, curr_rsm),
            "tail": tail,
            "avg_rs": avg_rs,
            "count": len(tickers),
        })

    if not rrg_rows:
        return {"ok": False, "error": f"Could not generate RRG data for market '{market}'"}

    q_order = {"Leading": 0, "Improving": 1, "Weakening": 2, "Lagging": 3}
    rrg_rows.sort(key=lambda x: (q_order.get(x["quadrant"], 4), -x["rs_ratio"]))

    return {
        "ok": True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "benchmark": benchmark_ticker,
        "rrg": rrg_rows,
        "note": f"RRG computed for {market} view vs {benchmark_ticker}.",
    }
