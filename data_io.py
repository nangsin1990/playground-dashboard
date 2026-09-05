"""
data_io.py — yfinance fetch layer
v5.3-patch:
  - _parse_result: robust MultiIndex via "Ticker" label + fallback level 1
  - _download_with_retry: กัน empty result (KR/CN)
  - fetch_batch: log RAW SHAPE สำหรับ debug
  - executor.shutdown(wait=False, cancel_futures=True)
"""

from __future__ import annotations
import logging
import time
import threading
import concurrent.futures
from datetime import datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from constants import (
    FETCH_PERIOD, FETCH_TIMEOUT, FETCH_CHUNK_SIZE, FETCH_MIN_ROWS,
    FETCH_RETRY_MAX, FETCH_RETRY_BASE, CACHE_TTL_DATA, DOWNLOAD_MAX_WORKERS,
)

log = logging.getLogger("playground.data_io")

_cache: dict[tuple, tuple[float, dict]] = {}
_lock  = threading.Lock()
_download_ex = concurrent.futures.ThreadPoolExecutor(
    max_workers=DOWNLOAD_MAX_WORKERS, thread_name_prefix="yf-dl"
)
_inflight: dict[tuple, threading.Event] = {}
_inflight_val: dict[tuple, dict] = {}
_CACHE_MAX_BATCHES = 200

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _do_download(tickers_str: str, period: str, timeout: int) -> Optional[pd.DataFrame]:
    return yf.download(
        tickers_str,
        period=period,
        auto_adjust=True,
        progress=False,
        timeout=timeout,
    )


def _download_with_retry(tickers_str: str, period: str, timeout: int) -> Optional[pd.DataFrame]:
    for attempt in range(FETCH_RETRY_MAX):
        try:
            future = _download_ex.submit(_do_download, tickers_str, period, timeout)
            try:
                raw = future.result(timeout=timeout + 5)
            except concurrent.futures.TimeoutError:
                future.cancel()
                log.warning("TIMEOUT attempt %d tickers=%s", attempt + 1, tickers_str[:60])
                if attempt < FETCH_RETRY_MAX - 1:
                    time.sleep(FETCH_RETRY_BASE ** attempt)
                continue

            # FIX: กัน empty hard fail (KR/CN)
            if raw is None or getattr(raw, "empty", False):
                log.warning("EMPTY yf.download result (attempt %d) tickers=%s",
                            attempt + 1, tickers_str[:60])
                if attempt < FETCH_RETRY_MAX - 1:
                    time.sleep(FETCH_RETRY_BASE ** attempt)
                continue

            return raw

        except Exception as e:
            err_str = str(e).lower()
            is_rate = any(k in err_str for k in ["too many", "rate", "429", "throttle"])
            wait = FETCH_RETRY_BASE ** attempt * (2 if is_rate else 1)
            log.warning("ERROR attempt %d: %s", attempt + 1, e)
            if attempt < FETCH_RETRY_MAX - 1:
                time.sleep(wait)
            continue

    return None


def _parse_result(raw: pd.DataFrame, tickers: tuple[str, ...]) -> dict[str, Optional[pd.DataFrame]]:
    result = {t: None for t in tickers}

    try:
        if raw is None or raw.empty:
            return result

        if not isinstance(raw.columns, pd.MultiIndex):
            if len(tickers) == 1:
                t  = tickers[0]
                df = raw.copy()
                if all(c in df.columns for c in REQUIRED_COLS):
                    df = df[REQUIRED_COLS].dropna(how="all")
                    if len(df) >= FETCH_MIN_ROWS:
                        result[t] = df
            return result

        lvl_names = [str(n).lower() if n is not None else "" for n in raw.columns.names]
        ticker_level = None
        if "ticker" in lvl_names:
            ticker_level = "Ticker" if "Ticker" in raw.columns.names else raw.columns.names[lvl_names.index("ticker")]
        else:
            last = {str(x) for x in raw.columns.get_level_values(-1)}
            first = {str(x) for x in raw.columns.get_level_values(0)}
            if "Close" in last:
                ticker_level = 0
            elif "Close" in first:
                ticker_level = 1
            else:
                ticker_level = 1
        try:
            ticker_axis = raw.columns.get_level_values(ticker_level)
        except Exception:
            ticker_axis = raw.columns.get_level_values(-1 if ticker_level == 0 else 0)

        tickers_in_data = set(ticker_axis)

        for t in tickers:
            if t not in tickers_in_data:
                continue
            try:
                try:
                    df = raw.xs(t, axis=1, level=ticker_level).copy()
                except Exception:
                    df = raw.xs(t, axis=1, level=1).copy()

                if df is None or df.empty:
                    continue
                if not all(c in df.columns for c in REQUIRED_COLS):
                    continue

                df = df[REQUIRED_COLS].dropna(how="all")
                if len(df) >= FETCH_MIN_ROWS:
                    result[t] = df

            except Exception as e:
                log.debug("parse failed %s: %s", t, e)

    except Exception as e:
        log.warning("parse_result fatal: %s", e)

    ok_count = sum(1 for v in result.values() if v is not None)
    log.info("parse_result OK %d/%d", ok_count, len(tickers))
    return result


