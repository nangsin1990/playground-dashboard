# FILE: backend.py
# (โค้ดทั้งหมดของคุณตั้งแต่ต้นจนเกือบสุดท้ายยังอยู่เหมือนเดิมทุกประการ)

from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable
from fastapi import FastAPI, Query, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
try:
    from cache_utils import ttl_cache
except Exception:
    def ttl_cache(*args, **kwargs):
        def wrapper(f): return f
        return wrapper
try:
    from constants import CACHE_TTL_DATA
except Exception:
    CACHE_TTL_DATA = 300
try:
    import data_io, pipeline, global_market as gm, etf_board as eb, market_regime as mr, leadership as lb, thematic_matrix as tm, rotation_rrg as rrg, economic_calendar as ec, correlation as corr, screener as scr, technical_analysis as ta, data_engine as eng, pandas as pd
except Exception as e:
    print("IMPORT WARNING:", e)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("playground")

app = FastAPI(title="Playground Dashboard API", version="6.0-Refactored")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_boot_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled exception at URL: %s", request.url)
    return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

def _resp(data: dict):
    return JSONResponse(data, status_code=200 if data.get("ok", True) else 503)

def get_cache_clearer(clear_func: Callable[[], None]):
    def dependency(refresh: bool = False):
        if refresh:
            try:
                log.info(f"Cache cleared for: {clear_func.__self__.__name__}")
                clear_func()
            except Exception:
                log.warning(f"Could not clear cache for a function.")
    return dependency

@ttl_cache(CACHE_TTL_DATA)
def _cached_dashboard(mode: str):
    active = pipeline.active_universe(mode)
    combined, ticker_meta, fetched = pipeline.fetch_universe(active)
    return pipeline.compute_dashboard(combined, ticker_meta, fetched, active)

@app.get("/api/health")
def health(): return {"status": "ok"}

@app.get("/api/status")
def status(): return {"status": "ok", "booted": _boot_time, "now": datetime.now().isoformat()}

@app.get("/api/dashboard")
def dashboard(mode: str = Query("core"), market: Optional[str] = None, _: None = Depends(get_cache_clearer(_cached_dashboard.cache_clear))):
    result = _cached_dashboard(mode)
    if market and isinstance(result, dict) and "watchlist" in result:
        result["watchlist"] = [w for w in result["watchlist"] if w.get("market") == market]
    return _resp(result)

@app.get("/api/progress")
def progress_api():
    from pipeline import get_fetch_state
    return _resp(get_fetch_state())

@app.get("/api/regime")
def regime_api(breadth_us_ma50: Optional[float] = None, breadth_us_ma200: Optional[float] = None):
    return _resp(mr.compute_market_regime(breadth_us_ma50=breadth_us_ma50, breadth_us_ma200=breadth_us_ma200))

@app.get("/api/search")
def search(q: str, mode: str = "core"):
    if not q: return _resp({"ok": True, "query": q, "results": []})
    query_lower = q.lower().strip()
    all_data = _cached_dashboard(mode)
    if not all_data.get("ok"): return _resp({"ok": False, "results": [], "error": "Dashboard data not available"})
    matches = [
        item for item in all_data.get("watchlist", [])
        if query_lower in item.get("ticker", "").lower() or query_lower in item.get("name", "").lower() or query_lower in item.get("theme", "").lower()
    ]
    return _resp({"ok": True, "query": q, "results": matches[:20]})

@app.get("/api/leadership")
def leadership_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(lb.build_leadership_board.cache_clear))):
    return _resp(lb.build_leadership_board(mode=mode))

@app.get("/api/global")
def global_api(_: None = Depends(get_cache_clearer(gm.fetch_global_market.cache_clear))): return _resp(gm.fetch_global_market())

@app.get("/api/calendar")
def calendar_api(_: None = Depends(get_cache_clearer(ec.fetch_economic_calendar.cache_clear))): return _resp(ec.fetch_economic_calendar())

@app.get("/api/correlation")
def correlation_api(_: None = Depends(get_cache_clearer(corr.fetch_correlation.cache_clear))): return _resp(corr.fetch_correlation())

@app.get("/api/etf")
def etf_api(_: None = Depends(get_cache_clearer(eb.fetch_etf_board.cache_clear))): return _resp(eb.fetch_etf_board())

@app.get("/api/rotation")
def rotation_api(mode: str = Query("core"), market: str = Query("GLOBAL"), _: None = Depends(get_cache_clearer(rrg.fetch_rotation.cache_clear))):
    return _resp(rrg.fetch_rotation(mode=mode, market=market))

@app.get("/api/screener")
def screener_api(request: Request, mode: str = Query("core"), _: None = Depends(get_cache_clearer(scr._get_all_rows.cache_clear))):
    params, sort_by, sort_desc = dict(request.query_params), params.get("sort_by", "ls"), params.get("sort_desc", "true").lower() == "true"
    return _resp(scr.fetch_screener(mode=mode, params=params, sort_by=sort_by, sort_desc=sort_desc))

@app.get("/api/thematic")
def thematic_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(tm.fetch_thematic.cache_clear))): return _resp(tm.fetch_thematic(mode=mode))

@app.get("/api/technicals")
def technicals_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_technicals.cache_clear))): return _resp(ta.fetch_technicals(ticker=ticker))

@app.get("/api/sector_rs")
def sector_rs_api(ticker: str, theme: str = "", _: None = Depends(get_cache_clearer(ta.fetch_sector_rs.cache_clear))): return _resp(ta.fetch_sector_rs(ticker=ticker, theme=theme))

@app.get("/api/earnings")
def earnings_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_earnings.cache_clear))): return _resp(ta.fetch_earnings(ticker=ticker))

@app.get("/api/dividends")
def dividends_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_dividends.cache_clear))): return _resp(ta.fetch_dividends(ticker=ticker))

@app.get("/api/options_iv")
def options_iv_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_options_iv.cache_clear))): return _resp(ta.fetch_options_iv(ticker=ticker))


# ============================[ 🚨 การเปลี่ยนแปลงสำคัญ 🚨 ]============================
#
# เราจะตั้งค่า "เสาอากาศ" 2 อันให้ FastAPI ครับ
# ลำดับสำคัญมาก: เราต้องตั้งค่าอันที่เฉพาะเจาะจงกว่าก่อน

# 1. เสาอากาศสำหรับ JS/CSS:
#    บอก FastAPI ว่า "ถ้ามีใครขอไฟล์ที่ขึ้นต้นด้วย /static/... ให้ไปหาจากโฟลเดอร์ที่ชื่อ 'static' นะ"
#    นี่คือการทำให้ JavaScript, CSS, รูปภาพต่างๆ โหลดได้ครับ
app.mount("/static", StaticFiles(directory="static"), name="static")

# 2. เสาอากาศสำหรับ HTML (ตัวหลัก):
#    บอก FastAPI ว่า "สำหรับคำขออื่นๆ ที่เหลือทั้งหมด (เช่น / หรือ /correlation) ให้ไปหาไฟล์ .html
#    จากโฟลเดอร์ปัจจุบัน (.) นะ"
#    ตัวนี้ต้องอยู่ล่างสุดเสมอ เพราะมันเป็นตัวรับทุกอย่างที่เสาอากาศตัวบนไม่รับ
app.mount("/", StaticFiles(directory=".", html=True), name="app")
#
# ====================================================================================
