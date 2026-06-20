"""
Leadership Board Engine
=======================
คำนวณ "ผู้นำตลาด" จากข้อมูล OHLCV ล้วนๆ ไม่ต้องการ API เพิ่ม

Methodology (based on William O'Neil / Minervini / IBD):
─────────────────────────────────────────────────────────
1. RS Rating (1-99)         — relative strength vs entire universe
2. Trend Template (0-4)     — Minervini's 4-condition checklist
3. Accumulation Score       — up-volume vs down-volume ratio (20d)
4. Proximity to 52W High    — leaders stay near highs
5. Base Tightness           — tight consolidation = coiling energy
6. Volume Surge             — institutional buying signal
7. Momentum Acceleration    — dRS 7D (RS improving)
8. Confluence Signals       — VDU + PPBP + BGU overlapping

Composite Leadership Score (0-100) weights these factors.

Leaderboard tabs:
  - Overall Leaders         (highest Leadership Score)
  - Top RS                  (pure relative strength)
  - Top Momentum            (fastest RS acceleration)
  - Near Breakout           (within 5% of 52W high, tight base)
  - Institutional Buying    (volume + accumulation signals)
  - Top Volume Expansion    (unusual volume surge)
  - Trend Template Passed   (all 4 Minervini conditions met)
"""

from __future__ import annotations
from datetime import datetime
import numpy as np
import pandas as pd

import data_engine as eng
from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_DATA,
    LB_TREND_LOOKBACK, LB_ACCUM_LOOKBACK, LB_TIGHTNESS_WEEKS,
    LB_UD_RATIO_LOOKBACK, LB_VOL_WINDOW,
    LB_BREAKOUT_PROX, LB_ACCUM_MIN, LB_UD_MIN, LB_VOL_MIN,
    LB_TOP_N, TRADING_DAYS_YEAR,
)

from universe import UNIVERSE




# ─────────────────────────────────────────────────────────────────────────────
# Per-ticker metrics (computed from enriched OHLCV DataFrame)
# ─────────────────────────────────────────────────────────────────────────────

def _trend_template(df: pd.DataFrame) -> dict:
    """
    Minervini Trend Template — 4 strict conditions:
    1. Price > SMA50
    2. Price > SMA150
    3. Price > SMA200
    4. SMA200 slope is positive (rising over last 20 bars)
    Score 0-4 (4 = perfect uptrend, qualifies for IBD-style leader)
    """
    last  = df.iloc[-1]
    p     = float(last["Close"])
    s50   = float(last["SMA50"])
    s150  = float(last.get("SMA150", last["SMA50"]))  # fallback
    s200  = float(last["SMA200"])

    sma200_series = df["SMA200"].dropna()
    slope = 0.0
    if len(sma200_series) >= 21:
        slope = float(sma200_series.iloc[-1] - sma200_series.iloc[-LB_TREND_LOOKBACK]) / float(sma200_series.iloc[-LB_TREND_LOOKBACK]) * 100

    c1 = p > s50
    c2 = p > s150
    c3 = p > s200
    c4 = slope > 0
    score = sum([c1, c2, c3, c4])
    return {
        "score": score,
        "c1_above_50":  c1,
        "c2_above_150": c2,
        "c3_above_200": c3,
        "c4_slope_up":  c4,
        "sma200_slope": round(slope, 2),
    }


def _accumulation_score(df: pd.DataFrame, lookback: int = LB_ACCUM_LOOKBACK) -> float:
    """
    Accumulation / Distribution Score (-1 to +1).
    Up days with above-avg volume = accumulation (+1 each)
    Down days with above-avg volume = distribution (-1 each)
    Normalised by number of days.
    """
    tail = df.tail(lookback + 1).copy()
    if len(tail) < 5:
        return 0.0
    tail["pct_chg"] = tail["Close"].pct_change()
    avg_vol = float(tail["Volume"].mean())
    score = 0.0
    for _, row in tail.iterrows():
        if pd.isna(row["pct_chg"]):
            continue
        high_vol = row["Volume"] > avg_vol * 0.8
        if row["pct_chg"] > 0 and high_vol:
            score += 1
        elif row["pct_chg"] < 0 and high_vol:
            score -= 1
    return round(score / lookback, 3)


def _base_tightness(df: pd.DataFrame, weeks: int = LB_TIGHTNESS_WEEKS) -> float:
    """
    Base tightness: % range (high-low)/midpoint over last `weeks` weeks.
    Lower = tighter = better (VCP-style contraction).
    Returns 0-100 (lower is better for leaders).
    """
    tail = df["Close"].tail(weeks * 5)
    if len(tail) < 5:
        return 100.0
    hi, lo = float(tail.max()), float(tail.min())
    mid = (hi + lo) / 2
    return round((hi - lo) / mid * 100, 2) if mid else 100.0


