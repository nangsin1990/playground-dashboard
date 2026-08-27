from __future__ import annotations

import logging
import math
import os
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA
import correlation as corr
import economic_calendar as ec
import etf_board as eb
import global_market as gm
import leadership as lb
import market_regime as mr
import pipeline
import rotation_rrg as rrg
import screener as scr
import technical_analysis as ta
import thematic_matrix as tm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("playground")

app = FastAPI(title="Playground Dashboard API", version="6.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_boot_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

ROOT = os.path.dirname(os.path.abspath(__file__))


def _jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(x) for x in obj]
    try:
        import pandas as pd
        import numpy as np
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()
        if isinstance(obj, pd.Series):
            return {str(k): _jsonable(v) for k, v in obj.to_dict().items()}
        if isinstance(obj, pd.DataFrame):
            return _jsonable(obj.to_dict(orient="records"))
        if isinstance(obj, np.generic):
            return _jsonable(obj.item())
        if isinstance(obj, np.ndarray):
            return _jsonable(obj.tolist())
    except Exception:
        pass
    if hasattr(obj, "item"):
        try:
            return _jsonable(obj.item())
        except Exception:
            pass
    return str(obj)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled exception at URL: %s", request.url)
    return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


def _resp(data: dict):
    safe = _jsonable(data if isinstance(data, dict) else {"ok": False, "error": "invalid payload"})
    return JSONResponse(safe, status_code=200 if safe.get("ok", True) else 503)


def _clear_price_and_pack():
    try:
        pipeline.load_market_pack.cache_clear()
    except Exception:
        pass
    try:
        import data_io
        data_io.clear_cache()
    except Exception:
        pass


def get_cache_clearer(clear_func: Callable[[], None]):
    def dependency(refresh: bool = False):
        if refresh:
            try:
                name = getattr(clear_func, "__name__", "cache_clear")
                log.info("Cache cleared for: %s", name)
                if clear_func is not _clear_price_and_pack:
                    clear_func()
                _clear_price_and_pack()
            except Exception:
                log.warning("Could not clear cache for a function.")
    return dependency


def _cached_dashboard(mode: str):
    pack = pipeline.load_market_pack(mode)
    return pack.get("dash") or {"ok": False, "error": "no dashboard pack"}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def status():
    return {"status": "ok", "booted": _boot_time, "now": datetime.now().isoformat()}


@app.get("/api/dashboard")
def dashboard(mode: str = Query("core"), market: Optional[str] = None, _: None = Depends(get_cache_clearer(_clear_price_and_pack))):
    pack = pipeline.load_market_pack(mode)
    result = pack.get("dash")
    if not isinstance(result, dict):
        return _resp({"ok": False, "error": "Dashboard data invalid"})
    result = dict(result)
    result.pop("rs_now", None)
    result.pop("rs_7", None)
    result.pop("ticker_signal", None)
    if market and "watchlist" in result:
        result["watchlist"] = [w for w in result.get("watchlist") or [] if w.get("market") == market]
    result["my_watchlist"] = _build_my_watch(pack)
    return _resp(result)


def _build_my_watch(pack: dict) -> dict:
    from personal_watchlist import build_my_watchlist
    import economic_calendar as ec
    dash = pack.get("dash") or {}
    try:
        events = (ec.fetch_economic_calendar() or {}).get("events") or []
    except Exception:
        events = []
    return build_my_watchlist(
        pack.get("combined") or {},
        pack.get("ticker_meta") or {},
        dash.get("ticker_signal") or {},
        dash.get("rs_now"),
        events,
    )


@app.get("/api/my_watchlist")
def my_watchlist_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(_clear_price_and_pack))):
    pack = pipeline.load_market_pack(mode)
    return _resp(_build_my_watch(pack))


@app.get("/api/progress")
def progress_api():
    state = pipeline.get_fetch_state()
    if not isinstance(state, dict):
        state = {}
    state = dict(state)
    state.setdefault("ok", True)
    return _resp(state)


@app.get("/api/regime")
def regime_api(breadth_us_ma50: Optional[float] = None, breadth_us_ma200: Optional[float] = None):
    if breadth_us_ma50 is None or breadth_us_ma200 is None:
        try:
            dash = (pipeline.load_market_pack("core") or {}).get("dash") or {}
            us = next((b for b in dash.get("breadth") or [] if b.get("code") == "US"), None)
            if us:
                if breadth_us_ma50 is None:
                    breadth_us_ma50 = us.get("ma50")
                if breadth_us_ma200 is None:
                    breadth_us_ma200 = us.get("ma200")
        except Exception:
            pass
    return _resp(mr.compute_market_regime(breadth_us_ma50=breadth_us_ma50, breadth_us_ma200=breadth_us_ma200))


