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
import etf_board as eb
import market_regime as mr
import leadership as lb
import thematic_matrix as tm
import rotation_rrg as rrg
import economic_calendar as ec

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


@app.get("/api/etf")
def etf_board(refresh: bool = Query(False)):
    """Return ETF Board: screener, movers, volume surge, sector rotation, category summary."""
    if refresh:
        eb.fetch_etf_board.cache_clear()
    result = eb.fetch_etf_board()
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


@app.get("/api/regime")
def regime(
    breadth_us_ma50:  float = Query(None),
    breadth_us_ma200: float = Query(None),
    refresh: bool = Query(False),
):
    """Market Regime classification: Bull/Bear/Risk-On/Risk-Off/Correction/High-Vol."""
    if refresh:
        mr.compute_market_regime.cache_clear()
    result = mr.compute_market_regime(breadth_us_ma50, breadth_us_ma200)
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


@app.get("/api/leadership")
def leadership_board(
    mode: str = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    """Leadership Board: Overall · Top RS · Top Momentum · Near Breakout · Institutional · Volume · Trend Template."""
    import pipeline
    active = pipeline.active_universe(mode)
    combined, ticker_meta, _ = pipeline.fetch_universe(active)
    if not combined:
        return JSONResponse({"ok": False, "error": "No data"}, status_code=503)

    import data_engine as eng
    import pandas as pd
    blended = pd.Series({t: eng.blended_return(d["Close"]) for t, d in combined.items()})
    rs_now  = eng.rs_rating_table(blended)
    blended7= pd.Series({t: eng.blended_return(d["Close"].iloc[:-7]) for t, d in combined.items() if len(d) > 7})
    rs_7    = eng.rs_rating_table(blended7).reindex(rs_now.index).fillna(rs_now)

    ticker_signal = {}
    for t, d in combined.items():
        sig = eng.run_scanners(d)
        rolled, conf, count = eng.confluence_flags(sig)
        ticker_signal[t] = {
            "count":      int(count.iloc[-1]),
            "confluence": bool(conf.iloc[-1]),
            "rolled":     {k: bool(v.iloc[-1]) for k, v in rolled.items()},
        }

    result = lb.build_leadership_board(combined, ticker_meta, rs_now, rs_7, ticker_signal)
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)

@app.get("/api/thematic")
def thematic(
    mode: str = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    """Thematic Matrix: equal-weight 1D/1M/3M return per theme/sector with member list."""
    if refresh:
        tm.fetch_thematic.cache_clear()
    result = tm.fetch_thematic(mode)
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


@app.get("/api/rotation")
def rotation(
    mode: str = Query("core", pattern="^(core|full)$"),
    refresh: bool = Query(False),
):
    """Relative Rotation Graph (RRG): JdK RS-Ratio & RS-Momentum per theme vs benchmark."""
    if refresh:
        rrg.fetch_rotation.cache_clear()
    result = rrg.fetch_rotation(mode)
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


@app.get("/api/calendar")
def economic_calendar(refresh: bool = Query(False)):
    """Economic Calendar: FOMC · CPI · NFP · GDP · PCE · Earnings (next 90 days)."""
    if refresh:
        ec.fetch_economic_calendar.cache_clear()
    result = ec.fetch_economic_calendar()
    return JSONResponse(result, status_code=200 if result.get("ok") else 503)


# Static frontend — must be mounted LAST so /api/* routes take precedence.
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
