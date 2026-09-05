# -*- coding: utf-8 -*-
"""Regression tests for audit fixes (VCP bands, breadth missing-data)."""
from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd

if "yfinance" not in sys.modules:
    _yf = types.ModuleType("yfinance")
    _yf.download = lambda *a, **k: None
    sys.modules["yfinance"] = _yf

import data_engine as eng
import pipeline


def _synth_vcp_df():
    n = 80
    idx = pd.bdate_range("2026-01-02", periods=n)
    close = pd.Series(np.linspace(100.0, 110.0, n), index=idx)
    # last 15 sessions dry volume vs SMA50
    vol = pd.Series(1_000_000.0, index=idx)
    vol.iloc[-15:] = 80_000.0
    high = close * 1.002
    low = close * 0.998
    df = pd.DataFrame({
        "Open": close,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": vol,
    }, index=idx)
    df["VOL_SMA50"] = df["Volume"].rolling(50, min_periods=10).mean()
    df["HIGH_52W"] = df["High"].rolling(60, min_periods=20).max()
    return df


def test_vcp_band_keeps_strongest_score():
    df = _synth_vcp_df()
    frame = eng._vcp_frame(df)
    assert frame is not None
    last = float(frame["score"].iloc[-1])
    # 15 dry days → 30; dry ratio ~8% → 25; off-peak tiny → 25; tight tiny → 20
    assert last >= 90, last
    assert bool(frame["is_vcp"].iloc[-1]) is True


def test_breadth_missing_ticker_not_bearish():
    idx = pd.bdate_range("2025-01-02", periods=220)
    close = pd.Series(np.linspace(50, 80, 220), index=idx)
    def _df():
        sma50 = close.rolling(50, min_periods=50).mean()
        sma200 = close.rolling(200, min_periods=200).mean()
        return pd.DataFrame({
            "Close": close, "SMA50": sma50, "SMA200": sma200,
            "Open": close, "High": close, "Low": close, "Volume": 1.0,
        }, index=idx)

    combined = {"A": _df(), "B": _df(), "C": _df()}
    # D exists as usable length but last day missing MA comparison → drop last close
    d = _df()
    d.loc[d.index[-1], ["Close", "SMA50", "SMA200"]] = np.nan
    combined["D"] = d
    ticker_meta = {t: {"market": "US"} for t in combined}
    rows, hist, _bear = pipeline._compute_breadth(combined, ticker_meta)
    assert rows, rows
    us = next(r for r in rows if r["code"] == "US")
    # 3 of 3 available names above MA50 → ~100, not 3/4=75
    assert us["ma50"] >= 99.0, us
    assert "coverage_pct" in us


def test_stock_check_refresh_uses_shared_gate():
    from pathlib import Path
    src = Path(__file__).with_name("backend.py").read_text(encoding="utf-8")
    marker = "@app.get(\"/api/stock_check\")"
    i = src.find(marker)
    assert i >= 0
    chunk = src[i:i + 500]
    assert "get_cache_clearer" in chunk
    assert "sck.clear_cache" in chunk
    assert "def stock_check_api" in chunk


def test_ttl_cache_timeout_does_not_steal_inflight():
    import threading
    import time
    from cache_utils import ttl_cache

    started = threading.Event()
    release = threading.Event()

    @ttl_cache(ttl_seconds=60, flight_wait=0.15)
    def slow(x):
        started.set()
        release.wait(timeout=8)
        return f"owner-{x}"

    def _cell(name):
        names = slow.__code__.co_freevars
        idx = names.index(name)
        return slow.__closure__[idx].cell_contents

    owner_result = []

    def run_owner():
        owner_result.append(slow("k"))

    t0 = threading.Thread(target=run_owner, name="owner")
    t0.start()
    assert started.wait(timeout=2)

    inflight = _cell("inflight")
    owner_ev = next(iter(inflight.values()))
    assert isinstance(owner_ev, threading.Event)

    def run_waiter():
        slow("k")

    t1 = threading.Thread(target=run_waiter, name="waiter")
    t1.start()
    time.sleep(0.35)  # past flight_wait; waiter is in fallback compute
    # waiter timed out and fell back; owner slot must still be present
    inflight = _cell("inflight")
    assert inflight, inflight
    assert next(iter(inflight.values())) is owner_ev

    release.set()
    t0.join(timeout=3)
    t1.join(timeout=3)
    assert owner_result == ["owner-k"]
    # owner finished and cleared its own slot
    assert not _cell("inflight")


def test_universe_listed_counts():
    from universe import LISTED_CORE, LISTED_FULL, LISTED_BY_MARKET, LISTED_US_ETF, LISTED_US_STOCK
    from constants import CORE_N
    from universe import UNIVERSE
    assert LISTED_FULL == 1071
    assert LISTED_CORE == 142
    assert LISTED_BY_MARKET["US"] == 754
    assert LISTED_US_STOCK == 619
    assert LISTED_US_ETF == 135
    assert sum(LISTED_BY_MARKET.values()) == 1071
    assert sum(min(CORE_N[m], LISTED_BY_MARKET[m]) for m in CORE_N) == 142
    us = list(UNIVERSE["US"])
    for name in ("RKLB", "IBIT", "IWO", "WSO"):
        assert name in UNIVERSE["US"]
        assert us.index(name) >= CORE_N["US"]
