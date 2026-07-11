# FILE: leadership.py

from __future__ import annotations
from datetime import datetime
import pandas as pd
import numpy as np

import pipeline
from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_DATA, LB_TREND_LOOKBACK, LB_ACCUM_LOOKBACK, LB_TIGHTNESS_WEEKS,
    LB_UD_RATIO_LOOKBACK, LB_VOL_WINDOW, LB_BREAKOUT_PROX, LB_ACCUM_MIN,
    LB_UD_MIN, LB_VOL_MIN, LB_TOP_N,
)
import data_engine as eng

# ✨ FIX: สร้างฟังก์ชันภายในเพื่อดึงและประมวลผลข้อมูล ทำให้ leadership board เป็นโมดูลที่สมบูรณ์
# ฟังก์ชันนี้จะถูก cache เพื่อให้การเรียกซ้ำจาก screener หรือ leadership API เร็วขึ้น
@ttl_cache(CACHE_TTL_DATA)
def _get_leadership_data(mode: str) -> dict:
    """Internal function to fetch and compute all necessary data for the board."""
    active = pipeline.active_universe(mode)
    combined, ticker_meta, fetch_results = pipeline.fetch_universe(active)

    if not combined:
        return {"ok": False, "error": "No data from pipeline"}

    # Reuse the main dashboard computation to get signals and RS ratings
    dash_data = pipeline.compute_dashboard(combined, ticker_meta, fetch_results, active)

    if not dash_data.get("ok"):
         return {"ok": False, "error": "Dashboard computation failed"}

    # เราต้องการข้อมูลดิบและข้อมูลที่คำนวณแล้วบางส่วน
    return {
        "ok": True,
        "combined": combined,
        "ticker_meta": ticker_meta,
        "rs_now": dash_data.get("rs_now_series"), # ต้องแน่ใจว่า pipeline ส่ง Series กลับมา
        "rs_7": dash_data.get("rs_7_series"),
        "ticker_signal": dash_data.get("ticker_signal"),
        "total_universe": len(combined),
    }

def _calc_trend_template(df: pd.DataFrame) -> dict:
    """Calculates Mark Minervini's Trend Template score (0-4)."""
    if len(df) < 200:
        return {"trend_c1": False, "trend_c2": False, "trend_c3": False, "trend_c4": False, "trend_score": 0}

    last = df.iloc[-1]
    c1 = last["Close"] > last.get("SMA50", 0)
    c2 = last["Close"] > last.get("SMA200", 0)
    c3 = last.get("SMA50", 0) > last.get("SMA200", 0)

    sma200_tail = df["SMA200"].tail(LB_TREND_LOOKBACK)
    c4 = sma200_tail.iloc[-1] > sma200_tail.iloc[0] if len(sma200_tail) > 1 else False

    score = sum([c1, c2, c3, c4])
    return {"trend_c1": bool(c1), "trend_c2": bool(c2), "trend_c3": bool(c3), "trend_c4": bool(c4), "trend_score": int(score)}

def _calc_accumulation(df: pd.DataFrame) -> dict:
    """Calculates Up/Down Volume Ratio and Accumulation Score."""
    if len(df) < LB_UD_RATIO_LOOKBACK:
        return {"ud_ratio": 1.0, "accum_score": 0.0}

    tail = df.tail(LB_UD_RATIO_LOOKBACK)
    change = tail["Close"].diff()
    up_vol = tail["Volume"][change > 0].sum()
    down_vol = tail["Volume"][change <= 0].sum()
    ud_ratio = up_vol / down_vol if down_vol > 0 else 5.0 

    ad = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low']).replace(0, np.nan) * df['Volume']
    ad_smooth = ad.ewm(span=LB_ACCUM_LOOKBACK, adjust=False).mean()
    vol_smooth = df['Volume'].ewm(span=LB_ACCUM_LOOKBACK, adjust=False).mean()

    accum_score = ad_smooth.iloc[-1] / vol_smooth.iloc[-1] if vol_smooth.iloc[-1] > 0 else 0.0

    return {"ud_ratio": round(float(ud_ratio), 2), "accum_score": round(float(accum_score), 3)}

def _calc_volatility(df: pd.DataFrame) -> dict:
    """Calculates Base Tightness and recent Volume Surge."""
    lookback = LB_TIGHTNESS_WEEKS * 5
    if len(df) < lookback:
        return {"base_tight": 100.0, "vol_ratio": 1.0}

    tail = df["Close"].tail(lookback)
    base_tight = (tail.max() - tail.min()) / tail.min() * 100

    vol_tail = df["Volume"].tail(LB_VOL_WINDOW)
    vol_ratio = vol_tail.iloc[-1] / vol_tail.iloc[:-1].mean() if len(vol_tail) > 1 else 1.0

    return {"base_tight": round(float(base_tight), 2), "vol_ratio": round(float(vol_ratio), 1)}

