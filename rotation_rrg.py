# FILE: rotation_rrg.py

from __future__ import annotations
from datetime import datetime
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
import data_engine as eng
from universe import RRG_US_SECTORS, RRG_GLOBAL_UNIVERSE, RRG_US_THEMES, BENCHMARK

CACHE_TTL_DATA = 15 * 60

UNIVERSE_MAP = {
    "GLOBAL": RRG_GLOBAL_UNIVERSE,
    "US_SECTORS": RRG_US_SECTORS,
    "US_THEMES": RRG_US_THEMES,
}

def _fetch_weekly_prices(tickers: list[str], period: str = "2y") -> pd.DataFrame | None:
    """Fetches daily prices and resamples to weekly ('W-FRI')."""
    try:
        raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True, progress=False, timeout=30)
        if raw is None or raw.empty:
            return None
        weekly = raw['Close'].resample('W-FRI').last().dropna(how='all', thresh=2)
        return weekly
    except Exception:
        return None

@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core", market: str = "GLOBAL") -> dict:
    """
    Fetches and computes RRG data for a specified market universe.
    """
    try:
        selected_universe = UNIVERSE_MAP.get(market)
        if not selected_universe:
            return {"ok": False, "error": f"Invalid market specified for RRG: {market}"}

        benchmark_ticker = BENCHMARK.get(market, "VT")
        all_tickers = list(selected_universe.keys()) + [benchmark_ticker]

        df_weekly = _fetch_weekly_prices(all_tickers)
        if df_weekly is None or benchmark_ticker not in df_weekly.columns or df_weekly[benchmark_ticker].isnull().all():
            return {"ok": False, "error": f"Could not fetch valid weekly data for benchmark {benchmark_ticker}"}

        valid_tickers = [t for t in selected_universe.keys() if t in df_weekly.columns and df_weekly[t].notna().sum() > 10]
        if not valid_tickers:
            return {"ok": False, "error": "No assets with sufficient historical data found in the selected universe."}

        rrg_metrics = eng.calculate_rrg_metrics(df_weekly, valid_tickers, benchmark_ticker)
        if not rrg_metrics:
             return {"ok": False, "error": "RRG computation failed. Not enough historical data for comparison."}

        rrg_list = []
        for ticker, data in rrg_metrics.items():
            rrg_list.append({
                "theme": selected_universe.get(ticker, ticker),
                "short": ticker,
                "quadrant": data["quadrant"],
                "rs_ratio": data["jrs"],
                "rs_momentum": data["jmo"],
                "tail": data["tail"],
                "avg_rs": None
            })

        return {
            "ok": True,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "market": market,
            "benchmark": benchmark_ticker,
            "rrg": rrg_list,
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": f"An unexpected error occurred in fetch_rotation: {str(e)}", "trace": traceback.format_exc()}