def _up_down_volume_ratio(df: pd.DataFrame, lookback: int = LB_UD_RATIO_LOOKBACK) -> float:
    """
    Up/Down Volume Ratio over last `lookback` days.
    >1.5 = strong accumulation; <0.7 = distribution.
    """
    tail = df.tail(lookback + 1).copy()
    if len(tail) < 3:
        return 1.0
    tail["pct_chg"] = tail["Close"].pct_change()
    up_vol   = tail.loc[tail["pct_chg"] > 0, "Volume"].sum()
    down_vol = tail.loc[tail["pct_chg"] < 0, "Volume"].sum()
    return round(up_vol / down_vol, 2) if down_vol > 0 else 2.0


def _consecutive_up_days(df: pd.DataFrame) -> int:
    """Count of consecutive up days ending today."""
    closes = df["Close"].values
    count = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            count += 1
        else:
            break
    return count


def _proximity_to_52w_high(df: pd.DataFrame) -> float:
    """% below 52-week high. 0 = AT the high; 5 = 5% below."""
    high52 = float(df["Close"].tail(TRADING_DAYS_YEAR).max())
    current = float(df["Close"].iloc[-1])
    return round((high52 - current) / high52 * 100, 2) if high52 else 100.0


def _returns(close: pd.Series) -> dict:
    def r(n):
        if len(close) <= n:
            return None
        p = close.iloc[-1 - n]
        return round((close.iloc[-1] / p - 1) * 100, 2) if p else None
    return {
        "r1d":  r(1),
        "r1w":  r(5),
        "r1m":  r(21),
        "r3m":  r(63),
        "r6m":  r(126),
        "r52w": r(252),
    }


