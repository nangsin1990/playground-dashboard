# FILE: leadership.py

from __future__ import annotations
from datetime import datetime
import pandas as pd
import numpy as np

from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_DATA, LB_TREND_LOOKBACK, LB_ACCUM_LOOKBACK, LB_TIGHTNESS_WEEKS,
    LB_UD_RATIO_LOOKBACK, LB_VOL_WINDOW, LB_BREAKOUT_PROX, LB_ACCUM_MIN,
    LB_UD_MIN, LB_VOL_MIN, LB_TOP_N,
)
import data_engine as eng

def _calc_trend_template(df: pd.DataFrame) -> dict:
    """Calculates Mark Minervini's Trend Template score (0-4)."""
    if len(df) < 200:
        return {"c1": False, "c2": False, "c3": False, "c4": False, "score": 0}

    last = df.iloc[-1]
    c1 = last["Close"] > last.get("SMA50", 0)
    c2 = last["Close"] > last.get("SMA200", 0)
    c3 = last.get("SMA50", 0) > last.get("SMA200", 0)

    # SMA200 slope
    sma200_tail = df["SMA200"].tail(LB_TREND_LOOKBACK)
    c4 = sma200_tail.iloc[-1] > sma200_tail.iloc[0] if len(sma200_tail) > 1 else False

    score = sum([c1, c2, c3, c4])
    return {"trend_c1": bool(c1), "trend_c2": bool(c2), "trend_c3": bool(c3), "trend_c4": bool(c4), "score": int(score)}

def _calc_accumulation(df: pd.DataFrame) -> dict:
    """Calculates Up/Down Volume Ratio and Accumulation Score."""
    if len(df) < LB_UD_RATIO_LOOKBACK:
        return {"ud_ratio": 1.0, "accum_score": 0.0}

    tail = df.tail(LB_UD_RATIO_LOOKBACK)
    change = tail["Close"].diff()
    up_vol = tail["Volume"][change > 0].sum()
    down_vol = tail["Volume"][change <= 0].sum()
    ud_ratio = up_vol / down_vol if down_vol > 0 else 5.0 # Avoid division by zero

    # Accumulation/Distribution Score
    ad = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low']).replace(0, np.nan) * df['Volume']
    ad_smooth = ad.ewm(span=LB_ACCUM_LOOKBACK, adjust=False).mean()
    vol_smooth = df['Volume'].ewm(span=LB_ACCUM_LOOKBACK, adjust=False).mean()

    accum_score = ad_smooth.iloc[-1] / vol_smooth.iloc[-1] if vol_smooth.iloc[-1] > 0 else 0.0

    return {"ud_ratio": round(float(ud_ratio), 2), "accum_score": round(float(accum_score), 3)}

def _calc_volatility(df: pd.DataFrame) -> dict:
    """Calculates Base Tightness and recent Volume Surge."""
    # Base Tightness (% volatility over X weeks)
    lookback = LB_TIGHTNESS_WEEKS * 5
    if len(df) < lookback:
        return {"base_tight": 100.0, "vol_ratio": 1.0}

    tail = df["Close"].tail(lookback)
    base_tight = (tail.max() - tail.min()) / tail.min() * 100

    # Volume Ratio
    vol_tail = df["Volume"].tail(LB_VOL_WINDOW)
    vol_ratio = vol_tail.iloc[-1] / vol_tail.iloc[:-1].mean() if len(vol_tail) > 1 else 1.0

    return {"base_tight": round(float(base_tight), 2), "vol_ratio": round(float(vol_ratio), 1)}

