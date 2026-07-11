## playground-dashboard-main/backend.py

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

from fastapi import FastAPI, Query, HTTPException, Request, Depends
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

app = FastAPI(title="Playground Dashboard API", version="5.1-DRY")

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
# DEPENDENCY FOR CACHE REFRESH
# =========================
# ✨ REFACTOR: สร้าง Dependency เพื่อจัดการการล้าง Cache จากส่วนกลาง
# ทำให้ไม่ต้องเขียน logic `if refresh:` ซ้ำๆ ในทุก endpoint
cclass CacheRefresher:
    def __init__(self, refresh: bool = False):
        self.refresh = refresh

    def __call__(self, refresh: bool = False):
        if refresh:
            if hasattr(self.clear_function, "__name__"):
                log.info(f"Cache cleared for: {self.clear_function.__name__}")
            self.clear_function()

# =========================
# CACHE
# =========================
@ttl_cache(CACHE_TTL_DATA)
def _cached_dashboard(mode: str):
    active = pipeline.active_universe(mode)
    combined, ticker_meta, fetched = pipeline.fetch_universe(active)
    return pipeline.compute_dashboard(combined, ticker_meta, fetched, active)

# Leadership board ไม่ได้ใช้ refresh=True ใน API ดังนั้นไม่ต้องแก้
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
    # ✨ REFACTOR: ลบ refresh: bool ออก แล้วใช้ Dependency แทน
    cache: None = Depends(CacheRefresher(_cached_dashboard.cache_clear))
):
    result = _cached_dashboard(mode)
    if market and isinstance(result, dict):
        result = dict(result)
        if "watchlist" in result:
            result["watchlist"] = [
                w for w in result["watchlist"]
                if w.get("market") == market
            ]
    return _resp(result)

@app.get("/api/progress")
def progress_api():
    from pipeline import get_fetch_state
    return _resp(get_fetch_state())

@app.get("/api/regime")
def regime_api(
    breadth_us_ma50: Optional[float] = Query(None),
    breadth_us_ma200: Optional[float] = Query(None)
):
    import market_regime as rg
    try:
        return _resp(rg.compute_market_regime(
            breadth_us_ma50=breadth_us_ma50,
            breadth_us_ma200=breadth_us_ma200
        ))
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/search")
def search(q: str, mode: str = "core"):
    # ✨ REFACTOR: ทำให้ Search ใช้งานได้จริง
    if not q:
        return _resp({"ok": True, "query": q, "results": []})

    query = q.lower().strip()
    result = _cached_dashboard(mode)

    if not result or "watchlist" not in result:
        return _resp({"ok": False, "results": []})

    matches = [
        item for item in result["watchlist"]
        if query in item.get("ticker", "").lower() or
           query in item.get("name", "").lower() or
           query in item.get("theme", "").lower()
    ]

    return _resp({
        "ok": True,
        "query": q,
        "results": matches
    })

# =========================
# EXTRA API ENDPOINTS
# =========================

@app.get("/api/leadership")
def leadership_api(mode: str = Query("core")):
    # This endpoint doesn't use cache refresh, so it's unchanged.
    return _resp(_cached_leadership(mode))

