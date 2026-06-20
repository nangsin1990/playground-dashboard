"""
backend.py — FastAPI entry point
v2 fixes:
  - /api/status: adds heartbeat URL (monitoring)
  - ?refresh=1: await fresh data instead of returning stale
  - Added /api/health endpoint (lightweight, no DB/yfinance)
  - Added "last updated" header X-Data-Updated on every response
"""

from __future__ import annotations
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import data_io
import pipeline
import global_market as gm
import etf_board    as eb
import market_regime as mr
import leadership   as lb
import thematic_matrix as tm
import rotation_rrg    as rrg
import economic_calendar as ec
import correlation   as corr

app = FastAPI(title="Playground Dashboard API", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_boot_time  = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
_last_call  = {"time": None}  # simple heartbeat tracker


def _resp(data: dict, *, extra_headers: dict | None = None) -> JSONResponse:
    """Wrap JSON response with X-Data-Updated header for frontend display."""
    headers = {"X-Data-Updated": data.get("updated", "")}
    if extra_headers:
        headers.update(extra_headers)
    status = 200 if data.get("ok") else 503
    return JSONResponse(data, status_code=status, headers=headers)


def _refresh_and_fetch(cache_clear_fn, fetch_fn, *args, **kwargs):
    """
    Clear cache → sleep briefly → fetch fresh data.
    Ensures ?refresh=1 doesn't return stale cached result.
    """
    cache_clear_fn()
    time.sleep(0.1)   # small pause so cache key expires cleanly
    return fetch_fn(*args, **kwargs)


# ── Health / Status ───────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    """Ultra-lightweight ping (no yfinance). Use for monitoring heartbeat."""
    _last_call["time"] = datetime.now().isoformat()
    return JSONResponse({"status": "ok", "ts": _last_call["time"]})


@app.get("/api/status")
def status():
    return JSONResponse({
        "status":    "ok",
        "server":    "Playground Dashboard API v3.0",
        "booted":    _boot_time,
        "now":       datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "last_call": _last_call["time"],
        "health_url": "/api/health",  # use this for monitoring ping
    })


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.get("/api/dashboard")
def dashboard(
    mode:    str = Query("core", pattern="^(core|full)$"),
    market:  Optional[str] = Query(None, pattern="^(US|TH|HK|JP|KR|CN)$"),
    refresh: bool = Query(False),
):
    _last_call["time"] = datetime.now().isoformat()
    if refresh:
        data_io.clear_cache()
        time.sleep(0.2)  # let cache expire before re-fetch

    active                         = pipeline.active_universe(mode)
    combined, ticker_meta, fetched = pipeline.fetch_universe(active)
    result                         = pipeline.compute_dashboard(combined, ticker_meta, fetched, active)

    if market and result.get("ok"):
        result["watchlist"]     = [w for w in result["watchlist"] if w.get("market") == market]
        result["market_filter"] = market

    return _resp(result)


# ── Search ────────────────────────────────────────────────────────────────────
@app.get("/api/search")
def search(
    q:    str = Query(..., min_length=1),
    mode: str = Query("core", pattern="^(core|full)$"),
):
    active                         = pipeline.active_universe(mode)
    combined, ticker_meta, fetched = pipeline.fetch_universe(active)
    result                         = pipeline.compute_dashboard(combined, ticker_meta, fetched, active)

    if not result.get("ok"):
        return _resp(result)

    q_lower = q.lower()
    hits    = [w for w in result["watchlist"]
               if q_lower in w["ticker"].lower()
               or q_lower in w["name"].lower()
               or q_lower in w["theme"].lower()]
    return _resp({"ok": True, "query": q, "results": hits, "total": len(hits),
                  "updated": result["updated"]})


# ── Global Market ─────────────────────────────────────────────────────────────
@app.get("/api/global")
def global_market(refresh: bool = Query(False)):
    result = (_refresh_and_fetch(gm.fetch_global_market.cache_clear, gm.fetch_global_market)
              if refresh else gm.fetch_global_market())
    return _resp(result)


# ── ETF Board ─────────────────────────────────────────────────────────────────
@app.get("/api/etf")
def etf_board(refresh: bool = Query(False)):
    result = (_refresh_and_fetch(eb.fetch_etf_board.cache_clear, eb.fetch_etf_board)
              if refresh else eb.fetch_etf_board())
    return _resp(result)


# ── Market Regime ─────────────────────────────────────────────────────────────
@app.get("/api/regime")
def regime(
    breadth_us_ma50:  float = Query(None),
    breadth_us_ma200: float = Query(None),
    refresh: bool = Query(False),
):
    if refresh:
        mr.compute_market_regime.cache_clear()
        time.sleep(0.1)
    result = mr.compute_market_regime(breadth_us_ma50, breadth_us_ma200)
    return _resp(result)


# ── Leadership Board ──────────────────────────────────────────────────────────
@app.get("/api/leadership")
def leadership_board(
    mode:    str  = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    import data_engine as eng
    import pandas as pd

    if refresh:
        data_io.clear_cache()
        time.sleep(0.2)

    active                     = pipeline.active_universe(mode)
    combined, ticker_meta, _   = pipeline.fetch_universe(active)

    if not combined:
        return _resp({"ok": False, "error": "No data", "updated": datetime.now().strftime("%d/%m/%Y %H:%M")})

    blended  = pd.Series({t: eng.blended_return(d["Close"]) for t, d in combined.items()})
    rs_now   = eng.rs_rating_table(blended)
    blended7 = pd.Series({t: eng.blended_return(d["Close"].iloc[:-7])
                           for t, d in combined.items() if len(d) > 7})
    rs_7     = eng.rs_rating_table(blended7).reindex(rs_now.index).fillna(rs_now)

    ticker_signal = {}
    for t, d in combined.items():
        sig         = eng.run_scanners(d)
        rolled, conf, count = eng.confluence_flags(sig)
        ticker_signal[t] = {
            "count":      int(count.iloc[-1]),
            "confluence": bool(conf.iloc[-1]),
            "rolled":     {k: bool(v.iloc[-1]) for k, v in rolled.items()},
        }

    result = lb.build_leadership_board(combined, ticker_meta, rs_now, rs_7, ticker_signal)
    return _resp(result)


# ── Thematic Matrix ───────────────────────────────────────────────────────────
@app.get("/api/thematic")
def thematic(
    mode:    str  = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    result = (_refresh_and_fetch(tm.fetch_thematic.cache_clear, tm.fetch_thematic, mode)
              if refresh else tm.fetch_thematic(mode))
    return _resp(result)


# ── RRG Rotation ──────────────────────────────────────────────────────────────
@app.get("/api/rotation")
def rotation(
    mode:    str  = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    result = (_refresh_and_fetch(rrg.fetch_rotation.cache_clear, rrg.fetch_rotation, mode)
              if refresh else rrg.fetch_rotation(mode))
    return _resp(result)


# ── Economic Calendar ─────────────────────────────────────────────────────────
@app.get("/api/calendar")
def economic_calendar(refresh: bool = Query(False)):
    result = (_refresh_and_fetch(ec.fetch_economic_calendar.cache_clear, ec.fetch_economic_calendar)
              if refresh else ec.fetch_economic_calendar())
    return _resp(result)


# ── Correlation Matrix ────────────────────────────────────────────────────────
@app.get("/api/correlation")
def correlation_matrix(refresh: bool = Query(False)):
    result = (_refresh_and_fetch(corr.fetch_correlation.cache_clear, corr.fetch_correlation)
              if refresh else corr.fetch_correlation())
    return _resp(result)


# ── Static frontend (mount LAST) ───────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
