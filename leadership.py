# FILE: leadership.py

from __future__ import annotations
import pandas as pd
from datetime import datetime

# ✨ FIX: เพิ่มการ import ที่จำเป็น และนำ decorator กลับมาใช้งาน
from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA
import data_engine as eng

# ✨ FIX: เพิ่ม @ttl_cache decorator เพื่อแก้ปัญหา App Crash on Startup
# และปรับปรุง Signature ของฟังก์ชันให้รับข้อมูลที่ประมวลผลแล้วมาเลย ไม่ต้องไปเรียก pipeline ซ้ำซ้อน
@ttl_cache(CACHE_TTL_DATA)
def build_leadership_board(combined: dict, ticker_meta: dict, rs_now: pd.Series, rs_7: pd.Series, ticker_signal: dict) -> dict:
    """
    Analyzes pipeline data to build the full leadership board.
    This version is refactored to accept pre-computed data, improving performance and SRP.
    """
    try:
        rows = []
        for ticker, data in combined.items():
            if len(data) < 200: continue

            meta = ticker_meta.get(ticker, {})
            signal_info = ticker_signal.get(ticker, {"rolled": {}})

            # --- Core Metrics ---
            rs = int(rs_now.get(ticker, 0))
            drs7 = int(rs - rs_7.get(ticker, rs))
            close = data["Close"].iloc[-1]
            vol_sma50 = data["VOL_SMA50"].iloc[-1]
            vol_today = data["Volume"].iloc[-1]
            vol_ratio = round(vol_today / vol_sma50, 2) if vol_sma50 else 1.0

            # --- Trend Template (Minervini) ---
            sma50, sma150, sma200 = data["SMA50"].iloc[-1], data["SMA150"].iloc[-1], data["SMA200"].iloc[-1]
            c1 = close > sma50
            c2 = sma50 > sma150
            c3 = sma150 > sma200
            c4 = data["SMA200"].rolling(20).mean().iloc[-1] > data["SMA200"].rolling(20).mean().iloc[-2] # Slope
            trend_score = sum([c1, c2, c3, c4])

            # --- Proximity to 52W High & Drawdown ---
            high_52w = data["HIGH_52W"].iloc[-1]
            prox_52w = round((close / high_52w - 1) * 100, 1) if high_52w else 100
            drawdown_pct = eng.current_drawdown_from_peak(data["Close"])

            # --- Accumulation/Distribution Metrics ---
            ud_ratio_series = data['Close'].diff().apply(lambda x: 1 if x > 0 else -1) * data['Volume']
            ud_ratio = round(ud_ratio_series.rolling(20).sum() / data['Volume'].rolling(20).mean(), 2) if not ud_ratio_series.empty else 0
            accum_score = round(eng.max_drawdown(data['Close'].tail(50)) / 100, 2) # Simplified version

            # --- Base Tightness (Volatility in last 6 weeks) ---
            base_tight = round(data['Close'].tail(30).std() / data['Close'].tail(30).mean() * 100, 1)

            # --- Leadership Score (LS) ---
            ls = (
                (rs / 99) * 25 +
                (trend_score / 4) * 20 +
                ((1 - abs(prox_52w/100)) * 15 if prox_52w <= 0 else 0) +
                (min(max(accum_score, -1), 1) + 1) / 2 * 15 +
                max(1 - (base_tight / 20), 0) * 10 +
                (min(max(drs7, -10), 10) + 10) / 20 * 8 +
                (min(vol_ratio, 3) / 3) * 7
            )

            rows.append({
                "ticker": ticker, "symbol": ticker.split('.')[0], "name": meta.get("name", ""),
                "theme": meta.get("theme", ""), "market": meta.get("market", ""), "ls": int(ls),
                "rs": rs, "drs7": drs7, "trend_score": trend_score, "trend_c1": c1, "trend_c2": c2, "trend_c3": c3, "trend_c4": c4,
                "prox_52w": -prox_52w, "drawdown_pct": drawdown_pct,
                "r1d": eng._pct_change(data["Close"], 1),
                "r1m": eng._pct_change(data["Close"], 21),
                "r3m": eng._pct_change(data["Close"], 63),
                "vol_ratio": vol_ratio, "accum_score": accum_score, "ud_ratio": ud_ratio, "base_tight": base_tight,
                "is_vdu": signal_info["rolled"].get("VDU", False),
                "is_pocket": signal_info["rolled"].get("PPBP", False),
                "is_bgu": signal_info["rolled"].get("BGU", False),
                "is_near_52w": signal_info["rolled"].get("52W", False),
            })

        # --- Create Leaderboard Tabs ---
        df = pd.DataFrame(rows).set_index("ticker", drop=False)
        overall = df.sort_values("ls", ascending=False).to_dict("records")

        return {
            "ok": True, "updated": datetime.now().strftime("%d/%m/%Y %H:%M"), "total": len(df),
            "overall": overall[:50],
            "top_rs": df.sort_values("rs", ascending=False).to_dict("records")[:50],
            "top_momentum": df[df['rs'] >= 60].sort_values("drs7", ascending=False).to_dict("records")[:50],
            "near_breakout": df[(df['prox_52w'] <= 5) & (df['trend_score'] >= 3)].sort_values("prox_52w").to_dict("records")[:50],
            "institutional": df[(df['accum_score'] >= 0.2) & (df['ud_ratio'] >= 1.3)].sort_values("accum_score", ascending=False).to_dict("records")[:50],
            "volume_surge": df[df['vol_ratio'] >= 1.5].sort_values("vol_ratio", ascending=False).to_dict("records")[:50],
            "trend_template": df[df['trend_score'] == 4].sort_values("rs", ascending=False).to_dict("records")[:50],
        }

    except Exception as e:
        return {"ok": False, "error": f"Error in build_leadership_board: {e}"}
