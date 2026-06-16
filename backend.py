"""
Stock Homework -- FastAPI backend
==================================
Run:   uvicorn backend:app --host 0.0.0.0 --port 8000
Share: ngrok http 8000

Endpoints:
  GET /                           → static/index.html (main dashboard)
  GET /api/status                 → lightweight health-check / uptime ping
  GET /api/dashboard              → full computed payload (cached 15 min)
      ?mode=core|full             → universe size
      &market=US|TH|HK|JP|KR|CN  → filter breadth/watchlist to one market
      &refresh=1                  → clear cache first
  GET /api/search                 → search watchlist by ticker/name/theme
      ?q=<query>&mode=core|full

mode=core  → ~126 liquid large/mid-cap names (fast, default)
mode=full  → ~913 names: full S&P 500 + Nasdaq-100 names not in S&P 500
             + Top-100 active US ETFs, plus SET100-level/HSI/Nikkei225/
             KOSPI200/CSI300 samples for TH/HK/JP/KR/CN.
             First call takes 1-3 min (batched yf.download); cached 15 min.
"""

from __future__ import annotations
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

app = FastAPI(title="Stock Homework Dashboard API", version="2.0")

# Allow any origin so ngrok public URL works with the local frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_boot_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")


@app.get("/api/status")
def status():
    """Lightweight health check / uptime ping for ngrok monitoring."""
    return JSONResponse({
        "status": "ok",
        "server": "Stock Homework Dashboard API v2.0",
        "booted": _boot_time,
        "now": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    })


@app.get("/api/dashboard")
def dashboard(
    mode: str = Query("core", pattern="^(core|full)$"),
    market: Optional[str] = Query(None, pattern="^(US|TH|HK|JP|KR|CN)$"),
    refresh: bool = Query(False),
):
    """Return the full computed dashboard payload.
    Optional `market` query param filters the Confluence Watchlist and
    returns only tickers from that market (breadth/stat-cards remain global).
    """
    if refresh:
        data_io.clear_cache()

    active = pipeline.active_universe(mode)
    combined, ticker_meta, fetch_results = pipeline.fetch_universe(active)
    result = pipeline.compute_dashboard(combined, ticker_meta, fetch_results, active)

    if market and result.get("ok"):
        result["watchlist"] = [w for w in result["watchlist"] if w.get("market") == market]
        # also expose which market filter is active so frontend can show it
        result["market_filter"] = market

    status_code = 200 if result.get("ok") else 503
    return JSONResponse(result, status_code=status_code)


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1),
    mode: str = Query("core", pattern="^(core|full)$"),
):
    """Search Confluence Watchlist by ticker symbol, company name, or theme.
    Re-uses the cached payload so no extra yfinance calls are made.
    """
    active = pipeline.active_universe(mode)
    combined, ticker_meta, fetch_results = pipeline.fetch_universe(active)
    result = pipeline.compute_dashboard(combined, ticker_meta, fetch_results, active)

    if not result.get("ok"):
        return JSONResponse(result, status_code=503)

    q_lower = q.lower()
    hits = [
        w for w in result["watchlist"]
        if q_lower in w["ticker"].lower()
        or q_lower in w["name"].lower()
        or q_lower in w["theme"].lower()
    ]
    return JSONResponse({"ok": True, "query": q, "results": hits, "total": len(hits)})




@app.get("/api/global")
def global_market(refresh: bool = Query(False)):
    """Return global market data: indices, futures, FX, commodities, bonds, VIX."""
    if refresh:
        gm.fetch_global_market.cache_clear()
    result = gm.fetch_global_market()
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)

# Static frontend — must be mounted LAST so /api/* routes take precedence.
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
