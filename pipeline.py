# FILE: pipeline.py
from __future__ import annotations
import logging
import threading
import time
from datetime import datetime
import concurrent.futures

import numpy as np
import pandas as pd

import data_engine as eng
import data_io
from universe import FLAGS, UNIVERSE
from constants import (
    CORE_N, PIPELINE_BATCH_SIZE,
    BREADTH_HISTORY_DAYS, BREADTH_BEAR_THRESHOLD, BREADTH_BEAR_FALL, BREADTH_BEAR_MIN_MKT,
    WATCHLIST_TOP_N, THEME_TOP_N, RS_MOVERS_TOP_N,
    TRADING_DAYS_MONTH, TRADING_DAYS_QUARTER, FETCH_RATE_DELAY,
)

log = logging.getLogger("playground.pipeline")
SIGNAL_NAMES = ["VDU", "PPBP", "BGU", "52W"]

FETCH_STATE = {}
_lock = threading.Lock()

def _upd(**kwargs):
    with _lock:
        FETCH_STATE.update(kwargs)

def get_fetch_state():
    with _lock:
        return FETCH_STATE.copy()

def active_universe(mode: str) -> dict:
    if mode != "full":
        return {
            mkt: {t: UNIVERSE[mkt][t] for i, t in enumerate(UNIVERSE[mkt]) if i < CORE_N.get(mkt, 10)}
            for mkt in UNIVERSE
        }
    return UNIVERSE

def _fetch_market(mkt_name: str, tickers_dict: dict) -> tuple[str, dict]:
    tickers = list(tickers_dict.keys())
    results = {}
    total_batches = (len(tickers) + PIPELINE_BATCH_SIZE - 1) // PIPELINE_BATCH_SIZE

    with _lock:
        FETCH_STATE["market"] = mkt_name
        FETCH_STATE["batch"] = 0
        FETCH_STATE["total_batches"] = total_batches
        FETCH_STATE["tickers_done_market"] = 0
        FETCH_STATE["tickers_total_market"] = len(tickers)

    for i, batch in enumerate(data_io.chunk(tickers, PIPELINE_BATCH_SIZE)):
        t0 = time.time()
        _upd(batch=i + 1)
        batch_results = data_io.fetch_batch(tuple(batch))
        results.update({t: v for t, v in batch_results.items() if v is not None})

        with _lock:
            FETCH_STATE["tickers_done_market"] += len(batch)
            FETCH_STATE["tickers_done"] = (FETCH_STATE.get("tickers_done", 0) + len(batch))

        log.info(f"batch {mkt_name} {i+1}/{total_batches} ({len(batch_results)}/{len(batch)} ok) took {time.time()-t0:.2f}s")
        if i < total_batches - 1:
            time.sleep(FETCH_RATE_DELAY)

    return mkt_name, results

def fetch_universe(active: dict) -> tuple[dict, dict, dict]:
    combined: dict[str, pd.DataFrame] = {}
    ticker_meta: dict[str, dict] = {}
    fetch_results: dict[str, dict] = {m: {} for m in active}
    markets = list(active.keys())
    total = sum(len(v) for v in active.values())
    t0 = time.time()
    _upd(stage="fetching", started=t0, tickers_total=total, markets_total=markets, markets_done=[], tickers_done=0)
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
                    name, theme = tk_dict.get(t, (t, "Unknown"))

                    # ✨ FIXED: Call the real indicator engine
                    combined[t] = eng.add_technical_indicators(df)

                    ticker_meta[t] = {"market": mkt_name, "name": name, "theme": theme}
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
        executor.shutdown(wait=False, cancel_futures=True)

    log.info("=== fetch_universe END %d tickers %.1fs ===", len(combined), time.time() - t0)
    _upd(stage="computing")
    return combined, ticker_meta, fetch_results

def _safe_series(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan).fillna(0)

