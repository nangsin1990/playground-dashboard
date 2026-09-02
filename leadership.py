# FILE: leadership.py

from __future__ import annotations
from datetime import datetime
import logging
import time

import pandas as pd
import numpy as np

import pipeline
from constants import (
    LB_TREND_LOOKBACK, LB_ACCUM_LOOKBACK, LB_TIGHTNESS_WEEKS,
    LB_UD_RATIO_LOOKBACK, LB_VOL_WINDOW, LB_BREAKOUT_PROX, LB_ACCUM_MIN,
    LB_UD_MIN, LB_VOL_MIN, LB_TOP_N,
)
import data_engine as eng

log = logging.getLogger("playground.leadership")

def _get_leadership_data(mode: str, pack: dict | None = None) -> dict:
    pack = pack if pack is not None else pipeline.load_market_pack(mode)
    dash_data = pack.get("dash") or {}
    combined = pack.get("combined") or {}
    if not combined or not dash_data.get("ok"):
        return {"ok": False, "error": dash_data.get("error") or "No data from pipeline"}
    return {
        "ok": True,
        "pack": pack,
        "combined": combined,
        "ticker_meta": pack.get("ticker_meta") or {},
        "rs_now": dash_data.get("rs_now"),
        "rs_7": dash_data.get("rs_7"),
        "ticker_signal": dash_data.get("ticker_signal"),
        "total_universe": len(combined),
    }


def _gt(a, b) -> bool:
    try:
        if a is None or b is None or pd.isna(a) or pd.isna(b):
            return False
        return float(a) > float(b)
    except (TypeError, ValueError):
        return False


def _calc_trend_template(df: pd.DataFrame) -> dict:
    """Minervini-lite ตามที่หน้า Leaders ระบุ: Px>50, Px>150, Px>200, SMA200 ชันขึ้น."""
    empty = {"trend_c1": False, "trend_c2": False, "trend_c3": False, "trend_c4": False, "trend_score": 0}
    if df is None or len(df) < 150:
        return empty
    last = df.iloc[-1]
    px = last.get("Close")
    sma50 = last.get("SMA50")
    sma150 = last.get("SMA150")
    sma200 = last.get("SMA200")
    if sma150 is None or pd.isna(sma150):
        sma150 = df["Close"].rolling(150, min_periods=50).mean().iloc[-1]
    if sma200 is None or pd.isna(sma200):
        if len(df) >= 200:
            sma200 = df["Close"].rolling(200, min_periods=100).mean().iloc[-1]
    c1 = _gt(px, sma50)
    c2 = _gt(px, sma150)
    c3 = _gt(px, sma200)
    sma200_s = df["SMA200"] if "SMA200" in df.columns else df["Close"].rolling(200, min_periods=100).mean()
    tail = sma200_s.tail(LB_TREND_LOOKBACK).dropna()
    c4 = bool(len(tail) > 1 and _gt(tail.iloc[-1], tail.iloc[0]))
    score = int(c1) + int(c2) + int(c3) + int(c4)
    return {"trend_c1": bool(c1), "trend_c2": bool(c2), "trend_c3": bool(c3), "trend_c4": bool(c4), "trend_score": score}

def _calc_accumulation(df: pd.DataFrame) -> dict:
    if len(df) < LB_UD_RATIO_LOOKBACK:
        return {"ud_ratio": 1.0, "accum_score": 0.0}
    tail = df.tail(LB_UD_RATIO_LOOKBACK)
    change = tail["Close"].diff()
    up_vol = tail["Volume"][change > 0].sum()
    down_vol = tail["Volume"][change <= 0].sum()
    ud_ratio = up_vol / down_vol if down_vol > 0 else 5.0
    ad = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low']).replace(0, np.nan) * df['Volume']
    ad = ad.fillna(0)
    ad_smooth = ad.ewm(span=LB_ACCUM_LOOKBACK, adjust=False).mean()
    vol_smooth = df['Volume'].ewm(span=LB_ACCUM_LOOKBACK, adjust=False).mean()
    accum_score = ad_smooth.iloc[-1] / vol_smooth.iloc[-1] if vol_smooth.iloc[-1] > 0 else 0.0
    return {"ud_ratio": round(float(ud_ratio), 2), "accum_score": round(float(accum_score), 3)}

def _calc_volatility(df: pd.DataFrame) -> dict:
    lookback = LB_TIGHTNESS_WEEKS * 5
    if len(df) < lookback:
        return {"base_tight": 100.0, "vol_ratio": 1.0}
    tail = df["Close"].tail(lookback)
    base_tight = (tail.max() - tail.min()) / tail.min() * 100 if tail.min() > 0 else 100.0
    vol_tail = df["Volume"].tail(LB_VOL_WINDOW)
    vol_ratio = vol_tail.iloc[-1] / vol_tail.iloc[:-1].mean() if len(vol_tail) > 1 and vol_tail.iloc[:-1].mean() > 0 else 1.0
    return {"base_tight": round(float(base_tight), 2), "vol_ratio": round(float(vol_ratio), 1)}