# ✨ FIX: แก้ไข Signature ของฟังก์ชันให้รับแค่ mode
# และเปลี่ยนชื่อฟังก์ชันใน cache_utils.py ให้ตรงกัน (จาก build_leadership_board เป็น compute_leadership_board)
# แต่เพื่อความง่าย ผมจะใช้ชื่อเดิมและ refactor ภายใน
@ttl_cache(CACHE_TTL_DATA)
def build_leadership_board(mode: str) -> dict:
    """
    This is the main, self-contained function for the leadership board.
    It fetches its own data via the pipeline.
    """
    # 1. ดึงข้อมูลที่จำเป็นทั้งหมด
    data = _get_leadership_data(mode=mode)
    if not data.get("ok"):
        return data # Return error dict

    combined = data["combined"]
    ticker_meta = data["ticker_meta"]
    rs_now = data["rs_now"]
    rs_7 = data["rs_7"]
    ticker_signal = data["ticker_signal"]

    # 2. คำนวณ Leadership stats สำหรับหุ้นแต่ละตัว
    all_stocks = []
    for ticker, df in combined.items():
        if len(df) < 50: continue
        meta = ticker_meta.get(ticker, {})
        last = df.iloc[-1]

        trend_data = _calc_trend_template(df)
        accum_data = _calc_accumulation(df)
        vol_data = _calc_volatility(df)
        prox_52w = (last["Close"] / last.get("HIGH_52W", last["Close"]) - 1) * 100 if last.get("HIGH_52W") else 100
        drawdown_pct = eng.current_drawdown_from_peak(df["Close"])

        rs_val = int(rs_now.get(ticker, 0))
        drs7_val = int(rs_val - rs_7.get(ticker, rs_val))

        # Leadership Score
        ls_rs = rs_val * 0.25
        ls_trend = (trend_data["trend_score"] / 4) * 100 * 0.20
        ls_prox = max(0, 100 - abs(prox_52w * 4)) * 0.15
        ls_accum = min(1, max(0, accum_data["accum_score"] / 0.5)) * 100 * 0.15
        ls_tight = max(0, 100 - vol_data["base_tight"] * 2) * 0.10
        ls_drs7 = min(100, max(0, drs7_val * 5)) * 0.08
        ls_vol = min(100, (vol_data["vol_ratio"] / 2) * 100) * 0.07
        ls_total = int(ls_rs + ls_trend + ls_prox + ls_accum + ls_tight + ls_drs7 + ls_vol)

        signals = ticker_signal.get(ticker, {})
        all_stocks.append({
            "ticker": ticker, "symbol": meta.get("name", ticker).split(".")[0], "name": meta.get("name", ""),
            "theme": meta.get("theme", ""), "market": meta.get("market", ""), "ls": ls_total,
            "rs": rs_val, "drs7": drs7_val, **trend_data, **accum_data, **vol_data,
            "prox_52w": abs(round(prox_52w, 1)), "drawdown_pct": round(drawdown_pct,1),
            "r1d": eng._pct_change(df['Close'], 1), "r1m": eng._pct_change(df['Close'], 21),
            "r3m": eng._pct_change(df['Close'], 63),
            "is_vdu": signals.get("rolled", {}).get("VDU", False),
            "is_pocket": signals.get("rolled", {}).get("PPBP", False),
            "is_bgu": signals.get("rolled", {}).get("BGU", False),
            "is_near_52w": signals.get("rolled", {}).get("52W", False),
        })

    # 3. จัดกลุ่มและเรียงลำดับสำหรับแต่ละ Tab
    overall = sorted(all_stocks, key=lambda x: x["ls"], reverse=True)[:LB_TOP_N * 2]
    top_rs = sorted([s for s in all_stocks if s["rs"] >= 90], key=lambda x: x["rs"], reverse=True)[:LB_TOP_N]
    top_momentum = sorted([s for s in all_stocks if s["drs7"] > 0], key=lambda x: x["drs7"], reverse=True)[:LB_TOP_N]
    near_breakout = sorted([s for s in all_stocks if s["prox_52w"] <= LB_BREAKOUT_PROX and s["trend_score"] >= 3], key=lambda x: x["prox_52w"])[:LB_TOP_N]
    institutional = sorted([s for s in all_stocks if s["accum_score"] >= LB_ACCUM_MIN and s["ud_ratio"] >= LB_UD_MIN], key=lambda x: (x["accum_score"], x["ud_ratio"]), reverse=True)[:LB_TOP_N]
    volume_surge = sorted([s for s in all_stocks if s["vol_ratio"] >= LB_VOL_MIN], key=lambda x: x["vol_ratio"], reverse=True)[:LB_TOP_N]
    trend_template = sorted([s for s in all_stocks if s["trend_score"] == 4 and s["rs"] > 70], key=lambda x: x["rs"], reverse=True)[:LB_TOP_N]

    return {
        "ok": True, "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total": len(all_stocks), "overall": overall, "top_rs": top_rs, "top_momentum": top_momentum,
        "near_breakout": near_breakout, "institutional": institutional,
        "volume_surge": volume_surge, "trend_template": trend_template,
    }