@app.get("/api/global")
def global_api(
    refresh: bool = False,
    cache: None = Depends(CacheRefresher(gm.fetch_global_market.cache_clear))
):
    try:
        if hasattr(gm, "fetch_global_market"):
            return _resp(gm.fetch_global_market())
        return _resp({"ok": False, "error": "global_market endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/calendar")
def calendar_api(
    refresh: bool = False,
    cache: None = Depends(CacheRefresher(ec.fetch_economic_calendar.cache_clear))
):
    try:
        if hasattr(ec, "fetch_economic_calendar"):
            return _resp(ec.fetch_economic_calendar())
        return _resp({"ok": False, "error": "calendar endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/correlation")
def correlation_api(
    refresh: bool = False,
    cache: None = Depends(CacheRefresher(corr.fetch_correlation.cache_clear))
):
    try:
        if hasattr(corr, "fetch_correlation"):
            return _resp(corr.fetch_correlation())
        return _resp({"ok": False, "error": "correlation endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/etf")
def etf_api(
    refresh: bool = False,
    cache: None = Depends(CacheRefresher(eb.fetch_etf_board.cache_clear))
):
    try:
        if hasattr(eb, "fetch_etf_board"):
            return _resp(eb.fetch_etf_board())
        return _resp({"ok": False, "error": "etf endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/rotation")
def rotation_api(
    mode: str = Query("core"),
    market: Optional[str] = Query("GLOBAL"), # <--- CHANGE 1: รับ market parameter, default เป็น GLOBAL
    cache: CacheRefresher = Depends()
):
    try:
        # <--- CHANGE 2: Logic การล้าง Cache
        if cache.refresh:
            if hasattr(rrg.fetch_rotation, "cache_clear_key"):
                # เคลียร์ cache ของ key ที่ระบุเท่านั้น
                rrg.fetch_rotation.cache_clear_key(mode=mode, market=market)
            else:
                # Fallback ถ้า decorator ไม่ใช่ตัว custom ของเรา
                rrg.fetch_rotation.cache_clear()

        # <--- CHANGE 3: ส่ง market parameter ไปให้ engine
        return _resp(rrg.fetch_rotation(mode=mode, market=market))

    except Exception as e:
        log.exception("rotation_api failed for market=%s", market)
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/screener")
async def screener_api(
    request: Request,
    mode: str = Query("core"),
    refresh: bool = False
):
    try:
        params = dict(request.query_params)
        sort_by = params.get("sort_by", "ls")
        sort_desc = params.get("sort_desc", "true").lower() == "true"

        # ✨ REFACTOR: ใช้ Dependency แทนการเคลียร์ cache แบบ manual
        # การล้าง cache ของ screener จะทำผ่าน _get_all_rows ซึ่งเป็น source หลัก
        if refresh and hasattr(scr, "_get_all_rows") and hasattr(scr._get_all_rows, "cache_clear"):
            scr._get_all_rows.cache_clear()
            log.info("Screener cache cleared.")

        if hasattr(scr, "fetch_screener"):
            result = scr.fetch_screener(mode=mode, params=params, sort_by=sort_by, sort_desc=sort_desc)
            return _resp(result)
        return _resp({"ok": False, "error": "screener.fetch_screener endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/thematic")
def thematic_api(
    mode: str = Query("core"),
    refresh: bool = False,
    cache: None = Depends(CacheRefresher(tm.fetch_thematic.cache_clear))
):
    try:
        if hasattr(tm, "fetch_thematic"):
            return _resp(tm.fetch_thematic(mode=mode))
        return _resp({"ok": False, "error": "thematic endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

# --- STOCK DEEP DIVE APIS ---
@app.get("/api/technicals")
def technicals_api(ticker: str, refresh: bool = False):
    if refresh: ta.fetch_technicals.cache_clear_key(ticker=ticker)
    return _resp(ta.fetch_technicals(ticker=ticker))

@app.get("/api/sector_rs")
def sector_rs_api(ticker: str, theme: str = "", refresh: bool = False):
    if refresh: ta.fetch_sector_rs.cache_clear_key(ticker=ticker, theme=theme)
    return _resp(ta.fetch_sector_rs(ticker=ticker, theme=theme))

@app.get("/api/earnings")
def earnings_api(ticker: str, refresh: bool = False):
    if refresh: ta.fetch_earnings.cache_clear_key(ticker=ticker)
    return _resp(ta.fetch_earnings(ticker=ticker))

@app.get("/api/dividends")
def dividends_api(ticker: str, refresh: bool = False):
    if refresh: ta.fetch_dividends.cache_clear_key(ticker=ticker)
    return _resp(ta.fetch_dividends(ticker=ticker))

@app.get("/api/options_iv")
def options_iv_api(ticker: str, refresh: bool = False):
    if refresh: ta.fetch_options_iv.cache_clear_key(ticker=ticker)
    return _resp(ta.fetch_options_iv(ticker=ticker))

# =========================
# STATIC (MUST BE LAST)
# =========================
STATIC_DIR = Path(__file__).parent

app.mount(
    "/",
    StaticFiles(directory=str(STATIC_DIR), html=True),
    name="static"
)
