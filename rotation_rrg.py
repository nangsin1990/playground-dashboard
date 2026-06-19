"""
Relative Rotation Graph (RRG) Engine
=====================================
คำนวณ RS Ratio และ RS Momentum สำหรับ Rotation Chart (RRG)

Methodology (Julius de Kempenaer / Bloomberg RRG):
  1. JdK RS-Ratio  = smoothed relative performance vs benchmark, normalised to 100
  2. JdK RS-Momentum = rate of change of RS-Ratio, normalised to 100
  3. Quadrant:
       Leading   = RS-Ratio > 100 AND RS-Momentum > 100
       Weakening = RS-Ratio > 100 AND RS-Momentum < 100
       Lagging   = RS-Ratio < 100 AND RS-Momentum < 100
       Improving = RS-Ratio < 100 AND RS-Momentum > 100

Benchmark choices per market:
  US / Global → SPY
  TH          → ^SET.BK (or first TH ticker as proxy)
  ETF view    → SPY

Tail = weekly RS-Ratio & RS-Momentum snapshots for last N weeks.
"""

from __future__ import annotations
from datetime import datetime

import numpy as np
import pandas as pd

import data_engine as eng
import pipeline
from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_DATA,
    RRG_SMOOTHING, RRG_ROLL_MIN, RRG_RRG_TAIL_WEEKS, RRG_TAIL_STEP,
    RRG_CLAMP_LO, RRG_CLAMP_HI, RRG_ROC_SHIFT, RRG_MIN_HISTORY,
)

from universe import UNIVERSE


BENCHMARK_US = "SPY"
BENCHMARK_TH = "^SET.BK"
  # EMA days for RS ratio


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rs_ratio_series(price: pd.Series, bench: pd.Series, span: int = RRG_SMOOTHING) -> pd.Series:
    """
    JdK RS-Ratio: smoothed (price/bench) normalised to 100-range.
    Raw relative = price / bench aligned by date.
    We return a 100-centred series: 100 = inline with benchmark.
    """
    aligned = pd.concat([price, bench], axis=1, join="inner")
    aligned.columns = ["p", "b"]
    rel = aligned["p"] / aligned["b"]
    smoothed = _ema(rel, span)
    # normalise: centre on rolling 52-week mean, scale so 1 std ≈ 5 points
    roll_mean = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).mean()
    roll_std = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).std().replace(0, np.nan)
    normalised = 100 + (smoothed - roll_mean) / roll_std * 5
    return normalised


def _rs_momentum_series(rs_ratio: pd.Series, span: int = RRG_SMOOTHING) -> pd.Series:
    """
    JdK RS-Momentum: rate-of-change of RS-Ratio, similarly normalised to 100.
    """
    roc = rs_ratio - rs_ratio.shift(RRG_ROC_SHIFT)
    smoothed = _ema(roc, span)
    roll_mean = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).mean()
    roll_std = smoothed.rolling(52, min_periods=RRG_ROLL_MIN).std().replace(0, np.nan)
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
    """Short label for RRG dots."""
    replacements = {
        "Information Technology": "IT",
        "Consumer Discretionary": "Con.Disc",
        "Consumer Staples": "Con.Sta",
        "Communication Services": "Comm",
        "Health Care": "Health",
        "ETF - Broad Market": "Broad ETF",
        "ETF - Sector Equity": "Sector ETF",
        "ETF - Fixed Income": "Bond ETF",
        "ETF - Commodity": "Cmd ETF",
        "ETF - International/EM": "EM ETF",
        "ETF - Leveraged/Inverse": "Lev ETF",
        "ETF - Volatility": "Vol ETF",
        "Semiconductors": "Semi",
        "Electronic Technology": "ElecTech",
    }
    return replacements.get(theme, theme.split(" ")[0])