@app.get("/api/search")
def search(q: str, mode: str = "core"):
    if not q:
        return _resp({"ok": True, "query": q, "results": []})
    query_lower = q.lower().strip()
    all_data = _cached_dashboard(mode)
    if not all_data.get("ok"):
        return _resp({"ok": False, "results": [], "error": "Dashboard data not available"})
    matches = [
        item for item in all_data.get("watchlist", [])
        if query_lower in item.get("ticker", "").lower()
        or query_lower in item.get("name", "").lower()
        or query_lower in item.get("theme", "").lower()
    ]
    return _resp({"ok": True, "query": q, "results": matches[:20]})


@app.get("/api/leadership")
def leadership_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(_clear_price_and_pack))):
    return _resp(lb.build_leadership_board(mode=mode))


@app.get("/api/laggards")
def laggards_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(_clear_price_and_pack))):
    return _resp(lb.build_laggards_board(mode=mode))


@app.get("/api/global")
def global_api(_: None = Depends(get_cache_clearer(gm.fetch_global_market.cache_clear))):
    return _resp(gm.fetch_global_market())


@app.get("/api/calendar")
def calendar_api(_: None = Depends(get_cache_clearer(ec.fetch_economic_calendar.cache_clear))):
    return _resp(ec.fetch_economic_calendar())


@app.get("/api/correlation")
def correlation_api(_: None = Depends(get_cache_clearer(corr.fetch_correlation.cache_clear))):
    return _resp(corr.fetch_correlation())


@app.get("/api/etf")
def etf_api(_: None = Depends(get_cache_clearer(eb.fetch_etf_board.cache_clear))):
    return _resp(eb.fetch_etf_board())


@app.get("/api/rotation")
def rotation_api(mode: str = Query("core"), market: str = Query("GLOBAL"), _: None = Depends(get_cache_clearer(rrg.fetch_rotation.cache_clear))):
    return _resp(rrg.fetch_rotation(mode=mode, market=market))


@app.get("/api/screener")
def screener_api(request: Request, mode: str = Query("core"), _: None = Depends(get_cache_clearer(_clear_price_and_pack))):
    params = dict(request.query_params)
    sort_by = params.get("sort_by", "ls")
    sort_desc = params.get("sort_desc", "true").lower() == "true"
    return _resp(scr.fetch_screener(mode=mode, params=params, sort_by=sort_by, sort_desc=sort_desc))


@app.get("/api/thematic")
def thematic_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(_clear_price_and_pack))):
    return _resp(tm.fetch_thematic(mode=mode))


@app.get("/api/technicals")
def technicals_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_technicals.cache_clear))):
    return _resp(ta.fetch_technicals(ticker=ticker))


@app.get("/api/sector_rs")
def sector_rs_api(ticker: str, theme: str = "", _: None = Depends(get_cache_clearer(ta.fetch_sector_rs.cache_clear))):
    return _resp(ta.fetch_sector_rs(ticker=ticker, theme=theme))


@app.get("/api/earnings")
def earnings_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_earnings.cache_clear))):
    return _resp(ta.fetch_earnings(ticker=ticker))


@app.get("/api/dividends")
def dividends_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_dividends.cache_clear))):
    return _resp(ta.fetch_dividends(ticker=ticker))


@app.get("/api/options_iv")
def options_iv_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_options_iv.cache_clear))):
    return _resp(ta.fetch_options_iv(ticker=ticker))


def _serve_root_file(name: str):
    path = os.path.join(ROOT, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"{name} not found")
    return FileResponse(path)


@app.get("/style.css", include_in_schema=False)
async def serve_style_css():
    return _serve_root_file("style.css")


@app.get("/nav.js", include_in_schema=False)
async def serve_nav_js():
    return _serve_root_file("nav.js")


if os.path.isdir(os.path.join(ROOT, "static")):
    app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "static")), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_index_page(request: Request):
    return FileResponse(os.path.join(ROOT, "index.html"))


@app.get("/{page_name}.html", response_class=HTMLResponse, include_in_schema=False)
async def serve_html_page(request: Request, page_name: str):
    if ".." in page_name or "/" in page_name:
        raise HTTPException(status_code=404, detail="Not Found")
    file_path = os.path.join(ROOT, f"{page_name}.html")
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail=f"Page not found: {page_name}.html")
