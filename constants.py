# @title

from __future__ import annotations
import logging
import time
import threading
_yfinance_lock = threading.Lock()
import concurrent.futures
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from constants import (
    FETCH_PERIOD, FETCH_TIMEOUT, FETCH_CHUNK_SIZE, FETCH_MIN_ROWS,
    FETCH_RETRY_MAX, FETCH_RETRY_BASE,
)

log = logging.getLogger("playground.data_io")

_cache: dict[tuple, dict] = {}
_lock  = threading.Lock()

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
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = ex.submit(_do_download, tickers_str, period, timeout)
            try:
                raw = future.result(timeout=timeout + 5)
            except concurrent.futures.TimeoutError:
                future.cancel()
                log.warning("TIMEOUT attempt %d tickers=%s", attempt + 1, tickers_str[:60])
                if attempt < FETCH_RETRY_MAX - 1:
                    time.sleep(FETCH_RETRY_BASE ** attempt)
                continue
            finally:
                # FIX: shutdown(wait=False, cancel_futures=True) — ไม่ค้าง
                ex.shutdown(wait=False, cancel_futures=True)

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

    if raw is None or raw.empty:
        return result

    # ── single ticker fallback ─────────────────────────────
    if not isinstance(raw.columns, pd.MultiIndex):
        if len(tickers) == 1:
            t = tickers[0]
            df = raw.copy()

            if "Close" in df.columns:
                for c in REQUIRED_COLS:
                    if c not in df.columns:
                        df[c] = np.nan

                df = df[REQUIRED_COLS].dropna(how="all")

                if len(df) >= FETCH_MIN_ROWS:
                    result[t] = df

        return result

    # ── MultiIndex handling ───────────────────────────────
    ticker_axis = raw.columns.get_level_values(-1)
    tickers_in_data = set(ticker_axis)

    level = raw.columns.names[1] if isinstance(raw.columns, pd.MultiIndex) else 1

    for t in tickers:
        try:
            if t not in tickers_in_data:
                continue

            try:
                df = raw.xs(t, axis=1, level=level).copy()
            except Exception:
                df = raw.xs(t, axis=1, level=1).copy()

            if df is None or df.empty:
                continue

            # ── must-have rule ───────────────────────────
            if "Close" not in df.columns:
                continue

            # ── fill missing columns instead of dropping ──
            for c in REQUIRED_COLS:
                if c not in df.columns:
                    df[c] = np.nan

            df = df[REQUIRED_COLS]

            result[t] = df

        except Exception as e:
            log.debug("parse failed %s: %s", t, e)

    ok_count = sum(1 for v in result.values() if v is not None)
    log.info("parse_result OK %d/%d", ok_count, len(tickers))

    return result

def fetch_batch(tickers: tuple[str, ...]) -> dict[str, Optional[pd.DataFrame]]:
    print("FETCH_BATCH CALLED", tickers)

    #tickers = tuple(t for t in tickers if isinstance(t, str) and len(t) > 0)
    tickers = tuple(t for t in tickers if isinstance(t, str) and len(t) > 0 and t != "BF.B")

    key = tuple(tickers)
    with _lock:
        if key in _cache:
            return _cache[key]

    tickers_str = ",".join(tickers)
    result: dict[str, Optional[pd.DataFrame]] = {t: None for t in tickers}

    with _yfinance_lock:
        raw = _download_with_retry(tickers_str, FETCH_PERIOD, FETCH_TIMEOUT)

    print("TICKERS =", tickers)
    print("RAW EMPTY =", raw is None or getattr(raw, "empty", False))
    print("RAW COLS =", getattr(raw, "columns", None))

    log.info("RAW SHAPE %s | tickers=%s", getattr(raw, "shape", None), tickers)

    if raw is not None and not raw.empty:
        result = _parse_result(raw, tickers)
    else:
        print("[RAW FAILED] no data from download")

    print("[BATCH OUTPUT]", len([v for v in result.values() if v is not None]))

    with _lock:
        _cache[key] = result

    return result


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
