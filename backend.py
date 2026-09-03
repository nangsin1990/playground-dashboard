from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA, PREWARM_INTERVAL_SEC, PREWARM_MODES
import preset_store
import correlation as corr
import earnings_board as eg
import economic_calendar as ec
import event_impact as ei
import etf_board as eb
import global_market as gm
import gold as gd
import leadership as lb
import market_regime as mr
import pipeline
import rotation_rrg as rrg
import screener as scr
import technical_analysis as ta
import thematic_matrix as tm
import fundamentals as fund

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("playground")

app = FastAPI(title="Playground Dashboard API", version="6.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_boot_time = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

ROOT = os.path.dirname(os.path.abspath(__file__))


# ── Cache pre-warm scheduler ────────────────────────────────────────────────
# ยิง load_market_pack() ล่วงหน้าเป็นระยะ (ก่อน TTL 15 นาทีหมดอายุ) เพื่อไม่ให้
# user คนแรกที่เปิด dashboard หลัง cache หมดอายุต้องรอ cold-load เอง
# (โดยเฉพาะ mode=full ~913 ticker ที่โหลดนานสุด)
_prewarm_stop = threading.Event()


def _prewarm_loop():
    # รอบแรกยิงทันทีตอน server เริ่ม กัน cold-start
    while not _prewarm_stop.is_set():
        for mode in PREWARM_MODES:
            if _prewarm_stop.is_set():
                break
            try:
                t0 = time.time()
                pipeline.load_market_pack(mode)
                log.info("prewarm OK mode=%s took=%.1fs", mode, time.time() - t0)
            except Exception:
                log.exception("prewarm FAILED mode=%s", mode)
        _prewarm_stop.wait(PREWARM_INTERVAL_SEC)


@app.on_event("startup")
def _start_prewarm():
    thread = threading.Thread(target=_prewarm_loop, daemon=True, name="cache-prewarm")
    thread.start()
    log.info("cache pre-warm scheduler started (interval=%ds, modes=%s)", PREWARM_INTERVAL_SEC, PREWARM_MODES)


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
                clear_func()
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
    try:
        preset_info = preset_store.store_status()
    except Exception as e:
        preset_info = {"error": str(e)}
    try:
        from cache_utils import cache_status
        cache_info = cache_status()
    except Exception as e:
        cache_info = {"error": str(e)}
    return {"status": "ok", "booted": _boot_time, "now": datetime.now().isoformat(), "preset_store": preset_info, "cache": cache_info}


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
    pack = pipeline.load_market_pack(mode)
    meta = pack.get("ticker_meta") or {}
    dash = pack.get("dash") or {}
    if not meta and not dash.get("ok"):
        return _resp({"ok": False, "results": [], "error": "Dashboard data not available"})
    rs_now = dash.get("rs_now")
    matches = []
    for t, m in meta.items():
        m = m or {}
        blob = " ".join([
            t,
            t.split(".")[0],
            str(m.get("name") or ""),
            str(m.get("theme") or ""),
            str(m.get("market") or ""),
        ]).lower()
        if query_lower not in blob:
            continue
        raw_rs = rs_now.get(t) if hasattr(rs_now, "get") else None
        try:
            rs_val = int(raw_rs) if raw_rs is not None and raw_rs == raw_rs else None
        except (TypeError, ValueError):
            rs_val = None
        matches.append({
            "ticker": t.split(".")[0],
            "full_ticker": t,
            "name": m.get("name", ""),
            "theme": m.get("theme", ""),
            "market": m.get("market", ""),
            "rs": rs_val,
            "patterns": [],
            "pct1d": None,
            "price": None,
        })
        if len(matches) >= 20:
            break
    return _resp({"ok": True, "query": q, "results": matches})


def _clear_leadership_and_pack():
    try:
        lb.build_leadership_board.cache_clear()
    except Exception:
        pass
    try:
        lb.build_laggards_board.cache_clear()
    except Exception:
        pass
    _clear_price_and_pack()


@app.get("/api/leadership")
def leadership_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(_clear_leadership_and_pack))):
    return _resp(lb.build_leadership_board(mode=mode))


@app.get("/api/laggards")
def laggards_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(_clear_leadership_and_pack))):
    return _resp(lb.build_laggards_board(mode=mode))


@app.get("/api/global")
def global_api(_: None = Depends(get_cache_clearer(gm.fetch_global_market.cache_clear))):
    return _resp(gm.fetch_global_market())


def _clear_gold_only(refresh: bool = False):
    """Refresh Gold cache only — do not invalidate the stock pipeline."""
    if refresh:
        try:
            gd.fetch_gold.cache_clear()
            log.info("Cache cleared for: fetch_gold")
        except Exception:
            log.warning("Could not clear gold cache")


@app.get("/api/gold")
def gold_api(_: None = Depends(_clear_gold_only)):
    return _resp(gd.fetch_gold())


@app.get("/api/calendar")
def calendar_api(_: None = Depends(get_cache_clearer(ec.fetch_economic_calendar.cache_clear))):
    return _resp(ec.fetch_economic_calendar())


@app.get("/api/earnings_board")
def earnings_board_api(_: None = Depends(get_cache_clearer(eg.fetch_earnings_board.cache_clear))):
    return _resp(eg.fetch_earnings_board())


@app.get("/api/event_impact")
def event_impact_api(_: None = Depends(get_cache_clearer(ei.fetch_event_impact.cache_clear))):
    return _resp(ei.fetch_event_impact())


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
    try:
        limit = int(params.get("limit") or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(20, min(limit, 500))
    return _resp(scr.fetch_screener(mode=mode, params=params, sort_by=sort_by, sort_desc=sort_desc, limit=limit))


# ── Screener saved presets — Drive-backed, shared across all devices ──────────
# (localStorage เดิมผูกกับ browser/เครื่องเดียว ใช้หลายเครื่องแล้ว preset ไม่เห็นตรงกัน
# ย้ายมาเก็บฝั่ง backend + Drive แทน ดู preset_store.py)
class PresetSaveBody(BaseModel):
    name: str
    filters: dict[str, Any]


@app.get("/api/screener/presets")
def screener_presets_list():
    try:
        return _resp({"ok": True, "presets": preset_store.list_presets()})
    except Exception as e:
        log.exception("screener_presets_list failed")
        return _resp({"ok": False, "error": str(e)})


@app.post("/api/screener/presets")
def screener_presets_save(body: PresetSaveBody):
    try:
        data = preset_store.save_preset(body.name, body.filters)
        return _resp({"ok": True, "presets": data})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("screener_presets_save failed")
        return _resp({"ok": False, "error": str(e)})


@app.delete("/api/screener/presets/{name}")
def screener_presets_delete(name: str):
    try:
        data = preset_store.delete_preset(name)
        return _resp({"ok": True, "presets": data})
    except Exception as e:
        log.exception("screener_presets_delete failed")
        return _resp({"ok": False, "error": str(e)})


@app.get("/api/thematic")
def thematic_api(mode: str = Query("core"), _: None = Depends(get_cache_clearer(_clear_price_and_pack))):
    return _resp(tm.fetch_thematic(mode=mode))


@app.get("/api/technicals")
def technicals_api(ticker: str, _: None = Depends(get_cache_clearer(ta.fetch_technicals.cache_clear))):
    return _resp(ta.fetch_technicals(ticker=ticker))


@app.get("/api/fundamentals")
def fundamentals_api(ticker: str, _: None = Depends(get_cache_clearer(fund.fetch_fundamentals.cache_clear))):
    return _resp(fund.fetch_fundamentals(ticker=ticker))


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
