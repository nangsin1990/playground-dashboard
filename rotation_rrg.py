# FILE: rotation_rrg.py

from __future__ import annotations
from datetime import datetime
import pandas as pd
import numpy as np

# ✨ FIX: Import ให้ถูกต้องตามโครงสร้างโปรเจกต์
import data_io
from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA, RRG_SMOOTHING
from universe import RRG_US_SECTORS, RRG_GLOBAL_UNIVERSE, BENCHMARK

# ✨ FIX: สร้าง Map ของ Universe ที่รองรับ เพื่อให้ Frontend เรียกใช้งานได้
UNIVERSE_MAP = {
    "GLOBAL": RRG_GLOBAL_UNIVERSE,
    "US_SECTORS": RRG_US_SECTORS,
    # สามารถเพิ่ม "US_THEMES" ได้ถ้ามี định nghĩa ใน universe.py
}

# --- Private Helper Functions for RRG Calculation ---
# (เนื่องจากไม่มีใน data_engine.py จึงต้องสร้างขึ้นมาใหม่ที่นี่)

def _calc_rs_ratio(asset_prices: pd.DataFrame, bench_prices: pd.Series) -> pd.DataFrame:
    """Calculates RS Ratio (Asset Price / Benchmark Price)"""
    return asset_prices.div(bench_prices, axis=0)

def _normalize_series(series: pd.Series, center: float = 100.0) -> pd.Series:
    """Normalizes a series around a center value based on its mean and std dev."""
    mean = series.mean()
    std = series.std()
    if std == 0:
        return pd.Series(center, index=series.index)
    return center + (series - mean) / std

# --- Main Public Function ---

@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core", market: str = "GLOBAL") -> dict:
    """
    Fetches and computes Relative Rotation Graph (RRG) data.
    Refactored to be self-contained and correct, fixing hallucinated function calls.
    """
    try:
        # 1. Select Universe and Benchmark based on 'market' parameter
        selected_universe = UNIVERSE_MAP.get(market)
        if not selected_universe:
            return {"ok": False, "error": f"Invalid market for RRG: {market}"}

        benchmark_ticker = BENCHMARK.get("US" if market != "GLOBAL" else "GLOBAL", "SPY")
        tickers_to_fetch = list(selected_universe.keys()) + [benchmark_ticker]

        # 2. Fetch weekly data using the correct IO module
        raw_data = data_io.fetch_batch(tuple(tickers_to_fetch))

        # Filter out failed fetches and resample to weekly
        weekly_closes = {}
        for ticker, df in raw_data.items():
            if df is not None and not df.empty:
                weekly_closes[ticker] = df['Close'].resample('W-FRI').last()

        df_weekly = pd.DataFrame(weekly_closes).dropna()

        if df_weekly.empty or benchmark_ticker not in df_weekly.columns:
            return {"ok": False, "error": f"Not enough data for benchmark {benchmark_ticker}"}

        # 3. Compute RRG Metrics
        bench_prices = df_weekly[benchmark_ticker]
        asset_prices = df_weekly.drop(columns=[benchmark_ticker])

        rs = _calc_rs_ratio(asset_prices, bench_prices)

        # JdK RS-Ratio (normalized)
        rs_ratio = rs.apply(_normalize_series, center=100.0)

        # JdK RS-Momentum (normalized rate of change of RS-Ratio)
        rs_momentum = rs_ratio.pct_change(periods=RRG_SMOOTHING).apply(_normalize_series, center=100.0)

        # 4. Format Output
        output = []
        for ticker in asset_prices.columns:
            if ticker not in rs_ratio.columns or ticker not in rs_momentum.columns:
                continue

            ratio_val = rs_ratio[ticker].iloc[-1]
            mom_val = rs_momentum[ticker].iloc[-1]

            # Determine Quadrant
            if ratio_val > 100 and mom_val > 100: quadrant = "Leading"
            elif ratio_val > 100 and mom_val < 100: quadrant = "Weakening"
            elif ratio_val < 100 and mom_val < 100: quadrant = "Lagging"
            else: quadrant = "Improving"

            output.append({
                "theme": selected_universe.get(ticker, ticker),
                "short": ticker,
                "rs_ratio": round(ratio_val, 2),
                "rs_momentum": round(mom_val, 2),
                "quadrant": quadrant,
                "tail": list(zip(rs_ratio[ticker].tail(12).tolist(), rs_momentum[ticker].tail(12).tolist()))
            })

        return {
            "ok": True,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "benchmark": benchmark_ticker,
            "market": market,
            "rrg": output,
        }

    except Exception as e:
        return {"ok": False, "error": f"An error occurred in fetch_rotation: {str(e)}"}