def compute_dashboard(combined, ticker_meta, fetch_results, active) -> dict:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    sync = data_io.sync_report(fetch_results, active)
    if not combined:
        _upd(stage="error", last_error="no data")
        return {"ok": False, "error": "ดึงข้อมูลจาก Yahoo Finance ไม่สำเร็จ", "sync": sync, "updated": now_str}

    log.info("compute_dashboard %d tickers", len(combined))

    close_df = pd.DataFrame({
        ticker: data['Close']
        for ticker, data in combined.items() if data is not None and not data.empty
    })

    breadth_rows, breadth_history_all, bear_override = _compute_breadth(combined, ticker_meta)

    signal_count_5d = {k: 0 for k in SIGNAL_NAMES}
    ticker_signal = {}
    for t, d in combined.items():
        try:
            # ✨ FIXED: Call real scanner and confluence engines
            sig = eng.run_scanners(d)
            rolled, conf, count = eng.confluence_flags(sig)

            count_val = float(count.iloc[-1]) if count is not None and not count.empty else 0
            conf_val = bool(conf.iloc[-1]) if conf is not None and not conf.empty else False
            last_rolled = {k: bool(v.iloc[-1]) for k, v in rolled.items() if v is not None and not v.empty}
            for k, v in last_rolled.items():
                if v: signal_count_5d[k] += 1
            ticker_signal[t] = {"rolled": last_rolled, "count": int(count_val), "confluence": conf_val}
        except Exception:
            log.exception("scanner failed: %s", t)
            ticker_signal[t] = {"rolled": {}, "count": 0, "confluence": False}

    # ✨ FIXED: Call real RS rating engines
    rs_now = eng.rs_rating_per_market(combined, ticker_meta)
    rs_7 = eng.rs_rating_table(close_df, 7) if not close_df.empty else pd.Series(dtype=float)

    theme_map = {m['theme']: [] for m in ticker_meta.values()}
    for t, m in ticker_meta.items():
        theme_map.setdefault(m['theme'], []).append(t)

    # ✨ FIXED: Call real theme returns engine
    themes_df = eng.theme_returns(close_df, theme_map, ticker_meta, rs_now)

    themes = themes_df.sort_values(by=['r1m', 'r3m'], ascending=[False, False])
    theme_rows = themes.head(THEME_TOP_N).to_dict('records')

    rs_delta = _safe_series(rs_now - rs_7)
    rs_movers = []
    top_gainers = rs_delta.nlargest(RS_MOVERS_TOP_N).index
    for t in top_gainers:
        rs_movers.append({
            "ticker": t.split(".")[0], "full_ticker": t,
            "rs": int(rs_now.get(t, 0)), "drs7": int(rs_delta.get(t, 0)),
            "spark": _safe_series(close_df[t].tail(7)).tolist() if t in close_df else []
        })

    watch = [t for t, s in ticker_signal.items() if s["confluence"]][:WATCHLIST_TOP_N]
    watchlist = []
    for t in watch:
        meta = ticker_meta.get(t, {})
        d = combined.get(t)
        if d is None: continue
        watchlist.append({
            "ticker": t.split(".")[0], "full_ticker": t,
            "name": meta.get("name", ""), "theme": meta.get("theme", ""),
            "patterns": [k for k, v in ticker_signal[t]["rolled"].items() if v],
            "pct1d": eng.pct_change(d["Close"], 1) or 0.0,
            "rs": int(rs_now.get(t, 0)),
            "market": meta.get("market", ""),
            "drawdown_pct": eng.current_drawdown_from_peak(d["Close"]),
            "max_dd_pct": eng.max_drawdown(d["Close"]),
        })

    _upd(stage="done")
    return {
        "ok": True, "updated": now_str, "universe_total": len(ticker_meta), "universe_loaded": len(combined),
        "sync": sync, "breadth": breadth_rows, "bear_override": bear_override,
        "stat_cards": {"VDU": signal_count_5d["VDU"], "PPBP": signal_count_5d["PPBP"], "BGU": signal_count_5d["BGU"],
                       "52W": signal_count_5d["52W"], "total": sum(signal_count_5d.values())},
        "theme_movers": theme_rows, "rs_movers": rs_movers,
        "watchlist": sorted(watchlist, key=lambda x: x["rs"], reverse=True),
        "breadth_history_all": breadth_history_all,
        "rs_now": rs_now, "rs_7": rs_7, "ticker_signal": ticker_signal
    }

def _compute_breadth(combined, ticker_meta):
    breadth_rows = []
    breadth_history_all = {}
    bear_markets = 0
    mkt_groups = {m: [] for m in FLAGS}
    for t, meta in ticker_meta.items():
        if meta["market"] in mkt_groups:
            mkt_groups[meta["market"]].append(t)

    for mkt, tickers in mkt_groups.items():
        if not tickers: continue

        above50, above200, hist50, hist200 = [], [], [], []
        for t in tickers:
            if t not in combined: continue
            df = combined[t]
            if len(df) < 200: continue
            above50.append(df['Close'].iloc[-1] > df['SMA50'].iloc[-1])
            above200.append(df['Close'].iloc[-1] > df['SMA200'].iloc[-1])

            hist50.append((df['Close'] > df['SMA50']).tail(BREADTH_HISTORY_DAYS))
            hist200.append((df['Close'] > df['SMA200']).tail(BREADTH_HISTORY_DAYS))

        if not above50: continue

        pct50 = sum(above50) / len(above50) * 100
        pct200 = sum(above200) / len(above200) * 100

        df50 = pd.DataFrame(hist50).mean() * 100
        df200 = pd.DataFrame(hist200).mean() * 100

        chg = pct50 - df50.iloc[-2] if len(df50) > 1 else 0.0

        if pct50 < BREADTH_BEAR_THRESHOLD and chg < BREADTH_BEAR_FALL:
            bear_markets += 1

        breadth_rows.append({"code": mkt, "flag": FLAGS[mkt], "ma50": pct50, "ma200": pct200, "chg": chg})

        breadth_history_all[mkt] = {
            "dates": [d.strftime('%Y-%m-%d') for d in df50.index],
            "ma50": _safe_series(df50).tolist(),
            "ma200": _safe_series(df200).tolist(),
            "universe": len(tickers)
        }

    bear_override = bear_markets >= BREADTH_BEAR_MIN_MKT
    breadth_rows.sort(key=lambda x: x['ma50'], reverse=True)
    return breadth_rows, breadth_history_all, bear_override
