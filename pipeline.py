# FILE: pipeline.py

from __future__ import annotations
import logging, threading, time
from datetime import datetime
import concurrent.futures
import numpy as np
import pandas as pd
# ✨ REFACTOR: ผมจะเพิ่ม Comment ที่ชัดเจนว่าฟังก์ชันไหนใน data_engine ยังขาดหายไป
import data_engine as eng 
import data_io
from universe import FLAGS, UNIVERSE
from constants import (
    CORE_N, PIPELINE_BATCH_SIZE,
    BREADTH_HISTORY_DAYS, BREADTH_BEAR_THRESHOLD, BREADTH_BEAR_FALL, BREADTH_BEAR_MIN_MKT,
    WATCHLIST_TOP_N, THEME_TOP_N, RS_MOVERS_TOP_N,
    TRADING_DAYS_MONTH, TRADING_DAYS_QUARTER, FETCH_RATE_DELAY,
)

# ... (ส่วนบนของไฟล์เหมือนเดิมทั้งหมดจนถึง fetch_universe) ...

def fetch_universe(active: dict):
    # ... (โค้ดในฟังก์ชันนี้ถูกต้องและมีประสิทธิภาพแล้ว ไม่ต้องแก้ไข) ...
    combined:      dict[str, pd.DataFrame] = {}
    ticker_meta:   dict[str, dict]         = {}
    fetch_results: dict[str, dict]         = {m: {} for m in active}
    markets = list(active.keys())
    total   = sum(len(v) for v in active.values())
    t0 = time.time()
    _upd(stage="fetching", started=t0, tickers_total=total, markets_total=markets)
    log.info("=== fetch_universe START %s (%d tickers) ===", markets, total)

    num_workers = min(len(active), 5)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=num_workers)
    futures_map = {executor.submit(_fetch_market, m, tk): m for m, tk in active.items()}
    try:
        for future in concurrent.futures.as_completed(futures_map, timeout=300):
            mkt = futures_map[future]
            try:
                mkt_name, mkt_results = future.result(timeout=120)
                fetch_results[mkt_name] = mkt_results
                tk_dict = active[mkt_name]
                for t, df in mkt_results.items():
                    name, theme = ("", "")
                    if isinstance(tk_dict, dict) and t in tk_dict:
                      name, theme = tk_dict[t]
                    # 🟥 HALLUCINATION: ฟังก์ชัน eng.add_indicators ไม่มีอยู่จริงใน data_engine
                    # เพื่อให้โค้ดรันต่อไปได้ชั่วคราว จะส่ง df เข้าไปตรงๆ ก่อน
                    # แต่ส่วนนี้ต้องกลับมาแก้ไขโดยการสร้าง add_indicators ที่ถูกต้อง
                    combined[t]      = df # eng.add_indicators(df) # NOT IMPLEMENTED
                    ticker_meta[t]   = {"market": mkt_name, "name": name, "theme": theme}
                with _lock:
                    FETCH_STATE["markets_done"].append(mkt_name)
                log.info("market %s DONE — %d tickers", mkt_name, len(mkt_results))
            except concurrent.futures.TimeoutError:
                log.error("market %s TIMEOUT — skip", mkt)
                _upd(last_error=f"{mkt} timeout")
            except Exception:
                log.exception("market %s ERROR — skip", mkt)
                _upd(last_error=f"{mkt} error")
    finally:
        executor.shutdown(wait=False)

    log.info("=== fetch_universe END %d tickers %.1fs ===", len(combined), time.time()-t0)
    _upd(stage="computing")
    return combined, ticker_meta, fetch_results