def _compute_leadership_score(
    rs: int,
    trend_score: int,
    accum: float,
    tight: float,
    vol_ratio: float,
    prox_52w: float,
    drs7: float,
    confluence_count: int,
    ud_ratio: float,
) -> float:
    """
    Composite Leadership Score (0-100).
    Weights are calibrated to match O'Neil / Minervini ranking philosophy.
    """
    # 1. RS Rating (25 pts) — most important
    rs_pts = rs / 99 * 25

    # 2. Trend Template (20 pts) — must be in proper uptrend
    trend_pts = trend_score / 4 * 20

    # 3. Accumulation Score (15 pts) — institutional buying
    accum_pts = max(0.0, min((accum + 1) / 2, 1.0)) * 15

    # 4. Proximity to 52W High (15 pts) — leaders near highs (invert: closer = higher)
    prox_pts = max(0.0, (10 - prox_52w) / 10) * 15
    prox_pts = max(0.0, min(prox_pts, 15.0))

    # 5. Base Tightness (10 pts) — tighter = more coiled (invert)
    tight_pts = max(0.0, (20 - tight) / 20) * 10
    tight_pts = max(0.0, min(tight_pts, 10.0))

    # 6. Momentum Acceleration dRS7 (8 pts)
    drs_pts = max(0.0, min(drs7 / 20, 1.0)) * 8

    # 7. Volume signals (4 pts each for vol_ratio + ud_ratio)
    vol_pts = min(vol_ratio / 3, 1.0) * 4
    ud_pts  = min(ud_ratio / 2.5, 1.0) * 4 if ud_ratio else 0

    # 8. Confluence signals bonus (3 pts max)
    conf_pts = min(confluence_count, 3) * 1.0

    total = rs_pts + trend_pts + accum_pts + prox_pts + tight_pts + drs_pts + vol_pts + ud_pts + conf_pts
    return round(min(total, 100.0), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

@ttl_cache(CACHE_TTL_DATA)
def compute_leadership_board(
    combined: tuple,   # tuple of (ticker, market, name, theme) so it's hashable
    rs_now_dict: tuple,   # tuple of (ticker, rs_int)
    rs_7d_dict: tuple,
    ticker_signal_dict: tuple,  # tuple of (ticker, confluence_count)
) -> dict:
    """
    combined, rs_now_dict, rs_7d_dict, ticker_signal_dict are passed as tuples
    (hashable) so TTL cache works. Caller converts dicts to sorted tuples.
    """
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    rs_now = dict(rs_now_dict)
    rs_7d  = dict(rs_7d_dict)
    sig_cnt = dict(ticker_signal_dict)

    rows = []
    for ticker, market, name, theme in combined:
        # combined is (ticker, market, name, theme) tuples
        pass

    # We re-fetch from the module-level cache in data_io
    # Actually compute from global combined dict passed in
    return {"ok": True, "updated": now_str, "rows": rows}


def build_leadership_board(
    combined: dict[str, "pd.DataFrame"],
    ticker_meta: dict[str, dict],
    rs_now: "pd.Series",
    rs_7d:  "pd.Series",
    ticker_signal: dict[str, dict],
) -> dict:
    """
    Real entry point called from pipeline.py / backend.py.
    Receives already-computed data from the main pipeline.
    """
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    rows = []
    for ticker, df in combined.items():
        meta = ticker_meta.get(ticker, {})
        market = meta.get("market", "")
        name   = meta.get("name", ticker)
        theme  = meta.get("theme", "")

        if len(df) < 60:
            continue

        close = df["Close"]

        # Core metrics
        tt    = _trend_template(df)
        accum = _accumulation_score(df)
        tight = _base_tightness(df)
        ud    = _up_down_volume_ratio(df)
        prox  = _proximity_to_52w_high(df)
        consec= _consecutive_up_days(df)
        rets  = _returns(close)

        # Volume
        vol_today = float(df["Volume"].iloc[-1])
        vol_avg50 = float(df["Volume"].tail(51).iloc[:-1].mean()) if len(df) >= 51 else float(df["Volume"].mean())
        vol_ratio = round(vol_today / vol_avg50, 2) if vol_avg50 else 1.0

        # RS
        rs_val = int(rs_now.get(ticker, 50))
        rs_7val= int(rs_7d.get(ticker, rs_val))
        drs7   = rs_val - rs_7val

        # Confluence
        sig = ticker_signal.get(ticker, {})
        conf_count = sig.get("count", 0)
        patterns   = [k for k, v in sig.get("rolled", {}).items() if v]

        # Minervini scanner signals from data_engine
        sig_results = eng.run_scanners(df)
        _, _, cnt_series = eng.confluence_flags(sig_results)
        is_near_52w = bool(eng.scan_52w_high(df).iloc[-1])
        is_pocket   = bool(eng.scan_pocket_pivot(df).iloc[-1])
        is_vdu      = bool(eng.scan_volume_dry_up(df).iloc[-1])
        is_bgu      = bool(eng.scan_buyable_gap_up(df).iloc[-1])

        # Leadership Score
        ls = _compute_leadership_score(
            rs=rs_val, trend_score=tt["score"], accum=accum,
            tight=tight, vol_ratio=vol_ratio, prox_52w=prox,
            drs7=drs7, confluence_count=conf_count, ud_ratio=ud
        )

        rows.append({
            "ticker":      ticker,
            "symbol":      ticker.split(".")[0],
            "market":      market,
            "name":        name,
            "theme":       theme,
            "price":       round(float(close.iloc[-1]), 2),
            # returns
            "r1d":         rets["r1d"],
            "r1w":         rets["r1w"],
            "r1m":         rets["r1m"],
            "r3m":         rets["r3m"],
            "r6m":         rets["r6m"],
            "r52w":        rets["r52w"],
            # RS
            "rs":          rs_val,
            "drs7":        drs7,
            # Trend
            "trend_score": tt["score"],
            "trend_c1":    tt["c1_above_50"],
            "trend_c2":    tt["c2_above_150"],
            "trend_c3":    tt["c3_above_200"],
            "trend_c4":    tt["c4_slope_up"],
            "sma200_slope":tt["sma200_slope"],
            # Volume / Accumulation
            "vol_ratio":   vol_ratio,
            "accum_score": accum,
            "ud_ratio":    ud,
            # Proximity / Base
            "prox_52w":    prox,
            "base_tight":  tight,
            "consec_up":   consec,
            # Signals
            "is_near_52w": is_near_52w,
            "is_pocket":   is_pocket,
            "is_vdu":      is_vdu,
            "is_bgu":      is_bgu,
            "conf_count":  conf_count,
            "patterns":    patterns,
            # Leadership score
            "ls":          ls,
        })

    if not rows:
        return {"ok": False, "error": "No leadership data available", "updated": now_str}

    # Sort views
    def top(key, n=20, filt=None, reverse=True):
        r = [x for x in rows if filt(x)] if filt else rows
        r = [x for x in r if x.get(key) is not None]
        return sorted(r, key=lambda x: x[key], reverse=reverse)[:n]

    return {
        "ok":            True,
        "updated":       now_str,
        "total":         len(rows),
        # The 7 leaderboard tabs
        "overall":       top("ls"),
        "top_rs":        top("rs"),
        "top_momentum":  top("drs7"),
        "near_breakout": top("ls", filt=lambda x: x["prox_52w"] <= 5 and x["trend_score"] >= 3),
        "institutional": top("ls", filt=lambda x: x["accum_score"] >= 0.2 and x["ud_ratio"] >= 1.3),
        "volume_surge":  top("vol_ratio", filt=lambda x: x["vol_ratio"] >= 1.5),
        "trend_template":top("ls", filt=lambda x: x["trend_score"] == 4),
    }
