# FILE: rotation_rrg.py

from __future__ import annotations
from datetime import datetime

import pandas as pd

from cache_utils import ttl_cache
import data_engine as eng
import data_io
from universe import RRG_US_SECTORS, RRG_GLOBAL_UNIVERSE, RRG_US_THEMES, BENCHMARK

CACHE_TTL_DATA = 15 * 60

UNIVERSE_MAP = {
    "GLOBAL": RRG_GLOBAL_UNIVERSE,
    "US_SECTORS": RRG_US_SECTORS,
    "US_THEMES": RRG_US_THEMES,
}


def _label(meta) -> str:
    if isinstance(meta, (tuple, list)):
        return str(meta[0])
    return str(meta or "")


def _weekly_from_batch(tickers: list[str]) -> pd.DataFrame | None:
    raw = data_io.fetch_batch(tuple(tickers))
    series = {}
    for t, df in (raw or {}).items():
        if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
            continue
        s = df["Close"].copy()
        s.index = pd.to_datetime(s.index, errors="coerce")
        s = s[~s.index.isna()].dropna()
        if s.empty:
            continue
        series[t] = s.resample("W-FRI").last()
    if len(series) < 2:
        return None
    out = pd.DataFrame(series).ffill().dropna(how="all")
    return out if not out.empty else None


@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core", market: str = "GLOBAL") -> dict:
    try:
        selected_universe = UNIVERSE_MAP.get(market)
        if not selected_universe:
            return {"ok": False, "error": f"Invalid market specified for RRG: {market}"}

        benchmark_ticker = BENCHMARK.get(market, "SPY")
        all_tickers = list(dict.fromkeys(list(selected_universe.keys()) + [benchmark_ticker]))

        df_weekly = _weekly_from_batch(all_tickers)
        if df_weekly is None:
            return {"ok": False, "error": f"Could not fetch weekly prices for {market}"}

        if benchmark_ticker not in df_weekly.columns or df_weekly[benchmark_ticker].dropna().empty:
            fallback = "SPY" if "SPY" in df_weekly.columns else df_weekly.columns[0]
            benchmark_ticker = str(fallback)

        valid_tickers = [
            t for t in selected_universe.keys()
            if t in df_weekly.columns and df_weekly[t].notna().sum() > 10
        ]
        if not valid_tickers:
            return {"ok": False, "error": "No assets with sufficient historical data found in the selected universe."}

        rrg_metrics = eng.calculate_rrg_metrics(df_weekly, valid_tickers, benchmark_ticker)
        if not rrg_metrics:
            return {"ok": False, "error": "RRG computation failed. Not enough historical data for comparison."}

        rrg_list = []
        for ticker, data in rrg_metrics.items():
            tail = data.get("tail") or []
            avg_rs = round(sum(p[0] for p in tail) / len(tail), 2) if tail else data["jrs"]
            rrg_list.append({
                "theme": _label(selected_universe.get(ticker, ticker)),
                "short": ticker,
                "quadrant": data["quadrant"],
                "rs_ratio": data["jrs"],
                "rs_momentum": data["jmo"],
                "tail": tail,
                "avg_rs": avg_rs,
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