@ttl_cache(CACHE_TTL_DATA)
def build_leadership_board(combined: dict, ticker_meta: dict, rs_now: pd.Series, rs_7: pd.Series, ticker_signal: dict) -> dict:
    all_stocks = []
    for ticker, df in combined.items():
        if len(df) < 50:
            continue

        meta = ticker_meta.get(ticker, {})
        last = df.iloc[-1]

        # Calculations
        trend_data = _calc_trend_template(df)
        accum_data = _calc_accumulation(df)
        vol_data = _calc_volatility(df)

        prox_52w = (last["Close"] / last.get("HIGH_52W", last["Close"]) - 1) * 100
        drawdown_pct = eng.current_drawdown_from_peak(df["Close"])

        rs_val = int(rs_now.get(ticker, 0))
        drs7_val = int(rs_val - rs_7.get(ticker, rs_val))

        # Leadership Score (weighted average)
        ls_rs = rs_val * 0.25
        ls_trend = (trend_data["score"] / 4) * 100 * 0.20
        ls_prox = max(0, 100 - abs(prox_52w * 4)) * 0.15 # Stronger score closer to high
        ls_accum = min(1, max(0, accum_data["accum_score"] / 0.5)) * 100 * 0.15
        ls_tight = max(0, 100 - vol_data["base_tight"] * 2) * 0.10
        ls_drs7 = min(100, max(0, drs7_val * 5)) * 0.08
        ls_vol = min(100, (vol_data["vol_ratio"] / 2) * 100) * 0.07
        ls_total = int(ls_rs + ls_trend + ls_prox + ls_accum + ls_tight + ls_drs7 + ls_vol)

        signals = ticker_signal.get(ticker, {})

        all_stocks.append({
            "ticker": ticker,
            "symbol": ticker.split(".")[0],
            "name": meta.get("name", ""),
            "theme": meta.get("theme", ""),
            "market": meta.get("market", ""),
            "ls": ls_total,
            "rs": rs_val,
            "drs7": drs7_val,
            **trend_data,
            **accum_data,
            **vol_data,
            "prox_52w": abs(round(prox_52w, 1)),
            "drawdown_pct": round(drawdown_pct,1),
            "r1d": eng.rs_vs_benchmark(df['Close'], df['Close'], [1]).get('periods', {}).get('p1',{}).get('stock_ret',0),
            "r1m": eng.rs_vs_benchmark(df['Close'], df['Close'], [21]).get('periods', {}).get('p21',{}).get('stock_ret',0),
            "r3m": eng.rs_vs_benchmark(df['Close'], df['Close'], [63]).get('periods', {}).get('p63',{}).get('stock_ret',0),
            "is_vdu": signals.get("rolled", {}).get("VDU", False),
            "is_pocket": signals.get("rolled", {}).get("PPBP", False),
            "is_bgu": signals.get("rolled", {}).get("BGU", False),
            "is_near_52w": signals.get("rolled", {}).get("52W", False),
        })

    # Sort and filter for different tabs
    # Overall
    overall = sorted(all_stocks, key=lambda x: x["ls"], reverse=True)[:LB_TOP_N * 2]
    # Top RS
    top_rs = sorted([s for s in all_stocks if s["rs"] >= 90], key=lambda x: x["rs"], reverse=True)[:LB_TOP_N]
    # Momentum
    top_momentum = sorted([s for s in all_stocks if s["drs7"] > 0], key=lambda x: x["drs7"], reverse=True)[:LB_TOP_N]
    # Near Breakout
    near_breakout = sorted([s for s in all_stocks if s["prox_52w"] <= LB_BREAKOUT_PROX and s["trend_score"] >= 3], key=lambda x: x["prox_52w"])[:LB_TOP_N]
    # Institutional Buying
    institutional = sorted([s for s in all_stocks if s["accum_score"] >= LB_ACCUM_MIN and s["ud_ratio"] >= LB_UD_MIN], key=lambda x: x["accum_score"], reverse=True)[:LB_TOP_N]
    # Volume Surge
    volume_surge = sorted([s for s in all_stocks if s["vol_ratio"] >= LB_VOL_MIN], key=lambda x: x["vol_ratio"], reverse=True)[:LB_TOP_N]
    # Trend Template
    trend_template = sorted([s for s in all_stocks if s["trend_score"] == 4 and s["rs"] > 70], key=lambda x: x["rs"], reverse=True)[:LB_TOP_N]

    return {
        "ok": True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total": len(all_stocks),
        "overall": overall,
        "top_rs": top_rs,
        "top_momentum": top_momentum,
        "near_breakout": near_breakout,
        "institutional": institutional,
        "volume_surge": volume_surge,
        "trend_template": trend_template,
    }