def build_leadership_board(mode: str) -> dict:
    t0 = time.time()
    pack = pipeline.load_market_pack(mode)
    cached = pack.get("_leadership")
    if isinstance(cached, dict) and cached.get("ok"):
        return cached
    data = _get_leadership_data(mode=mode, pack=pack)
    if not data.get("ok"):
        return data

    combined = data["combined"]
    ticker_meta = data["ticker_meta"]
    rs_now = data.get("rs_now")
    rs_7 = data.get("rs_7")
    ticker_signal = data.get("ticker_signal")

    if rs_now is None: rs_now = pd.Series(dtype=float)
    if rs_7 is None: rs_7 = pd.Series(dtype=float)
    if ticker_signal is None: ticker_signal = {}

    all_stocks = []
    for ticker, df in combined.items():
        if df is None or len(df) < 50: continue
        meta = ticker_meta.get(ticker, {})
        last = df.iloc[-1]

        trend_data = _calc_trend_template(df)
        accum_data = _calc_accumulation(df)
        vol_data = _calc_volatility(df)

        high_52w = last.get("HIGH_52W", df['High'].tail(252).max() if len(df) >= 252 else df['High'].max())
        try:
            high_52w = float(high_52w)
        except (TypeError, ValueError):
            high_52w = 0.0
        off_52w = (float(last["Close"]) / high_52w - 1) * 100 if high_52w and high_52w > 0 else 0.0
        prox_below = 0.0 if off_52w >= 0 else abs(off_52w)

        drawdown_pct = eng.current_drawdown_from_peak(df["Close"])

        raw_rs = rs_now.get(ticker) if hasattr(rs_now, "get") else None
        try:
            rs_val = int(raw_rs) if raw_rs is not None and pd.notna(raw_rs) else None
        except (TypeError, ValueError):
            rs_val = None
        raw_rs7 = rs_7.get(ticker) if hasattr(rs_7, "get") else None
        try:
            rs7 = int(raw_rs7) if raw_rs7 is not None and pd.notna(raw_rs7) else None
        except (TypeError, ValueError):
            rs7 = None
        drs7_val = (rs_val - rs7) if rs_val is not None and rs7 is not None else 0

        ls_rs = (rs_val or 0) * 0.25
        ls_trend = (trend_data["trend_score"] / 4) * 100 * 0.20
        ls_prox = max(0, 100 - prox_below * 4) * 0.15
        ls_accum = min(1, max(0, accum_data["accum_score"] / 0.5)) * 100 * 0.15
        ls_tight = max(0, 100 - vol_data["base_tight"] * 2) * 0.10
        ls_drs7 = min(100, max(0, drs7_val * 5)) * 0.08
        ls_vol = min(100, max(0, (vol_data["vol_ratio"] - 1) * 50)) * 0.07
        ls_total = int(ls_rs + ls_trend + ls_prox + ls_accum + ls_tight + ls_drs7 + ls_vol)

        signals = ticker_signal.get(ticker, {})

        all_stocks.append({
            "ticker": ticker, "symbol": str(ticker).split(".")[0], "name": meta.get("name", ""),
            "theme": meta.get("theme", ""), "market": meta.get("market", ""), "ls": ls_total,
            "rs": rs_val, "drs7": drs7_val, **trend_data, **accum_data, **vol_data,
            "off_52w": round(float(off_52w), 1),
            "prox_52w": round(float(prox_below), 1),
            "drawdown_pct": round(drawdown_pct, 1),
            "price": round(float(last["Close"]), 4 if float(last["Close"]) < 1 else 2),
            "r1d": eng.pct_change(df['Close'], 1),
            "r1m": eng.pct_change(df['Close'], 21),
            "r3m": eng.pct_change(df['Close'], 63),
            "is_vdu": signals.get("rolled", {}).get("VDU", False),
            "is_pocket": signals.get("rolled", {}).get("PPBP", False),
            "is_bgu": signals.get("rolled", {}).get("BGU", False),
            "is_near_52w": signals.get("rolled", {}).get("52W", False),
            "is_vcp": signals.get("rolled", {}).get("VCP", False) or bool((signals.get("vcp") or {}).get("is_vcp")),
            **{k: v for k, v in (signals.get("vcp") or {}).items() if k.startswith("vcp_")},
        })
        last = all_stocks[-1]
        weak_rs = max(0, 99 - (rs_val if rs_val is not None else 50))
        fade = max(0, -drs7_val) * 6
        dd = abs(min(0.0, drawdown_pct))
        dist = 80 if accum_data["accum_score"] < 0 or accum_data["ud_ratio"] < 0.8 else 25
        broken = (4 - trend_data["trend_score"]) / 4 * 100
        last["ws"] = int(min(99, weak_rs * 0.30 + min(100, fade) * 0.20 + min(100, dd * 2) * 0.20 + dist * 0.15 + broken * 0.15))

    overall = sorted(all_stocks, key=lambda x: x["ls"], reverse=True)[:LB_TOP_N * 2]
    top_rs = sorted([s for s in all_stocks if (s.get("rs") or 0) >= 80], key=lambda x: x.get("rs") or 0, reverse=True)[:LB_TOP_N]
    top_momentum = sorted([s for s in all_stocks if s["drs7"] > 0], key=lambda x: x["drs7"], reverse=True)[:LB_TOP_N]
    near_breakout = sorted([s for s in all_stocks if s["prox_52w"] <= LB_BREAKOUT_PROX and s["trend_score"] >= 3], key=lambda x: x["prox_52w"])[:LB_TOP_N]
    institutional = sorted([s for s in all_stocks if s["accum_score"] >= LB_ACCUM_MIN and s["ud_ratio"] >= LB_UD_MIN], key=lambda x: (x["accum_score"], x["ud_ratio"]), reverse=True)[:LB_TOP_N]
    volume_surge = sorted([s for s in all_stocks if s["vol_ratio"] >= LB_VOL_MIN], key=lambda x: x["vol_ratio"], reverse=True)[:LB_TOP_N]
    trend_template = sorted([s for s in all_stocks if s["trend_score"] == 4 and (s.get("rs") or 0) > 70], key=lambda x: x.get("rs") or 0, reverse=True)[:LB_TOP_N]

    next_macro, earn_map = _calendar_overlay()
    for bucket in (overall, top_rs, top_momentum, near_breakout, institutional, volume_surge, trend_template, all_stocks):
        for row in bucket:
            tk = str(row.get("ticker") or "").split(".")[0].upper()
            if tk in earn_map:
                row["next_earnings"] = earn_map[tk]
            if next_macro:
                row["next_macro"] = next_macro

    out = {
        "ok": True, "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total": len(all_stocks), "overall": overall, "top_rs": top_rs, "top_momentum": top_momentum,
        "near_breakout": near_breakout, "institutional": institutional,
        "volume_surge": volume_surge, "trend_template": trend_template,
        "next_macro": next_macro,
        "universe": all_stocks,
        "markets": sorted({s.get("market") for s in all_stocks if s.get("market")}),
    }
    pack["_leadership"] = out
    log.info("leadership board mode=%s rows=%d in %.2fs", mode, len(all_stocks), time.time() - t0)
    return out


