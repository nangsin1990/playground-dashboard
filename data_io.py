"""
data_io.py — yfinance fetch layer
v3: exponential backoff + retry on rate-limit / timeout
    data_lag_minutes added to sync_report for frontend display
"""

from __future__ import annotations
import time
import threading
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from constants import (
    FETCH_PERIOD, FETCH_TIMEOUT, FETCH_CHUNK_SIZE, FETCH_MIN_ROWS,
    FETCH_RETRY_MAX, FETCH_RETRY_BASE,
)

_cache: dict[tuple, pd.DataFrame] = {}
_lock  = threading.Lock()

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _download_with_retry(tickers_str: str, period: str, timeout: int) -> Optional[pd.DataFrame]:
    """yf.download with exponential backoff on failure."""
    for attempt in range(FETCH_RETRY_MAX):
        try:
            raw = yf.download(
                tickers_str,
                period=period,
                auto_adjust=True,
                progress=False,
                timeout=timeout,
            )
            if raw is not None and not raw.empty:
                return raw
        except Exception as e:
            err_str = str(e).lower()
            is_rate_limit = any(k in err_str for k in ["too many", "rate", "429", "throttle"])
            wait = FETCH_RETRY_BASE ** attempt * (2 if is_rate_limit else 1)
            if attempt < FETCH_RETRY_MAX - 1:
                time.sleep(wait)
            continue
    return None


def fetch_batch(tickers: tuple[str, ...]) -> dict[str, Optional[pd.DataFrame]]:
    """Fetch a batch of tickers; returns dict of ticker → OHLCV DataFrame."""
    with _lock:
        key = tickers
        if key in _cache:
            return _cache[key]

    tickers_str = " ".join(tickers)
    result: dict[str, Optional[pd.DataFrame]] = {t: None for t in tickers}

    raw = _download_with_retry(tickers_str, FETCH_PERIOD, FETCH_TIMEOUT)

    if raw is None or raw.empty:
        with _lock:
            _cache[key] = result
        return result

    # Normalise multi-level vs single-level columns
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            try:
                df = raw.xs(t, axis=1, level=1).copy() if t in raw.columns.get_level_values(1) else None
                if df is not None and not df.empty and all(c in df.columns for c in REQUIRED_COLS):
                    df = df[REQUIRED_COLS].dropna(how="all")
                    if len(df) >= FETCH_MIN_ROWS:
                        result[t] = df
            except Exception:
                pass
    else:
        # Single ticker
        if len(tickers) == 1:
            t = tickers[0]
            df = raw.copy()
            if all(c in df.columns for c in REQUIRED_COLS):
                df = df[REQUIRED_COLS].dropna(how="all")
                if len(df) >= FETCH_MIN_ROWS:
                    result[t] = df

    with _lock:
        _cache[key] = result
    return result


def clear_cache():
    with _lock:
        _cache.clear()


def sync_report(fetch_results: dict, active: dict) -> dict:
    """
    Build per-market sync summary.
    Adds data_lag_minutes: yfinance is delayed ~15 min during market hours.
    """
    rows = []
    now  = datetime.now()
    for market, tickers in active.items():
        loaded = len(fetch_results.get(market, {}))
        total  = len(tickers)
        rows.append({
            "market":  market,
            "loaded":  loaded,
            "total":   total,
            "pct":     round(loaded / total * 100, 1) if total else 0,
        })
    return {
        "markets":          rows,
        "timestamp":        now.strftime("%d/%m/%Y %H:%M"),
        "data_lag_note":    "yfinance data delayed ~15 min during market hours",
        "data_lag_minutes": 15,
    }
