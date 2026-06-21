"""
backend.py — FastAPI entry point
v5 fixes (per architecture review):
  1. HTTP 500 for unhandled exceptions (ไม่ return 200 อีกต่อไป)
  2. No silent except pass — errors logged properly
  3. /api/search ไม่ recompute dashboard ทั้งหมด — search จาก cache
  4. Dashboard generation cached ด้วย TTL cache
  5. No blocking time.sleep() ใน request path
"""

from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import data_io
import pipeline
import global_market       as gm
import etf_board           as eb
import market_regime       as mr
import leadership          as lb
import thematic_matrix     as tm
import rotation_rrg        as rrg
import economic_calendar   as ec
import correlation         as corr
import screener            as scr
import technical_analysis  as ta
import data_engine         as eng
import pandas              as pd

from cache_utils import ttl_cache
from constants   import CACHE_TTL_DATA

log = logging.getLogger("playground")

app = FastAPI(title="Playground Dashboard API", version="5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Fix 1: Proper HTTP 500 for unhandled exceptions ──────────────────────────
from fastapi import Request
from fastapi.responses import JSONResponse as _JSONR

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled exception on %s", request.url)
    return _JSONR(
        status_code=500,   # correct HTTP status (was wrongly 200)
        content={"ok": False, "error": str(exc)},
    )


_boot_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
_last_call: dict = {"time": None}


def _resp(data: dict) -> JSONResponse:
    headers = {"X-Data-Updated": data.get("updated", "")}
    # Fix 1b: 200 only when ok=True, 503 when data unavailable
    status = 200 if data.get("ok") else 503
    return JSONResponse(data, status_code=status, headers=headers)


# ── Fix 4: Cache dashboard computation (not just raw data) ───────────────────
@ttl_cache(CACHE_TTL_DATA)
def _cached_dashboard(mode: str) -> dict:
    """Full dashboard computation — cached at TTL level."""
    active = pipeline.active_universe(mode)
    combined, ticker_meta, fetched = pipeline.fetch_universe(active)
    return pipeline.compute_dashboard(combined, ticker_meta, fetched, active)


def _cached_leadership(mode: str) -> dict:
    """Leadership board — reuses cached dashboard data."""
    active = pipeline.active_universe(mode)
    combined, ticker_meta, _ = pipeline.fetch_universe(active)
    if not combined:
        return {"ok": False, "error": "No data", "updated": datetime.now().strftime("%d/%m/%Y %H:%M")}
    blended  = pd.Series({t: eng.blended_return(d["Close"]) for t, d in combined.items()})
    rs_now   = eng.rs_rating_per_market(combined, ticker_meta)
    blended7 = pd.Series({t: eng.blended_return(d["Close"].iloc[:-7])
                           for t, d in combined.items() if len(d) > 7})
    rs_7     = eng.rs_rating_table(blended7).reindex(rs_now.index).fillna(rs_now)
    ticker_signal = {}
    for t, d in combined.items():
        sig             = eng.run_scanners(d)
        rolled, conf, count = eng.confluence_flags(sig)
        ticker_signal[t] = {
            "count": int(count.iloc[-1]),
            "confluence": bool(conf.iloc[-1]),
            "rolled": {k: bool(v.iloc[-1]) for k, v in rolled.items()},
        }
    return lb.build_leadership_board(combined, ticker_meta, rs_now, rs_7, ticker_signal)


# ── Health / Status ───────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    _last_call["time"] = datetime.now().isoformat()
    return JSONResponse({"status": "ok", "ts": _last_call["time"]})


@app.get("/api/status")
def status():
    return JSONResponse({
        "status":    "ok",
        "version":   "5.0",
        "booted":    _boot_time,
        "now":       datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "last_call": _last_call["time"],
    })


# ── Dashboard ─────────────────────────────────────────────────────────────────
@app.get("/api/dashboard")
def dashboard(
    mode:    str = Query("core", pattern="^(core|full)$"),
    market:  Optional[str] = Query(None),
    refresh: bool = Query(False),
):
    _last_call["time"] = datetime.now().isoformat()

    # Fix 5: No blocking sleep — just clear cache flag, next call fetches fresh
    if refresh:
        _cached_dashboard.cache_clear()
        data_io.clear_cache()

    result = _cached_dashboard(mode)

    if market and result.get("ok"):
        result = dict(result)
        result["watchlist"]     = [w for w in result["watchlist"] if w.get("market") == market]
        result["market_filter"] = market

    return _resp(result)


# ── Fix 3: Search — no recompute, search from existing cache ─────────────────
@app.get("/api/search")
def search(
    q:    str = Query(..., min_length=1),
    mode: str = Query("core", pattern="^(core|full)$"),
):
    # Use cached dashboard — do NOT recompute
    result = _cached_dashboard(mode)

    if not result.get("ok"):
        return _resp({"ok": False, "error": "Data not ready yet. Load dashboard first.", "results": []})

    q_lower = q.lower()

    # Search across universe ticker_meta instead of just watchlist
    active     = pipeline.active_universe(mode)
    all_tickers = [
        {"ticker": t.split(".")[0], "name": name, "theme": theme, "market": mkt}
        for mkt, tk in active.items()
        for t, (name, theme) in tk.items()
    ]
    hits = [
        r for r in all_tickers
        if q_lower in r["ticker"].lower()
        or q_lower in r["name"].lower()
        or q_lower in r["theme"].lower()
    ][:20]

    return _resp({
        "ok":      True,
        "query":   q,
        "results": hits,
        "total":   len(hits),
        "updated": result.get("updated", ""),
    })


# ── Global Market ─────────────────────────────────────────────────────────────
@app.get("/api/global")
def global_market(refresh: bool = Query(False)):
    if refresh:
        gm.fetch_global_market.cache_clear()
    return _resp(gm.fetch_global_market())


# ── ETF Board ─────────────────────────────────────────────────────────────────
@app.get("/api/etf")
def etf_board(refresh: bool = Query(False)):
    if refresh:
        eb.fetch_etf_board.cache_clear()
    return _resp(eb.fetch_etf_board())


# ── Market Regime ─────────────────────────────────────────────────────────────
@app.get("/api/regime")
def regime(
    breadth_us_ma50:  Optional[float] = Query(None),
    breadth_us_ma200: Optional[float] = Query(None),
    refresh: bool = Query(False),
):
    if refresh:
        mr.compute_market_regime.cache_clear()
    return _resp(mr.compute_market_regime(breadth_us_ma50, breadth_us_ma200))


# ── Leadership Board ──────────────────────────────────────────────────────────
@app.get("/api/leadership")
def leadership_board(
    mode:    str  = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    if refresh:
        data_io.clear_cache()
        _cached_dashboard.cache_clear()
    return _resp(_cached_leadership(mode))


# ── Thematic Matrix ───────────────────────────────────────────────────────────
@app.get("/api/thematic")
def thematic(
    mode:    str  = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    if refresh:
        tm.fetch_thematic.cache_clear()
    return _resp(tm.fetch_thematic(mode))


# ── RRG Rotation ─────────────────────────────────────────────────────────────
@app.get("/api/rotation")
def rotation(
    mode:    str  = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    if refresh:
        rrg.fetch_rotation.cache_clear()
    return _resp(rrg.fetch_rotation(mode))


# ── Economic Calendar ─────────────────────────────────────────────────────────
@app.get("/api/calendar")
def economic_calendar(refresh: bool = Query(False)):
    if refresh:
        ec.fetch_economic_calendar.cache_clear()
    return _resp(ec.fetch_economic_calendar())


# ── Screener ──────────────────────────────────────────────────────────────────
@app.get("/api/screener")
def screener_ep(
    mode:      str            = Query("core", pattern="^(core|full)$"),
    rs_min:    float          = Query(0),
    rs_max:    float          = Query(99),
    trend_min: int            = Query(0),
    accum_min: float          = Query(-1.0),
    vol_min:   float          = Query(0.0),
    prox_max:  float          = Query(100.0),
    r1m_min:   Optional[float]= Query(None),
    r3m_min:   Optional[float]= Query(None),
    ls_min:    float          = Query(0.0),
    drs7_min:  Optional[float]= Query(None),
    market:    str            = Query(""),
    theme:     str            = Query(""),
    signal:    str            = Query(""),
    sort_by:   str            = Query("ls"),
    sort_desc: bool           = Query(True),
    limit:     int            = Query(100),
    refresh:   bool           = Query(False),
):
    if refresh:
        scr._get_all_rows.cache_clear()
    params = {
        "rs_min": rs_min, "rs_max": rs_max, "trend_min": trend_min,
        "accum_min": accum_min, "vol_min": vol_min, "prox_max": prox_max,
        "r1m_min": r1m_min, "r3m_min": r3m_min, "ls_min": ls_min,
        "drs7_min": drs7_min, "market": market, "theme": theme, "signal": signal,
    }
    return _resp(scr.fetch_screener(mode, params, sort_by, sort_desc, limit))


# ── Technical Analysis ────────────────────────────────────────────────────────
@app.get("/api/technicals")
def technicals(ticker: str = Query(..., min_length=1, max_length=10), refresh: bool = Query(False)):
    t = ticker.upper().strip()
    if refresh: ta.fetch_technicals.cache_clear()
    return _resp(ta.fetch_technicals(t))


@app.get("/api/sector_rs")
def sector_rs(ticker: str = Query(...), theme: str = Query(""), refresh: bool = Query(False)):
    t = ticker.upper().strip()
    if refresh: ta.fetch_sector_rs.cache_clear()
    return _resp(ta.fetch_sector_rs(t, theme))


@app.get("/api/earnings")
def earnings(ticker: str = Query(..., min_length=1, max_length=10), refresh: bool = Query(False)):
    t = ticker.upper().strip()
    if refresh: ta.fetch_earnings.cache_clear()
    return _resp(ta.fetch_earnings(t))


@app.get("/api/dividends")
def dividends(ticker: str = Query(..., min_length=1, max_length=10), refresh: bool = Query(False)):
    t = ticker.upper().strip()
    if refresh: ta.fetch_dividends.cache_clear()
    return _resp(ta.fetch_dividends(t))


@app.get("/api/options_iv")
def options_iv(ticker: str = Query(..., min_length=1, max_length=10), refresh: bool = Query(False)):
    t = ticker.upper().strip()
    if refresh: ta.fetch_options_iv.cache_clear()
    return _resp(ta.fetch_options_iv(t))


# ── Correlation Matrix ────────────────────────────────────────────────────────
@app.get("/api/correlation")
def correlation_matrix(refresh: bool = Query(False)):
    if refresh: corr.fetch_correlation.cache_clear()
    return _resp(corr.fetch_correlation())


# ── Static frontend (LAST) ────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
