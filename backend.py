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
# EXTRA API ENDPOINTS
# =========================

@app.get("/api/leadership")
def leadership_api(mode: str = Query("core")):
    return _resp(_cached_leadership(mode))


@app.get("/api/global")
def global_api(mode: str = Query("core")):
    try:
        # ⚡ FIX: เปลี่ยนไปเรียกฟังก์ชันที่ถูกต้องคือ `fetch_global_market`
        # ฟังก์ชันเดิมที่เรียก (build_global_market_board, get_global_market) ไม่มีอยู่จริงใน `global_market.py`
        if hasattr(gm, "fetch_global_market"):
            return _resp(gm.fetch_global_market())

        # Fallback เก่าเผื่อไว้ แต่ไม่น่าจะได้ใช้
        elif hasattr(gm, "build_global_market_board"):
            return _resp(gm.build_global_market_board())
        elif hasattr(gm, "get_global_market"):
            return _resp(gm.get_global_market())

        return _resp({"ok": False, "error": "global_market endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/calendar")
def calendar_api(refresh: bool = False): # ⚡ CHANGE: เพิ่มพารามิเตอร์ refresh
    try:
        # ⚡ CHANGE: เพิ่ม Logic การเคลียร์ Cache
        if refresh:
            if hasattr(ec, "fetch_economic_calendar") and hasattr(ec.fetch_economic_calendar, "cache_clear"):
                ec.fetch_economic_calendar.cache_clear()
                log.info("Economic calendar cache cleared.")

        # ⚡ CHANGE: แก้ไขให้เรียกฟังก์ชันที่ถูกต้อง คือ fetch_economic_calendar()
        if hasattr(ec, "fetch_economic_calendar"):
            return _resp(ec.fetch_economic_calendar())

        # ลบ Fallback ที่ไม่เคยถูกเรียกออกไป เพื่อความสะอาด
        return _resp({"ok": False, "error": "calendar endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/correlation")
def correlation_api(
    mode: str = Query("core"), 
    refresh: bool = False  # ⚡ CHANGE: เพิ่ม parameter `refresh`
):
    try:
        # ⚡ CHANGE: เพิ่ม Logic การเคลียร์ Cache เมื่อมีการร้องขอ
        if refresh:
            if hasattr(corr, "fetch_correlation") and hasattr(corr.fetch_correlation, "cache_clear"):
                corr.fetch_correlation.cache_clear()
                log.info("Correlation matrix cache cleared.")

        # ⚡ CHANGE: แก้ชื่อฟังก์ชันที่เรียกให้ถูกต้องเป็น `fetch_correlation()`
        # และลบ Fallback ที่ไม่จำเป็นออกไป
        if hasattr(corr, "fetch_correlation"):
            return _resp(corr.fetch_correlation())

        return _resp({"ok": False, "error": "correlation endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/etf")
def etf_api(mode: str = Query("core")):
    try:
        # ⚡ FIX: เปลี่ยนไปเรียกฟังก์ชันที่ถูกต้องคือ `fetch_etf_board`
        if hasattr(eb, "fetch_etf_board"):
            return _resp(eb.fetch_etf_board())

        # Fallback เก่าเผื่อไว้
        elif hasattr(eb, "build_etf_board"):
            return _resp(eb.build_etf_board())
        elif hasattr(eb, "get_etf_board"):
            return _resp(eb.get_etf_board())

        return _resp({"ok": False, "error": "etf endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/rotation")
def rotation_api(mode: str = Query("core"), refresh: bool = False):
    """
    Endpoint for Relative Rotation Graph (RRG) data.
    """
    try:
        # ⚡ FIX: Added refresh capability for consistency.
        if refresh:
            # Ensure the cache for the fetch function is cleared.
            if hasattr(rrg, "fetch_rotation") and hasattr(rrg.fetch_rotation, "cache_clear"):
                rrg.fetch_rotation.cache_clear()

        # ⚡ FIX: Changed to call the correct existing function `fetch_rotation`.
        # The previous functions `build_rotation_board` or `build_rrg` did not exist.
        if hasattr(rrg, "fetch_rotation"):
            return _resp(rrg.fetch_rotation(mode=mode))

        # Fallback if function is still not found (defensive coding)
        return _resp({"ok": False, "error": "rotation endpoint not implemented"})

    except Exception as e:
        log.exception("Error in /api/rotation for mode=%s", mode)
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/screener")
async def screener_api(
    request: Request, # ⚡ CHANGE: ใช้ Request object เพื่อดึง query parameters ทั้งหมด
    mode: str = Query("core"),
    refresh: bool = False
):
    """
    Handles screener requests by collecting all query parameters
    and passing them to the screener engine.
    """
    try:
        # ⚡ CHANGE: แปลง QueryParams ทั้งหมดเป็น dict เพื่อส่งให้ engine
        # ทำให้รองรับ filter ได้ทุกตัวที่ frontend ส่งมา
        params = dict(request.query_params)
        sort_by = params.get("sort_by", "ls")
        sort_desc = params.get("sort_desc", "true").lower() == "true"

        # ⚡ CHANGE: เพิ่ม Logic การล้าง Cache เฉพาะส่วน screener
        if refresh:
            # Import screener module here to access its functions
            import screener as scr
            # The data source for the screener is _get_all_rows
            if hasattr(scr, "_get_all_rows") and hasattr(scr._get_all_rows, "cache_clear"):
                 scr._get_all_rows.cache_clear()
                 log.info("Screener cache cleared.")

        # ⚡ CHANGE: เรียกใช้ฟังก์ชันที่ถูกต้อง (`fetch_screener`) พร้อมส่ง parameters ทั้งหมด
        # จากเดิมที่เรียก `build_screener` หรือ `run_screener` แบบไม่มีพารามิเตอร์เลย
        if hasattr(scr, "fetch_screener"):
            result = scr.fetch_screener(
                mode=mode, 
                params=params, 
                sort_by=sort_by, 
                sort_desc=sort_desc
            )
            return _resp(result)

        return _resp({"ok": False, "error": "screener.fetch_screener endpoint not implemented"})

    except Exception as e:
        log.exception("Screener API failed")
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/thematic")
def thematic_api(mode: str = Query("core")):
    try:
        # ⚡ FIX: เปลี่ยนไปเรียกฟังก์ชัน `fetch_thematic` ที่มีอยู่จริง และส่งพารามิเตอร์ `mode` เข้าไปด้วย
        if hasattr(tm, "fetch_thematic"):
            return _resp(tm.fetch_thematic(mode=mode))

        # Fallback เก่า (ไม่น่าจะได้ใช้แล้ว)
        elif hasattr(tm, "build_thematic_matrix"):
            return _resp(tm.build_thematic_matrix())
        elif hasattr(tm, "get_thematic_matrix"):
            return _resp(tm.get_thematic_matrix())

        return _resp({"ok": False, "error": "thematic endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/technicals")
def technicals_api(ticker: str, refresh: bool = False):
    try:
        # ✨ CHANGE: เพิ่มการรองรับ refresh=True
        if refresh and hasattr(ta, "fetch_technicals") and hasattr(ta.fetch_technicals, "cache_clear_key"):
            ta.fetch_technicals.cache_clear_key(ticker=ticker)
            log.info(f"Technicals cache cleared for {ticker}.")

        # ✨ CHANGE: แก้ไขชื่อฟังก์ชันให้ถูกต้องเป็น fetch_technicals
        if hasattr(ta, "fetch_technicals"):
            return _resp(ta.fetch_technicals(ticker))

        return _resp({"ok": False, "error": "technicals endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/sector_rs")
def sector_rs_api(ticker: str, theme: str = "", refresh: bool = False):
    try:
        # ✨ CHANGE: เพิ่มการรองรับ refresh=True
        if refresh and hasattr(ta, "fetch_sector_rs") and hasattr(ta.fetch_sector_rs, "cache_clear_key"):
            ta.fetch_sector_rs.cache_clear_key(ticker=ticker, theme=theme)
            log.info(f"Sector RS cache cleared for {ticker}.")

        # ✨ CHANGE: แทนที่ stub เดิมด้วยการเรียกฟังก์ชันจริง
        if hasattr(ta, "fetch_sector_rs"):
            return _resp(ta.fetch_sector_rs(ticker, theme))

        return _resp({"ok": False, "error": "sector_rs endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/earnings")
def earnings_api(ticker: str, refresh: bool = False):
    try:
        # ✨ CHANGE: เพิ่มการรองรับ refresh=True
        if refresh and hasattr(ta, "fetch_earnings") and hasattr(ta.fetch_earnings, "cache_clear_key"):
            ta.fetch_earnings.cache_clear_key(ticker=ticker)
            log.info(f"Earnings cache cleared for {ticker}.")

        # ✨ CHANGE: แทนที่ stub เดิมด้วยการเรียกฟังก์ชันจริง
        if hasattr(ta, "fetch_earnings"):
            return _resp(ta.fetch_earnings(ticker))

        return _resp({"ok": False, "error": "earnings endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/dividends")
def dividends_api(ticker: str, refresh: bool = False):
    try:
        # ✨ CHANGE: เพิ่มการรองรับ refresh=True
        if refresh and hasattr(ta, "fetch_dividends") and hasattr(ta.fetch_dividends, "cache_clear_key"):
            ta.fetch_dividends.cache_clear_key(ticker=ticker)
            log.info(f"Dividends cache cleared for {ticker}.")

        # ✨ CHANGE: แทนที่ stub เดิมด้วยการเรียกฟังก์ชันจริง
        if hasattr(ta, "fetch_dividends"):
            return _resp(ta.fetch_dividends(ticker))

        return _resp({"ok": False, "error": "dividends endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/options_iv")
def options_iv_api(ticker: str, refresh: bool = False):
    try:
        # ✨ CHANGE: เพิ่มการรองรับ refresh=True
        if refresh and hasattr(ta, "fetch_options_iv") and hasattr(ta.fetch_options_iv, "cache_clear_key"):
            ta.fetch_options_iv.cache_clear_key(ticker=ticker)
            log.info(f"Options IV cache cleared for {ticker}.")

        # ✨ CHANGE: แทนที่ stub เดิมด้วยการเรียกฟังก์ชันจริง
        if hasattr(ta, "fetch_options_iv"):
            return _resp(ta.fetch_options_iv(ticker))

        return _resp({"ok": False, "error": "options_iv endpoint not implemented"})
    except Exception as e:
        return _resp({"ok": False, "error": str(e)})

@app.get("/api/progress")
def progress_api():
    # ✨ NEW: Endpoint สำหรับให้ Frontend ดึงสถานะการโหลดข้อมูล
    from pipeline import get_fetch_state
    return _resp(get_fetch_state())


@app.get("/api/regime")
def regime_api(
    breadth_us_ma50: Optional[float] = Query(None),
    breadth_us_ma200: Optional[float] = Query(None)
):
    # ✨ NEW: Endpoint สำหรับคำนวณ Market Regime
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
    
# =========================
# STATIC (MUST BE LAST)
# =========================
STATIC_DIR = Path(__file__).parent

app.mount(
    "/",
    StaticFiles(directory=str(STATIC_DIR), html=True),
    name="static"
)
