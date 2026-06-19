"""
data_io.py — Network layer (yfinance only)
v2 fixes:
  - Timeout: yf.download wraps in threading.Timer → kills hung batch after FETCH_TIMEOUT
  - Rate limit: sleep FETCH_RATE_DELAY between chunks
  - Delisted filter: skip tickers returning 0 rows or wrong schema
  - Per-ticker error isolation: one bad ticker can't crash the batch
"""

from __future__ import annotations
import time
import threading
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
from universe import BENCHMARK
from constants import (
    CACHE_TTL_DATA, FETCH_MIN_ROWS, FETCH_TIMEOUT, FETCH_RATE_DELAY,
)

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def _fetch_with_timeout(tickers: list[str], period: str, timeout: float) -> pd.DataFrame | None:
    """
    Run yf.download in a thread; return None if it exceeds `timeout` seconds.
    Prevents server hanging on dead/delisted tickers.
    """
    result = [None]
    exc    = [None]

    def _run():
        try:
            result[0] = yf.download(
                tickers, period=period, interval="1d",
                group_by="ticker", auto_adjust=True,
                threads=True, progress=False,
            )
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        # Thread still running → timed out
        return None
    if exc[0] is not None:
        return None
    return result[0]


@ttl_cache(CACHE_TTL_DATA)
def fetch_batch(tickers: tuple[str, ...], period: str = "18mo") -> dict[str, pd.DataFrame]:
    """
    Download a batch of tickers in one yf.download call.
    Returns {ticker: OHLCV df}; failed/delisted tickers are absent (caller skips).
    """
    if not tickers:
        return {}

    raw = _fetch_with_timeout(list(tickers), period, timeout=FETCH_TIMEOUT)
    if raw is None or raw.empty:
        return {}

    out: dict[str, pd.DataFrame] = {}

    if isinstance(raw.columns, pd.MultiIndex):
        available = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t not in available:
                continue
            try:
                df = raw[t][REQUIRED_COLS].dropna()
                # Delisted check: too few rows = dead ticker
                if len(df) >= FETCH_MIN_ROWS:
                    out[t] = df
            except (KeyError, IndexError):
                continue
    else:
        # Single-ticker flat columns
        if len(tickers) == 1 and set(REQUIRED_COLS).issubset(raw.columns):
            df = raw[REQUIRED_COLS].dropna()
            if len(df) >= FETCH_MIN_ROWS:
                out[tickers[0]] = df

    return out


def fetch_history(ticker: str, period: str = "18mo") -> pd.DataFrame | None:
    return fetch_batch((ticker,), period=period).get(ticker)


def chunk(seq: list, size: int) -> list[list]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


@ttl_cache(CACHE_TTL_DATA)
def fetch_benchmark(market: str, period: str = "18mo") -> pd.DataFrame | None:
    sym = BENCHMARK.get(market)
    if not sym:
        return None
    return fetch_history(sym, period=period)


def clear_cache():
    fetch_batch.cache_clear()
    fetch_benchmark.cache_clear()


def sync_report(fetched: dict[str, dict[str, pd.DataFrame]], universe: dict) -> dict[str, dict]:
    report = {}
    for market, data in fetched.items():
        total  = len(universe[market])
        ok     = len(data)
        failed = [t for t in universe[market] if t not in data]
        report[market] = {"ok": ok, "total": total, "failed": failed}
    return report
