"""
Relative Rotation Graph (RRG) Engine
=====================================
v3 fixes:
  - RRG_RRG_TAIL_WEEKS typo → RRG_TAIL_WEEKS
  - EMA span 14 (smoothing reduced quadrant noise)
  - ROC shift 14 (matches smoothing)
  - Removed TH benchmark (US-only focus)
  - RS Rating scoped per market (no cross-market RS)

Methodology (Julius de Kempenaer / Bloomberg RRG):
  1. JdK RS-Ratio  = smoothed relative performance vs benchmark, normalised to 100
  2. JdK RS-Momentum = rate of change of RS-Ratio, normalised to 100
  3. Quadrant:
       Leading   = RS-Ratio > 100 AND RS-Momentum > 100
       Weakening = RS-Ratio > 100 AND RS-Momentum < 100
       Lagging   = RS-Ratio < 100 AND RS-Momentum < 100
       Improving = RS-Ratio < 100 AND RS-Momentum > 100
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

from universe import UNIVERSE

BENCHMARK_US = "SPY"


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rs_ratio_series(price: pd.Series, bench: pd.Series, span: int = RRG_SMOOTHING) -> pd.Series:
    """
    JdK RS-Ratio: smoothed (price/bench) normalised to 100-range.
    Raw relative = price / bench aligned by date.
    Returns 100-centred series: 100 = inline with benchmark.
    """
    aligned = pd.concat([price, bench], axis=1, join="inner")
    aligned.columns = ["p", "b"]
    rel      = aligned["p"] / aligned["b"]
    smoothed = _ema(rel, span)
    roll_mean = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).mean()
    roll_std  = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).std().replace(0, np.nan)
    normalised = 100 + (smoothed - roll_mean) / roll_std * 5
    return normalised


def _rs_momentum_series(rs_ratio: pd.Series, span: int = RRG_SMOOTHING) -> pd.Series:
    """
    JdK RS-Momentum: rate-of-change of RS-Ratio, similarly normalised to 100.
    """
    roc      = rs_ratio - rs_ratio.shift(RRG_ROC_SHIFT)
    smoothed = _ema(roc, span)
    roll_mean = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).mean()
    roll_std  = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).std().replace(0, np.nan)
    normalised = 100 + (smoothed - roll_mean) / roll_std * 5
    return normalised


def _quadrant(rs_ratio: float, rs_momentum: float) -> str:
    if rs_ratio >= 100 and rs_momentum >= 100:
        return "Leading"
    if rs_ratio >= 100 and rs_momentum < 100:
        return "Weakening"
    if rs_ratio < 100 and rs_momentum < 100:
        return "Lagging"
    return "Improving"


def _short_name(theme: str) -> str:
    replacements = {
        "Information Technology": "IT",
        "Consumer Discretionary": "Con.Disc",
        "Consumer Staples":       "Con.Sta",
        "Communication Services": "Comm",
        "Health Care":            "Health",
        "ETF - Broad Market":     "Broad ETF",
        "ETF - Sector Equity":    "Sector ETF",
        "ETF - Fixed Income":     "Bond ETF",
        "ETF - Commodity":        "Cmd ETF",
        "ETF - International/EM": "EM ETF",
        "ETF - Leveraged/Inverse":"Lev ETF",
        "ETF - Volatility":       "Vol ETF",
        "Semiconductors":         "Semi",
        "Electronic Technology":  "ElecTech",
    }
    return replacements.get(theme, theme.split(" ")[0])


@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core") -> dict:
    """
    Compute RRG data for US theme groups vs SPY benchmark.
    RS is computed per-market so cross-currency comparison is avoided.
    """
    active = pipeline.active_universe(mode)

    # Fetch SPY benchmark
    try:
        bench_raw = data_io.fetch_batch((BENCHMARK_US,))
    except Exception:
        bench_raw = {}

    combined, ticker_meta, _ = pipeline.fetch_universe(active)

    if not combined:
        return {"ok": False, "error": "No data"}

    # RS Ratings scoped to US market only (no cross-market mixing)
    us_tickers = [t for t, m in ticker_meta.items() if m.get("market") == "US"]
    if us_tickers:
        blended_us = pd.Series({t: eng.blended_return(combined[t]["Close"])
                                 for t in us_tickers if t in combined})
        rs_us = eng.rs_rating_table(blended_us)
    else:
        rs_us = pd.Series(dtype=int)

    # Benchmark close
    bench_close: pd.Series | None = None
    if BENCHMARK_US in bench_raw and bench_raw[BENCHMARK_US] is not None:
        bench_close = bench_raw[BENCHMARK_US]["Close"]
    elif BENCHMARK_US in combined:
        bench_close = combined[BENCHMARK_US]["Close"]
    else:
        us_closes = [combined[t]["Close"] for t in us_tickers if t in combined]
        if us_closes:
            bench_close = pd.concat(us_closes, axis=1).mean(axis=1)

    # Group tickers by theme (US only for RRG)
    theme_map: dict[str, list[str]] = {}
    for t, meta in ticker_meta.items():
        if meta.get("market") != "US":
            continue
        th = meta.get("theme", "Unknown")
        theme_map.setdefault(th, []).append(t)

    rrg_rows = []

    for theme, tickers in theme_map.items():
        closes = [combined[t]["Close"] for t in tickers if t in combined]
        if not closes:
            continue

        theme_idx = pd.concat(closes, axis=1).mean(axis=1).dropna()
        if len(theme_idx) < RRG_MIN_HISTORY:
            continue

        if bench_close is None or len(bench_close) < RRG_MIN_HISTORY:
            continue

        common_idx = theme_idx.index.intersection(bench_close.index)
        if len(common_idx) < RRG_MIN_HISTORY:
            continue
        ti = theme_idx.loc[common_idx]
        bi = bench_close.loc[common_idx]

        try:
            rsr = _rs_ratio_series(ti, bi)
            rsm = _rs_momentum_series(rsr)
        except Exception:
            continue

        # Weekly tail snapshots
        tail = []
        for w in range(RRG_TAIL_WEEKS, 0, -1):
            idx_pos = len(rsr) - w * RRG_TAIL_STEP
            if idx_pos < 0:
                continue
            r = float(rsr.iloc[idx_pos]) if not np.isnan(rsr.iloc[idx_pos]) else 100.0
            m = float(rsm.iloc[idx_pos]) if not np.isnan(rsm.iloc[idx_pos]) else 100.0
            tail.append([
                round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, r)), 2),
                round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, m)), 2),
            ])

        curr_rsr = float(rsr.iloc[-1]) if not np.isnan(rsr.iloc[-1]) else 100.0
        curr_rsm = float(rsm.iloc[-1]) if not np.isnan(rsm.iloc[-1]) else 100.0
        curr_rsr = round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, curr_rsr)), 2)
        curr_rsm = round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, curr_rsm)), 2)
        tail.append([curr_rsr, curr_rsm])

        avg_rs = int(np.mean([int(rs_us.get(t, 0)) for t in tickers if t in rs_us])) if len(rs_us) else 0

        rrg_rows.append({
            "theme":       theme,
            "short":       _short_name(theme),
            "market":      "US",
            "rs_ratio":    curr_rsr,
            "rs_momentum": curr_rsm,
            "quadrant":    _quadrant(curr_rsr, curr_rsm),
            "tail":        tail,
            "avg_rs":      avg_rs,
            "count":       len(tickers),
        })

    q_order = {"Leading": 0, "Improving": 1, "Weakening": 2, "Lagging": 3}
    rrg_rows.sort(key=lambda x: (q_order.get(x["quadrant"], 4), -x["rs_ratio"]))

    return {
        "ok":              True,
        "updated":         datetime.now().strftime("%d/%m/%Y %H:%M"),
        "universe_loaded": len(combined),
        "benchmark":       BENCHMARK_US,
        "rrg":             rrg_rows,
        "note":            "RRG computed for US themes vs SPY. EMA smoothing reduces quadrant noise.",
    }
