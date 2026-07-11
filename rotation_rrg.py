# FILE: rotation_rrg.py

from __future__ import annotations
from datetime import datetime
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_DATA, RRG_SMOOTHING, RRG_ROLL_MIN, RRG_TAIL_WEEKS
)
import data_engine as eng

# ✨ FIX: นำเข้า Universe และ Benchmark จากที่เดียวคือ universe.py
# เพื่อให้ง่ายต่อการจัดการและลดความซ้ำซ้อน
from universe import RRG_US_SECTORS, RRG_GLOBAL_UNIVERSE, BENCHMARK

# สร้าง Mapping สำหรับ Universe และ Benchmark ที่จะใช้
# เพิ่ม US_THEMES เข้าไปในอนาคตได้
UNIVERSE_MAP = {
    "GLOBAL": RRG_GLOBAL_UNIVERSE,
    "US_SECTORS": RRG_US_SECTORS,
    # "US_THEMES": RRG_US_THEMES, # Placeholder for future
}

def _fetch_weekly_prices(tickers: list[str], period: str = "2y") -> pd.DataFrame | None:
    """Fetches daily prices and resamples to weekly ('W-FRI')."""
    try:
        # ดึงข้อมูลรายวันย้อนหลัง 2 ปี เพื่อให้มีข้อมูลพอสำหรับคำนวณ RRG
        raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True, progress=False, timeout=20)
        if raw is None or raw.empty:
            return None
        # Resample to weekly data, taking the last price of each week (Friday)
        weekly = raw['Close'].resample('W-FRI').last().dropna(how='all')
        return weekly
    except Exception:
        return None

@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core", market: str = "GLOBAL") -> dict:
    """
    Fetches and computes Relative Rotation Graph (RRG) data.
    This version is completely rewritten to be functional.
    """
    try:
        # 1. Validate Market and select Universe/Benchmark
        selected_universe = UNIVERSE_MAP.get(market)
        if not selected_universe:
            return {"ok": False, "error": f"Invalid market specified for RRG: {market}"}

        benchmark_ticker = BENCHMARK.get(market, "VT")
        all_tickers = list(selected_universe.keys()) + [benchmark_ticker]

        # 2. Fetch Data (Weekly)
        df_weekly = _fetch_weekly_prices(all_tickers)
        if df_weekly is None or benchmark_ticker not in df_weekly.columns:
            return {"ok": False, "error": f"Could not fetch weekly data for benchmark {benchmark_ticker}"}

        # 3. Compute RRG using new functions in data_engine
        rrg_results = eng.compute_rrg(df_weekly, selected_universe.keys(), benchmark_ticker)
        if not rrg_results:
             return {"ok": False, "error": "RRG computation failed. Not enough data."}

        # 4. Format Output for Frontend
        rrg_list = []
        for ticker, data in rrg_results.items():
            rrg_list.append({
                "theme": selected_universe.get(ticker, ticker),
                "short": ticker,
                "quadrant": data["quadrant"],
                "rs_ratio": data["jrs"],
                "rs_momentum": data["jmo"],
                "tail": data["tail"],
                "avg_rs": None # Placeholder, can be added later
            })

        return {
            "ok": True,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "market": market,
            "benchmark": benchmark_ticker,
            "rrg": rrg_list,
        }
    except Exception as e:
        return {"ok": False, "error": f"An unexpected error occurred in fetch_rotation: {str(e)}"}