@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core") -> dict:
    """
    Compute RRG data for all theme groups.
    Returns list of {theme, rs_ratio, rs_momentum, quadrant, tail, avg_rs, market}.
    """
    active = pipeline.active_universe(mode)

    # We need SPY (US benchmark) and ^SET.BK (TH benchmark) in combined
    bench_tickers = [BENCHMARK_US]
    try:
        import data_io
        bench_raw = data_io.fetch_batch(tuple(bench_tickers))
    except Exception:
        bench_raw = {}

    combined, ticker_meta, _ = pipeline.fetch_universe(active)

    if not combined:
        return {"ok": False, "error": "No data"}

    # RS Ratings for avg_rs
    blended = pd.Series({t: eng.blended_return(d["Close"]) for t, d in combined.items()})
    rs_now = eng.rs_rating_table(blended)

    # Benchmark close series
    bench_close_us: pd.Series | None = None
    if BENCHMARK_US in bench_raw and bench_raw[BENCHMARK_US] is not None:
        bench_close_us = bench_raw[BENCHMARK_US]["Close"]
    elif BENCHMARK_US in combined:
        bench_close_us = combined[BENCHMARK_US]["Close"]
    else:
        # Fallback: use equal-weight US universe as proxy benchmark
        us_closes = [combined[t]["Close"] for t in combined if ticker_meta.get(t, {}).get("market") == "US"]
        if us_closes:
            bench_close_us = pd.concat(us_closes, axis=1).mean(axis=1)

    # Group tickers by theme
    theme_map: dict[str, list[str]] = {}
    for t, meta in ticker_meta.items():
        th = meta.get("theme", "Unknown")
        theme_map.setdefault(th, []).append(t)

    rrg_rows = []
      # store last 16 weekly points for the tail

    for theme, tickers in theme_map.items():
        if len(tickers) < 1:
            continue

        # Equal-weight theme price index (daily)
        closes = []
        market = ticker_meta[tickers[0]].get("market", "US")
        for t in tickers:
            if t in combined:
                closes.append(combined[t]["Close"])
        if not closes:
            continue

        theme_idx = pd.concat(closes, axis=1).mean(axis=1).dropna()
        if len(theme_idx) < 30:
            continue

        # Choose benchmark
        bench = bench_close_us
        if bench is None or len(bench) < 30:
            # No benchmark → skip
            continue

        # Align
        common_idx = theme_idx.index.intersection(bench.index)
        if len(common_idx) < 30:
            continue
        ti = theme_idx.loc[common_idx]
        bi = bench.loc[common_idx]

        # Compute RS-Ratio and RS-Momentum series
        try:
            rsr = _rs_ratio_series(ti, bi)
            rsm = _rs_momentum_series(rsr)
        except Exception:
            continue

        # Weekly tail snapshots (sample every 5 trading days from end)
        tail = []
        for w in range(RRG_TAIL_WEEKS, 0, -1):
            idx_pos = len(rsr) - w * 5
            if idx_pos < 0:
                continue
            r = float(rsr.iloc[idx_pos]) if not np.isnan(rsr.iloc[idx_pos]) else 100.0
            m = float(rsm.iloc[idx_pos]) if not np.isnan(rsm.iloc[idx_pos]) else 100.0
            # clamp to 90-115 range for display
            tail.append([round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, r)), 2), round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, m)), 2)])

        # Current values
        curr_rsr = float(rsr.iloc[-1]) if not np.isnan(rsr.iloc[-1]) else 100.0
        curr_rsm = float(rsm.iloc[-1]) if not np.isnan(rsm.iloc[-1]) else 100.0
        curr_rsr = round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, curr_rsr)), 2)
        curr_rsm = round(max(RRG_CLAMP_LO, min(RRG_CLAMP_HI, curr_rsm)), 2)
        tail.append([curr_rsr, curr_rsm])

        avg_rs = int(np.mean([int(rs_now.get(t, 0)) for t in tickers if t in rs_now]))

        rrg_rows.append({
            "theme": theme,
            "short": _short_name(theme),
            "market": market,
            "rs_ratio": curr_rsr,
            "rs_momentum": curr_rsm,
            "quadrant": _quadrant(curr_rsr, curr_rsm),
            "tail": tail,
            "avg_rs": avg_rs,
            "count": len(tickers),
        })

    # Sort: Leading first, then Improving, Weakening, Lagging
    q_order = {"Leading": 0, "Improving": 1, "Weakening": 2, "Lagging": 3}
    rrg_rows.sort(key=lambda x: (q_order.get(x["quadrant"], 4), -x["rs_ratio"]))

    return {
        "ok": True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "universe_loaded": len(combined),
        "benchmark": BENCHMARK_US,
        "rrg": rrg_rows,
    }