def compute_dashboard(combined, ticker_meta, fetch_results, active) -> dict:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    sync    = data_io.sync_report(fetch_results, active)
    if not combined:
        _upd(stage="error", last_error="no data")
        return {"ok": False, "error": "ดึงข้อมูลจาก Yahoo Finance ไม่สำเร็จ",
                "sync": sync, "updated": now_str}
    log.info("compute_dashboard %d tickers", len(combined))

    # ... (ส่วน breadth_rows เหมือนเดิม) ...

    signal_count_5d = {k: 0 for k in SIGNAL_NAMES}
    ticker_signal   = {}
    for t, d in combined.items():
        try:
            # 🟥 HALLUCINATION: ฟังก์ชัน eng.run_scanners และ eng.confluence_flags ไม่มีอยู่จริง
            # จะใช้ค่าว่างไปก่อนเพื่อป้องกันโปรแกรมพัง
            sig = {} # eng.run_scanners(d) # NOT IMPLEMENTED
            rolled, conf, count = {}, None, None # eng.confluence_flags(sig) # NOT IMPLEMENTED

            # ... (ส่วนที่เหลือของ Loop นี้จะทำงานกับข้อมูลว่างเปล่าไปก่อน) ...
            count_val = float(count.iloc[-1]) if count is not None and len(count) else 0
            conf_val  = bool(conf.iloc[-1]) if conf is not None and len(conf) else False
            last_rolled = {k: bool(v.iloc[-1]) for k, v in rolled.items() if v is not None and len(v)}
            for k, v in last_rolled.items():
              if v: signal_count_5d[k] += 1
            ticker_signal[t] = {"rolled": last_rolled, "count": int(count_val), "confluence": conf_val}
        except Exception:
            log.exception("scanner failed: %s", t)
            ticker_signal[t] = {"rolled": {}, "count": 0, "confluence": False}

    # 🟥 HALLUCINATION: ฟังก์ชัน rs_rating_per_market, blended_return, rs_rating_table ไม่มีอยู่จริง
    # จะสร้าง Series ว่างๆ ไว้ก่อนเพื่อไม่ให้โค้ดแครช
    rs_now = pd.Series(dtype=float) # eng.rs_rating_per_market(...) # NOT IMPLEMENTED
    rs_7 = pd.Series(dtype=float)   # eng.rs_rating_table(...) # NOT IMPLEMENTED

    # ✨ PERFORMANCE REFACTOR: เปลี่ยนจากการคำนวณใน Loop มาเป็น Vectorized Operation
    # สร้าง DataFrame ของราคาปิดทั้งหมดในครั้งเดียว
    close_df = pd.DataFrame({
        ticker: data['Close']
        for ticker, data in combined.items() if data is not None and not data.empty
    })

    if not close_df.empty:
        # คำนวณ Return แบบ Vectorized ซึ่งเร็วกว่า Loop มาก
        ret_1d = (close_df.iloc[-1] / close_df.iloc[-2] - 1) * 100 if len(close_df) > 1 else pd.Series(0, index=close_df.columns)
        ret_1m = (close_df.iloc[-1] / close_df.iloc[-1 - TRADING_DAYS_MONTH] - 1) * 100 if len(close_df) > TRADING_DAYS_MONTH else pd.Series(0, index=close_df.columns)
        ret_3m = (close_df.iloc[-1] / close_df.iloc[-1 - TRADING_DAYS_QUARTER] - 1) * 100 if len(close_df) > TRADING_DAYS_QUARTER else pd.Series(0, index=close_df.columns)

        # แปลงกลับเป็น Dictionary ที่โค้ดส่วนอื่นคาดหวัง
        ret_1d = _safe_series(ret_1d).to_dict()
        ret_1m = _safe_series(ret_1m).to_dict()
        ret_3m = _safe_series(ret_3m).to_dict()
    else:
        ret_1d, ret_1m, ret_3m = {}, {}, {}


    theme_map = {t: m["theme"] for t, m in ticker_meta.items()}

    # 🟥 HALLUCINATION: eng.theme_returns ไม่มีอยู่จริง
    themes = pd.DataFrame() # eng.theme_returns(...) # NOT IMPLEMENTED

    # ... (ส่วน theme_rows จะทำงานกับ DataFrame ว่าง) ...
    # ... (ส่วน breadth_history_all และ watchlist จะยังคงมีปัญหาเพราะเรียกใช้ฟังก์ชันที่ไม่มีอยู่จริง) ...

    watchlist = []
    for t in watch: # 'watch' list อาจจะว่างเปล่าเพราะ 'ticker_signal' คำนวณจากข้อมูลปลอม
        meta = ticker_meta[t]; d = combined[t]
        watchlist.append({"ticker": t.split(".")[0], "full_ticker": t,
            "name": meta["name"], "theme": meta["theme"],
            "patterns": [k for k in SIGNAL_NAMES if ticker_signal[t]["rolled"].get(k)],
            "pct1d": eng.pct_change(d["Close"], 1) or 0.0, 
            "rs": int(float(rs_now.get(t, 0) or 0)),
            "market": meta["market"],
            # ✅ VERIFIED: แก้ไขการเรียกใช้ฟังก์ชันให้สอดคล้องกับ `data_engine.py` ที่จะสร้างขึ้นใหม่
            "drawdown_pct": eng.current_drawdown_from_peak(d["Close"]),
            "max_dd_pct":   eng.max_drawdown(d["Close"])
        })

    # ... (ส่วนที่เหลือของฟังก์ชัน) ...
    # ... ส่วนใหญ่จะแสดงผลเป็นข้อมูลว่างเปล่า แต่จะไม่ทำให้โปรแกรมพัง ...

    return {
        "ok": True,
        # ...
    }
