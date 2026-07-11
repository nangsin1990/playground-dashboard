# FILE: rotation_rrg.py

from __future__ import annotations
import pandas as pd
from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA
import data_engine as eng

# ✨ FIX: นำเข้า Universe ทั้งหมดที่จำเป็นและถูกต้องตามไฟล์ universe.py ล่าสุด
from universe import (
    RRG_US_SECTORS,
    RRG_THAI_SECTORS,
    RRG_CRYPTO,
    RRG_COMMODITIES,
    RRG_GLOBAL_UNIVERSE,
    BENCHMARK,
    FLAGS
)

# สร้าง Mapping สำหรับ Universe และ Benchmark ที่จะใช้
UNIVERSE_MAP = {
    "US": RRG_US_SECTORS,
    "TH": RRG_THAI_SECTORS,
    "CRYPTO": RRG_CRYPTO,
    "COMMODITIES": RRG_COMMODITIES,
    "GLOBAL": RRG_GLOBAL_UNIVERSE,
}

# --- Main Public Function ---

@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core", market: str = "GLOBAL") -> dict:
    """
    Fetches and computes Relative Rotation Graph (RRG) data for a given market.
    This function is refactored to be simpler, more robust, and support multiple markets.

    Args:
        mode (str): The pipeline mode (e.g., 'core'). Currently unused but kept for API consistency.
        market (str): The market to analyze ('US', 'TH', 'CRYPTO', 'COMMODITIES', 'GLOBAL').

    Returns:
        dict: A dictionary containing the RRG data or an error message.
    """
    try:
        # 1. Validate Market and select Universe/Benchmark
        selected_universe = UNIVERSE_MAP.get(market)
        if not selected_universe:
            return {"ok": False, "error": f"Invalid market specified: {market}"}

        # ใช้ Benchmark ของตลาดนั้นๆ หรือใช้ 'GLOBAL' เป็น default ถ้าไม่มี
        benchmark_ticker = BENCHMARK.get(market, BENCHMARK.get("GLOBAL", "SPY"))

        # 2. Fetch Data
        # รวม benchmark เข้าไปใน list เพื่อดึงข้อมูลทีเดียว
        all_tickers = list(selected_universe.keys()) + [benchmark_ticker]
        df_prices = eng.fetch_data(all_tickers, "1y") # ดึงข้อมูล 1 ปีสำหรับ RRG

        if df_prices.empty or benchmark_ticker not in df_prices.columns:
            return {"ok": False, "error": f"Could not fetch data for benchmark {benchmark_ticker}"}

        # 3. Compute RRG
        # แยกข้อมูล benchmark ออกมา
        benchmark_prices = df_prices[benchmark_ticker]
        asset_prices = df_prices.drop(columns=[benchmark_ticker])

        # คำนวณ JdK RS-Ratio และ JdK RS-Momentum
        # ใช้ eng.rs_ratio และ eng.rs_momentum ที่มีอยู่แล้ว
        rs_ratio_series = eng.rs_ratio(asset_prices, benchmark_prices, normalize=True)
        rs_momentum_series = eng.rs_momentum(rs_ratio_series, normalize=True)

        # 4. Format Output
        output_data = []
        for ticker in asset_prices.columns:
            if ticker in rs_ratio_series and ticker in rs_momentum_series:
                flag_url = FLAGS.get(market.upper()) or FLAGS.get(ticker, "")
                output_data.append({
                    "ticker": ticker,
                    "name": selected_universe.get(ticker, ticker),
                    "rs_ratio": rs_ratio_series[ticker],
                    "rs_momentum": rs_momentum_series[ticker],
                    "flag": flag_url,
                })

        return {
            "ok": True,
            "market": market,
            "benchmark": benchmark_ticker,
            "data": output_data,
        }

    except Exception as e:
        return {"ok": False, "error": f"An unexpected error occurred in fetch_rotation: {str(e)}"}
