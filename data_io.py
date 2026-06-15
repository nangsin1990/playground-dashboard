"""
Data I/O layer -- the only module that talks to the network (yfinance /
Yahoo Finance). Kept isolated from data_engine.py so the engine stays
unit-testable offline.

At ~900 tickers, one-request-per-ticker is impractical (minutes of latency
+ Yahoo rate-limit risk). `fetch_batch` uses `yf.download` with a ticker
list so yfinance threads many symbols per HTTP round, then splits the
resulting MultiIndex frame back into per-ticker DataFrames. Failed/empty
tickers are simply absent from the result -- callers treat that as "skip".

NOTE: TH tickers use the standard `.BK` suffix, which Yahoo Finance serves
for SET-listed names -- this is what stands in for the doc's "SET Scraper"
sync status, since it needs no separate scraper/credentials.
"""

from __future__ import annotations
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
from universe import BENCHMARK

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]
MIN_ROWS = 60
CACHE_TTL = 15 * 60


@ttl_cache(CACHE_TTL)
def fetch_batch(tickers: tuple[str, ...], period: str = "18mo") -> dict[str, pd.DataFrame]:
    """Download a batch of tickers in one yf.download call.
    Returns {ticker: OHLCV df}; tickers with no/insufficient data are
    simply absent (caller skips them)."""
    if not tickers:
        return {}
    try:
        raw = yf.download(
            list(tickers), period=period, interval="1d",
            group_by="ticker", auto_adjust=True, threads=True,
            progress=False,
        )
    except Exception:
        return {}
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
            except (KeyError, IndexError):
                continue
            if len(df) >= MIN_ROWS:
                out[t] = df
    else:
        # single-ticker download sometimes returns flat columns
        if len(tickers) == 1 and set(REQUIRED_COLS).issubset(raw.columns):
            df = raw[REQUIRED_COLS].dropna()
            if len(df) >= MIN_ROWS:
                out[tickers[0]] = df

    return out


def fetch_history(ticker: str, period: str = "18mo") -> pd.DataFrame | None:
    """Single-ticker convenience wrapper over fetch_batch (used for the
    optional benchmark fetch)."""
    return fetch_batch((ticker,), period=period).get(ticker)


def chunk(seq: list, size: int) -> list[list]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


@ttl_cache(CACHE_TTL)
def fetch_benchmark(market: str, period: str = "18mo") -> pd.DataFrame | None:
    sym = BENCHMARK.get(market)
    if not sym:
        return None
    return fetch_history(sym, period=period)


def clear_cache():
    fetch_batch.cache_clear()
    fetch_benchmark.cache_clear()


def sync_report(fetched: dict[str, dict[str, pd.DataFrame]], universe: dict) -> dict[str, dict]:
    """{market: {'ok': n_ok, 'total': n_total, 'failed': [tickers]}}"""
    report = {}
    for market, data in fetched.items():
        total = len(universe[market])
        ok = len(data)
        failed = [t for t in universe[market] if t not in data]
        report[market] = {"ok": ok, "total": total, "failed": failed}
    return report
