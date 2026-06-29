# @title
#"""
#backend.py — CACHE_TTl correction
#"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# =========================
# SAFE IMPORTS
# =========================
try:
    from cache_utils import ttl_cache
except Exception:
    def ttl_cache(*args, **kwargs):
        def wrapper(f):
            return f
        return wrapper

try:
    from constants import CACHE_TTL_DATA
except Exception:
    CACHE_TTL_DATA = 300

# core modules
try:
    import data_io
    import pipeline
    import global_market as gm
    import etf_board as eb
    import market_regime as mr
    import leadership as lb
    import thematic_matrix as tm
    import rotation_rrg as rrg
    import economic_calendar as ec
    import correlation as corr
    import screener as scr
    import technical_analysis as ta
    import data_engine as eng
    import pandas as pd
except Exception as e:
    print("IMPORT WARNING:", e)

# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("playground")

app = FastAPI(title="Playground Dashboard API", version="5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# STATE
# =========================
_boot_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
_last_call = {"time": None}

# =========================
# EXCEPTION HANDLER
# =========================
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled exception: %s", request.url)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": str(exc)},
    )

def _resp(data: dict):
    return JSONResponse(
        data,
        status_code=200 if data.get("ok", True) else 503
    )

# =========================
# CACHE
# =========================
@ttl_cache(CACHE_TTL_DATA)
def _cached_dashboard(mode: str):
    active = pipeline.active_universe(mode)
    combined, ticker_meta, fetched = pipeline.fetch_universe(active)
    return pipeline.compute_dashboard(combined, ticker_meta, fetched, active)

def _cached_leadership(mode: str):
    active = pipeline.active_universe(mode)
    combined, ticker_meta, _ = pipeline.fetch_universe(active)

    if not combined:
        return {"ok": False, "error": "No data"}

    blended = pd.Series({
        t: eng.blended_return(d["Close"])
        for t, d in combined.items()
    })

    rs_now = eng.rs_rating_per_market(combined, ticker_meta)

    return lb.build_leadership_board(
        combined, ticker_meta, rs_now, rs_now, {}
    )

# =========================
# API
# =========================
@app.get("/api/health")
def health():
    _last_call["time"] = datetime.now().isoformat()
    return {"status": "ok"}

@app.get("/api/status")
def status():
    return {
        "status": "ok",
        "booted": _boot_time,
        "now": datetime.now().isoformat()
    }

@app.get("/api/dashboard")
def dashboard(
    mode: str = Query("core"),
    market: Optional[str] = None,
    refresh: bool = False
):
    if refresh:
        _cached_dashboard.cache_clear()

    result = _cached_dashboard(mode)

    if market and isinstance(result, dict):
        result = dict(result)
        if "watchlist" in result:
            result["watchlist"] = [
                w for w in result["watchlist"]
                if w.get("market") == market
            ]

    return _resp(result)

@app.get("/api/search")
def search(q: str, mode: str = "core"):
    result = _cached_dashboard(mode)

    if not result:
        return _resp({"ok": False, "results": []})

    return _resp({
        "ok": True,
        "query": q,
        "results": []
    })

# =========================
# STATIC (MUST BE LAST)
# =========================
STATIC_DIR = Path(__file__).parent

app.mount(
    "/",
    StaticFiles(directory=str(STATIC_DIR), html=True),
    name="static"
)
