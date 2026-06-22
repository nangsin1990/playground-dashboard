"""
data_io.py — yfinance fetch layer
v5: Drive-persistent batch cache (survives Colab restarts)

Changes from v4:
  - _cache dict → replaced with Drive-backed pickle per batch key
  - clear_cache() now clears Drive files too
  - fetch_batch() checks Drive cache before calling yfinance
  - TTL = CACHE_TTL_DATA (same as dashboard cache, default 15 min)

Note: ถ้า Drive ไม่ได้ mount → fallback /tmp (in-session เท่านั้น)
"""

from __future__ import annotations
import hashlib
import logging
import pickle
import threading
import time
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from constants import (
    FETCH_PERIOD, FETCH_TIMEOUT, FETCH_CHUNK_SIZE, FETCH_MIN_ROWS,
    FETCH_RETRY_MAX, FETCH_RETRY_BASE, CACHE_TTL_DATA,
)

log = logging.getLogger("playground.data_io")

# ── Disk cache paths ─────────────────────────────────────────────────────────
def _batch_cache_dir() -> Path:
    drive = Path("/content/drive/MyDrive/playground_cache/batches")
    fallb = Path("/tmp/playground_cache/batches")
    d     = drive if Path("/content/drive/MyDrive").exists() else fallb
    d.mkdir(parents=True, exist_ok=True)
    return d


def _batch_key_path(tickers: tuple[str, ...]) -> Path:
    raw  = "|".join(sorted(tickers)) + "|" + FETCH_PERIOD
    sha  = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return _batch_cache_dir() / f"batch_{sha}.pkl"


# ── In-memory L1 cache (hot path, cleared on restart) ────────────────────────
_mem_cache: dict[tuple, dict] = {}
_lock       = threading.Lock()

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


# ── Download helpers ──────────────────────────────────────────────────────────
def _do_download(tickers_str: str, period: str, timeout: int) -> Optional[pd.DataFrame]:
    return yf.download(
        tickers_str,
        period=period,
        auto_adjust=True,
        progress=False,
        timeout=timeout,
    )


def _download_with_retry(tickers_str: str, period: str, timeout: int) -> Optional[pd.DataFrame]:
    """yf.download with exponential backoff + hard wall-clock timeout."""
    for attempt in range(FETCH_RETRY_MAX):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(_do_download, tickers_str, period, timeout)
                try:
                    raw = future.result(timeout=timeout + 5)
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    if attempt < FETCH_RETRY_MAX - 1:
                        time.sleep(FETCH_RETRY_BASE ** attempt)
                    continue

            if raw is not None and not raw.empty:
                return raw

        except Exception as e:
            err_str = str(e).lower()
            is_rate  = any(k in err_str for k in ["too many", "rate", "429", "throttle"])
            wait     = FETCH_RETRY_BASE ** attempt * (2 if is_rate else 1)
            if attempt < FETCH_RETRY_MAX - 1:
                time.sleep(wait)
            continue

    return None


def _parse_result(raw: pd.DataFrame, tickers: tuple[str, ...]) -> dict[str, Optional[pd.DataFrame]]:
    """Normalise multi-level or single-level yfinance output."""
    result: dict[str, Optional[pd.DataFrame]] = {t: None for t in tickers}

    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            try:
                lvl_vals = raw.columns.get_level_values(1)
                if t not in lvl_vals:
                    continue
                df = raw.xs(t, axis=1, level=1).copy()
                if all(c in df.columns for c in REQUIRED_COLS):
                    df = df[REQUIRED_COLS].dropna(how="all")
                    if len(df) >= FETCH_MIN_ROWS:
                        result[t] = df
            except Exception:
                pass
    elif len(tickers) == 1:
        t  = tickers[0]
        df = raw.copy()
        if all(c in df.columns for c in REQUIRED_COLS):
            df = df[REQUIRED_COLS].dropna(how="all")
            if len(df) >= FETCH_MIN_ROWS:
                result[t] = df

    return result


# ── Disk cache ops ────────────────────────────────────────────────────────────
def _disk_load(path: Path, ttl: float) -> tuple[bool, Optional[dict]]:
    if not path.exists():
        return False, None
    try:
        if (time.time() - path.stat().st_mtime) >= ttl:
            return False, None
        with path.open("rb") as f:
            return True, pickle.load(f)
    except Exception as e:
        log.debug("disk_load error %s: %s", path.name, e)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return False, None


def _disk_save(path: Path, value: dict) -> None:
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)
    except Exception as e:
        log.debug("disk_save error %s: %s", path.name, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# ── Public API ────────────────────────────────────────────────────────────────
def fetch_batch(tickers: tuple[str, ...]) -> dict[str, Optional[pd.DataFrame]]:
    """
    Fetch a batch of tickers.
    Cache priority: L1 memory → L2 Drive pickle → yfinance download
    """
    key = tickers

    # L1: memory (hot path)
    with _lock:
        if key in _mem_cache:
            return _mem_cache[key]

    # L2: Drive disk
    disk_path        = _batch_key_path(tickers)
    ok, cached_result = _disk_load(disk_path, CACHE_TTL_DATA)
    if ok and cached_result is not None:
        log.info("batch DISK HIT: %d tickers", len(tickers))
        with _lock:
            _mem_cache[key] = cached_result
        return cached_result

    # Miss → download
    log.info("batch MISS → downloading %d tickers from yfinance ...", len(tickers))
    tickers_str = " ".join(tickers)
    result: dict[str, Optional[pd.DataFrame]] = {t: None for t in tickers}

    raw = _download_with_retry(tickers_str, FETCH_PERIOD, FETCH_TIMEOUT)
    if raw is not None and not raw.empty:
        result = _parse_result(raw, tickers)

    # Save to both layers
    with _lock:
        _mem_cache[key] = result
    _disk_save(disk_path, result)

    return result


def clear_cache():
    """Clear both in-memory and Drive cache for all batches."""
    with _lock:
        _mem_cache.clear()

    cache_dir = _batch_cache_dir()
    deleted   = 0
    try:
        for f in cache_dir.glob("batch_*.pkl"):
            try:
                f.unlink(missing_ok=True)
                deleted += 1
            except Exception:
                pass
    except Exception as e:
        log.warning("clear_cache disk error: %s", e)

    log.info("clear_cache: removed %d batch files", deleted)


def cache_info() -> dict:
    """Summary of current data_io cache state."""
    cache_dir  = _batch_cache_dir()
    on_drive   = Path("/content/drive/MyDrive").exists()
    mem_keys   = 0
    with _lock:
        mem_keys = len(_mem_cache)
    try:
        files    = list(cache_dir.glob("batch_*.pkl"))
        total_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
        return {
            "drive_mounted": on_drive,
            "cache_dir":     str(cache_dir),
            "disk_files":    len(files),
            "disk_mb":       round(total_mb, 2),
            "mem_batches":   mem_keys,
        }
    except Exception as e:
        return {"drive_mounted": on_drive, "error": str(e)}


def sync_report(fetch_results: dict, active: dict) -> dict:
    """Build per-market sync summary."""
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