def build_laggards_board(mode: str) -> dict:
    board = build_leadership_board(mode)
    if not board.get("ok"):
        return board
    stocks = list(board.get("universe") or [])
    n = LB_TOP_N
    weak_rs = sorted(
        [s for s in stocks if s.get("rs") is not None and s.get("rs") <= 30],
        key=lambda x: x.get("rs") if x.get("rs") is not None else 99,
    )[:n]
    rs_fade = sorted([s for s in stocks if (s.get("drs7") or 0) < 0], key=lambda x: x.get("drs7") or 0)[:n]
    distribution = sorted(
        [s for s in stocks if (s.get("accum_score") or 0) <= -0.1 or (s.get("ud_ratio") or 1) <= 0.8],
        key=lambda x: (x.get("accum_score") or 0, x.get("ud_ratio") or 1),
    )[:n]
    off_highs = sorted(
        [s for s in stocks if (s.get("prox_52w") or 0) >= 20 or (s.get("drawdown_pct") or 0) <= -20],
        key=lambda x: x.get("prox_52w") or 0,
        reverse=True,
    )[:n]
    broken_trend = sorted(
        [s for s in stocks if (s.get("trend_score") or 0) <= 1],
        key=lambda x: (x.get("trend_score") or 0, x.get("rs") or 0),
    )[:n]
    worst = sorted(stocks, key=lambda x: x.get("ws") or 0, reverse=True)[:n]
    return {
        "ok": True,
        "updated": board.get("updated"),
        "total": len(stocks),
        "universe": stocks,
        "markets": board.get("markets") or [],
        "worst": worst,
        "weak_rs": weak_rs,
        "rs_fade": rs_fade,
        "distribution": distribution,
        "off_highs": off_highs,
        "broken_trend": broken_trend,
    }


def _calendar_overlay():
    # หน้า Leadership ใช้ชุดราคาเดียวกับหน้าแรก ไม่ดึงงบรายตัวจาก Yahoo
    return None, {}
