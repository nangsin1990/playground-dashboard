# -*- coding: utf-8 -*-
"""HTTP / Yahoo helpers with retry, TTL cache, and per-source metrics. MWS v3.8.0"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import requests
import yfinance as yf

from config import CACHE_TTL_SEC
from utils import naive_frame

logger = logging.getLogger("mws.data")

_CACHE: Dict[str, Tuple[float, Any]] = {}
API_METRICS: Dict[str, Any] = {
    "calls": 0,
    "errors": 0,
    "latency_ms": [],
    "by_source": {},
}
_METRIC_LAT_CAP = 400


def metric_source_bucket(source: str) -> str:
    if not source:
        return "unknown"
    if source.startswith("yfinance_hist:"):
        return "yfinance_hist"
    if source.startswith("yfinance_peer"):
        return "yfinance_peer"
    return source


def _append_lat(bucket_list, latency_ms: float):
    bucket_list.append(latency_ms)
    extra = len(bucket_list) - _METRIC_LAT_CAP
    if extra > 0:
        del bucket_list[:extra]


def cache_get(key: str):
    item = _CACHE.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > CACHE_TTL_SEC:
        _CACHE.pop(key, None)
        return None
    return val


def cache_set(key: str, val: Any):
    _CACHE[key] = (time.time(), val)


def cache_size() -> int:
    return len(_CACHE)


def record_metric(source: str, latency_ms: float, ok: bool = True):
    source = metric_source_bucket(source)
    API_METRICS["calls"] += 1
    _append_lat(API_METRICS["latency_ms"], latency_ms)
    if not ok:
        API_METRICS["errors"] += 1
    bucket = API_METRICS["by_source"].setdefault(
        source, {"calls": 0, "errors": 0, "latency_ms": []}
    )
    bucket["calls"] += 1
    _append_lat(bucket["latency_ms"], latency_ms)
    if not ok:
        bucket["errors"] += 1


def request_with_retry(
    url: str,
    params: Optional[dict] = None,
    timeout: int = 12,
    max_retries: int = 3,
    source: str = "http",
) -> Optional[Any]:
    params = params or {}
    last_err = None
    for attempt in range(max_retries):
        t0 = time.time()
        try:
            r = requests.get(url, params=params, timeout=timeout)
            latency = (time.time() - t0) * 1000
            if r.status_code == 200:
                record_metric(source, latency, ok=True)
                return r.json()
            if r.status_code == 429:
                record_metric(source, latency, ok=False)
                wait = min(2 ** attempt * 1.5, 20)
                logger.warning("%s rate limited (429), retry in %.1fs", source, wait)
                time.sleep(wait)
                last_err = "RATE_LIMIT"
                continue
            if r.status_code in (401, 403):
                record_metric(source, latency, ok=False)
                logger.error("%s auth error %s", source, r.status_code)
                return {"_error": "AUTH_ERROR", "status": r.status_code}
            if r.status_code >= 500:
                record_metric(source, latency, ok=False)
                wait = min(2 ** attempt, 10)
                logger.warning("%s server %s, retry in %.1fs", source, r.status_code, wait)
                time.sleep(wait)
                last_err = f"SERVER_{r.status_code}"
                continue
            record_metric(source, latency, ok=False)
            return {"_error": f"HTTP_{r.status_code}"}
        except requests.Timeout as e:
            record_metric(source, (time.time() - t0) * 1000, ok=False)
            last_err = "TIMEOUT"
            logger.warning("%s timeout attempt %s: %s", source, attempt + 1, e)
            time.sleep(min(2 ** attempt, 8))
        except Exception as e:
            record_metric(source, (time.time() - t0) * 1000, ok=False)
            last_err = str(e)
            logger.warning("%s error attempt %s: %s", source, attempt + 1, e)
            time.sleep(min(2 ** attempt, 8))
    logger.error("%s failed after retries: %s", source, last_err)
    return {"_error": last_err or "UNKNOWN"}


def yf_history_retry(
    ticker_or_symbol,
    period: str = "1y",
    max_retries: int = 3,
    source: Optional[str] = None,
) -> pd.DataFrame:
    t = ticker_or_symbol if hasattr(ticker_or_symbol, "history") else yf.Ticker(ticker_or_symbol)
    src = source or (getattr(t, "ticker", None) or str(ticker_or_symbol))
    last_err = None
    for attempt in range(max_retries):
        t0 = time.time()
        try:
            hist = t.history(period=period, auto_adjust=True)
            latency = (time.time() - t0) * 1000
            if hist is not None and not hist.empty:
                record_metric(f"yfinance_hist:{src}", latency, ok=True)
                hist = naive_frame(hist)
                if "Close" in hist.columns:
                    hist = hist.dropna(subset=["Close"])
                return hist
            record_metric(f"yfinance_hist:{src}", latency, ok=False)
            last_err = "EMPTY"
        except Exception as e:
            record_metric(f"yfinance_hist:{src}", (time.time() - t0) * 1000, ok=False)
            last_err = str(e)
            logger.warning("%s history attempt %s failed: %s", src, attempt + 1, e)
        time.sleep(min(1.5 * (2 ** attempt), 8))
    logger.warning("%s history failed after retries: %s", src, last_err)
    return pd.DataFrame()
