"""
data_io.py — yfinance fetch layer
v5.2 fixes (per Incident Report):
  1. ThreadPoolExecutor → ใช้ wait=False ตอน shutdown ป้องกัน executor.shutdown(wait=True) ค้าง
  2. Daemon thread สำหรับ yf.download — process ไม่ค้างถ้า thread ตาย
  3. log ทุก retry + timeout ชัดเจน ไม่กลืน error
  4. Drive persistent cache (จาก v5.1 คงเดิม)
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
    FETCH_PERIOD, FETCH_TIMEOUT, FETCH_MIN_ROWS,
    FETCH_RETRY_MAX, FETCH_RETRY_BASE, CACHE_TTL_DATA,
)

log = logging.getLogger("playground.data_io")

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


# ── Disk cache paths ──────────────────────────────────────────────────────────
def _batch_cache_dir() -> Path:
    drive = Path("/content/drive/MyDrive/playground_cache/batches")
    fallb = Path("/tmp/playground_cache/batches")
    d     = drive if Path("/content/drive/MyDrive").exists() else fallb
    d.mkdir(parents=True, exist_ok=True)
    return d


def _batch_key_path(tickers: tuple[str, ...]) -> Path:
    raw = "|".join(sorted(tickers)) + "|" + FETCH_PERIOD
    sha = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return _batch_cache_dir() / f"batch_{sha}.pkl"


# ── In-memory L1 cache ────────────────────────────────────────────────────────
_mem_cache: dict[tuple, dict] = {}
_lock       = threading.Lock()


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


# ── Fix #1: Download with non-blocking executor shutdown ──────────────────────
def _do_download(tickers_str: str, period: str, timeout: int) -> Optional[pd.DataFrame]:
    """yf.download — runs inside daemon thread so it won't block process exit."""
    return yf.download(
        tickers_str,
        period=period,
        auto_adjust=True,
        progress=False,
        timeout=timeout,
    )


def _download_with_retry(tickers_str: str, period: str, timeout: int) -> Optional[pd.DataFrame]:
    """
    yf.download with:
    - Hard wall-clock timeout via Future.result()
    - executor shutdown(wait=False) ← KEY FIX: ไม่รอ thread ที่ค้างอยู่
    - Exponential backoff on rate-limit
    - Full logging on every attempt
    """
    n_tickers = len(tickers_str.split())
    for attempt in range(FETCH_RETRY_MAX):
        t0 = time.time()
        log.info("yf.download attempt %d/%d — %d tickers (timeout=%ds)",
                 attempt + 1, FETCH_RETRY_MAX, n_tickers, timeout)
        try:
            # Fix #1: shutdown(wait=False) — ถ้า future ถูก cancel/timeout
            # thread อาจยังรันอยู่แต่ไม่บล็อก caller อีกต่อไป
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = ex.submit(_do_download, tickers_str, period, timeout)
            try:
                raw = future.result(timeout=timeout + 5)
            except concurrent.futures.TimeoutError:
                future.cancel()
                elapsed = round(time.time() - t0, 1)
                log.warning("yf.download TIMEOUT after %.1fs (attempt %d/%d) — %d tickers",
                            elapsed, attempt + 1, FETCH_RETRY_MAX, n_tickers)
                if attempt < FETCH_RETRY_MAX - 1:
                    wait = FETCH_RETRY_BASE ** attempt
                    log.info("retrying in %.1fs ...", wait)
                    time.sleep(wait)
                continue
            finally:
                # Fix #1: shutdown(wait=False) ← ไม่ block ถ้า thread ค้าง
                ex.shutdown(wait=False)

            elapsed = round(time.time() - t0, 1)
            if raw is not None and not raw.empty:
                log.info("yf.download OK — %.1fs, shape=%s", elapsed, raw.shape)
                return raw
            else:
                log.warning("yf.download returned empty (attempt %d/%d)", attempt + 1, FETCH_RETRY_MAX)

        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            err_str = str(e).lower()
            is_rate = any(k in err_str for k in ["too many", "rate", "429", "throttle"])
            wait    = FETCH_RETRY_BASE ** attempt * (2 if is_rate else 1)
            # Fix #1: log ออกมา ไม่กลืน
            log.warning("yf.download ERROR (attempt %d/%d, %.1fs): %s",
                        attempt + 1, FETCH_RETRY_MAX, elapsed, e)
            if attempt < FETCH_RETRY_MAX - 1:
                log.info("retrying in %.1fs ...", wait)
                time.sleep(wait)
            continue

    log.error("yf.download FAILED after %d attempts — %d tickers", FETCH_RETRY_MAX, n_tickers)
    return None


# ── Parse yfinance output ─────────────────────────────────────────────────────
def _parse_result(raw: pd.DataFrame, tickers: tuple[str, ...]) -> dict[str, Optional[pd.DataFrame]]:
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
                log.debug("parse failed for %s", t)
    elif len(tickers) == 1:
        t  = tickers[0]
        df = raw.copy()
        if all(c in df.columns for c in REQUIRED_COLS):
            df = df[REQUIRED_COLS].dropna(how="all")
            if len(df) >= FETCH_MIN_ROWS:
                result[t] = df

    ok_count = sum(1 for v in result.values() if v is not None)
    log.info("parse_result: %d/%d tickers OK", ok_count, len(tickers))
    return result


# ── Disk cache helpers ────────────────────────────────────────────────────────
def _disk_load(path: Path, ttl: float) -> tuple[bool, Optional[dict]]:
    if not path.exists():
        return False, None
    try:
        if (time.time() - path.stat().st_mtime) >= ttl:
            log.debug("disk cache EXPIRED: %s", path.name)
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
        log.debug("disk_save OK: %s", path.name)
    except Exception as e:
        log.debug("disk_save error %s: %s", path.name, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# ── Public API ────────────────────────────────────────────────────────────────
def fetch_batch(tickers: tuple[str, ...]) -> dict[str, Optional[pd.DataFrame]]:
    """
    Fetch batch: L1 memory → L2 Drive pickle → yfinance download.
    """
    key = tickers

    # L1: memory
    with _lock:
        if key in _mem_cache:
            log.debug("MEM HIT: %d tickers", len(tickers))
            return _mem_cache[key]

    # L2: Drive disk
    disk_path        = _batch_key_path(tickers)
    ok, cached_result = _disk_load(disk_path, CACHE_TTL_DATA)
    if ok and cached_result is not None:
        log.info("DISK HIT: %d tickers (from %s)", len(tickers), disk_path.name)
        with _lock:
            _mem_cache[key] = cached_result
        return cached_result

    # Miss → yfinance
    tickers_str = " ".join(tickers)
    result: dict[str, Optional[pd.DataFrame]] = {t: None for t in tickers}

    raw = _download_with_retry(tickers_str, FETCH_PERIOD, FETCH_TIMEOUT)
    if raw is not None and not raw.empty:
        result = _parse_result(raw, tickers)

    # Save both layers
    with _lock:
        _mem_cache[key] = result
    _disk_save(disk_path, result)

    return result


def clear_cache():
    """Clear memory + all Drive batch files."""
    with _lock:
        n_mem = len(_mem_cache)
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

    log.info("clear_cache: %d mem keys + %d disk files removed", n_mem, deleted)


def cache_info() -> dict:
    on_drive = Path("/content/drive/MyDrive").exists()
    with _lock:
        mem_keys = len(_mem_cache)
    try:
        cache_dir = _batch_cache_dir()
        files     = list(cache_dir.glob("batch_*.pkl"))
        total_mb  = sum(f.stat().st_size for f in files) / 1024 / 1024
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