def fetch_batch(tickers: tuple[str, ...]) -> dict[str, Optional[pd.DataFrame]]:
    key = tickers
    now = time.time()
    waiter = None
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < CACHE_TTL_DATA:
            return hit[1]
        ev = _inflight.get(key)
        if ev is None:
            ev = threading.Event()
            _inflight[key] = ev
        else:
            waiter = ev

    if waiter is not None:
        waiter.wait(timeout=FETCH_TIMEOUT + 15)
        with _lock:
            hit = _cache.get(key)
            if hit:
                return hit[1]
            stored = _inflight_val.get(key)
            if stored is not None:
                return stored
        return {t: None for t in tickers}

    tickers_str = " ".join(tickers)
    result: dict[str, Optional[pd.DataFrame]] = {t: None for t in tickers}

    raw = _download_with_retry(tickers_str, FETCH_PERIOD, FETCH_TIMEOUT)

    log.info("RAW SHAPE %s | tickers=%s chunk=%s", getattr(raw, "shape", None), tickers, FETCH_CHUNK_SIZE)

    if raw is not None and not raw.empty:
        result = _parse_result(raw, tickers)

    now = time.time()
    with _lock:
        _cache[key] = (now, result)
        _inflight_val[key] = result
        ev = _inflight.pop(key, None)
        expired = [k for k, (ts, _) in _cache.items() if (now - ts) >= CACHE_TTL_DATA]
        for k in expired:
            _cache.pop(k, None)
            _inflight_val.pop(k, None)
        if len(_cache) > _CACHE_MAX_BATCHES:
            oldest = sorted(_cache.items(), key=lambda item: item[1][0])
            for k, _ in oldest[: len(_cache) - _CACHE_MAX_BATCHES]:
                _cache.pop(k, None)
    if ev is not None:
        ev.set()
    return result


def lookup_ticker(ticker: str) -> Optional[pd.DataFrame]:
    """Return a cached OHLCV frame for ticker if any batch still holds it."""
    if not ticker:
        return None
    now = time.time()
    with _lock:
        for ts, batch in _cache.values():
            if (now - ts) >= CACHE_TTL_DATA:
                continue
            df = (batch or {}).get(ticker)
            if df is not None and not getattr(df, "empty", True):
                return df
    return None


def clear_cache():
    with _lock:
        _cache.clear()


def cache_info() -> dict:
    with _lock:
        n = len(_cache)
    return {"mem_batches": n, "drive_mounted": False, "disk_files": 0, "disk_mb": 0}


def sync_report(fetch_results: dict, active: dict) -> dict:
    rows = []
    now  = datetime.now()
    for market, tickers in active.items():
        loaded = len(fetch_results.get(market, {}))
        total  = len(tickers)
        rows.append({
            "market": market,
            "loaded": loaded,
            "total":  total,
            "pct":    round(loaded / total * 100, 1) if total else 0,
        })
    return {
        "markets":          rows,
        "timestamp":        now.strftime("%d/%m/%Y %H:%M"),
        "data_lag_note":    "⏱ yfinance data delayed ~15 min during market hours",
        "data_lag_minutes": 15,
    }
