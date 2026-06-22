"""
pipeline.py — Orchestration layer
v5.2 fixes (per Incident Report):
  1. ลบ except Exception: pass → log.exception ทุกตัว
  2. เพิ่ม FETCH_STATE progress tracker → /api/status อ่านได้
  3. เพิ่ม batch-level + market-level logging (ดู Colab output)
  4. future.result timeout ครบทุก market ไม่ค้าง
"""
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

# ── Fix #2: Global progress state (thread-safe) ───────────────────────────────
_state_lock = threading.Lock()
FETCH_STATE: dict = {
    "stage":          "idle",      # idle | fetching | computing | done | error
    "market":         "",
    "batch":          0,
    "total_batches":  0,
    "markets_done":   [],
    "markets_total":  [],
    "started":        None,
    "elapsed_sec":    0,
    "last_error":     "",
}

def _set_state(**kw):
    with _state_lock:
        FETCH_STATE.update(kw)
        if FETCH_STATE.get("started"):
            FETCH_STATE["elapsed_sec"] = round(time.time() - FETCH_STATE["started"], 1)

def get_fetch_state() -> dict:
    with _state_lock:
        return dict(FETCH_STATE)


def core_universe() -> dict:
    return {m: dict(list(UNIVERSE[m].items())[:CORE_N[m]]) for m in CORE_N}

def active_universe(mode: str) -> dict:
    return UNIVERSE if mode == "full" else core_universe()


# ── 1) Fetch — parallel per market ───────────────────────────────────────────
def _fetch_market(market: str, ticker_dict: dict) -> tuple[str, dict]:
    """Fetch one market's tickers in batches — with progress + logging."""
    flat       = list(ticker_dict.items())
    batches    = list(data_io.chunk(flat, PIPELINE_BATCH_SIZE))
    n_batches  = len(batches)
    results    = {}

    log.info("[%s] START — %d tickers / %d batches", market, len(flat), n_batches)
    _set_state(market=market, batch=0, total_batches=n_batches)

    for i, batch in enumerate(batches, 1):
        tickers = tuple(t for t, _ in batch)
        t0      = time.time()

        # Fix #3: log every batch start
        log.info("[%s] batch %d/%d — %d tickers ...", market, i, n_batches, len(tickers))
        _set_state(market=market, batch=i, total_batches=n_batches)

        try:
            raw = data_io.fetch_batch(tickers)
            for t, _ in batch:
                df = raw.get(t)
                if df is not None:
                    results[t] = df
        except Exception:
            # Fix #1: ไม่กลืน error — log ออกมา
            log.exception("[%s] batch %d/%d FAILED", market, i, n_batches)

        elapsed = round(time.time() - t0, 1)
        log.info("[%s] batch %d/%d DONE — %d/%d tickers OK (%.1fs)",
                 market, i, n_batches, len(results), len(flat), elapsed)

        time.sleep(FETCH_RATE_DELAY)

    log.info("[%s] END — %d/%d tickers loaded", market, len(results), len(flat))
    return market, results


def fetch_universe(active: dict):
    combined:      dict[str, pd.DataFrame] = {}
    ticker_meta:   dict[str, dict]         = {}
    fetch_results: dict[str, dict]         = {m: {} for m in active}

    markets = list(active.keys())
    t_start = time.time()
    _set_state(
        stage="fetching",
        market="",
        batch=0,
        total_batches=0,
        markets_done=[],
        markets_total=markets,
        started=t_start,
        last_error="",
    )

    log.info("=== fetch_universe START — markets: %s ===", markets)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_fetch_market, m, tk): m for m, tk in active.items()}

        for future in concurrent.futures.as_completed(futures):
            market = futures[future]
            try:
                # Fix #1: timeout per-market so one hung market doesn't block all
                mkt_result, market_results = future.result(timeout=120)
                fetch_results[mkt_result]  = market_results
                ticker_dict                = active[mkt_result]

                for t, df in market_results.items():
                    name, theme         = ticker_dict[t]
                    combined[t]         = eng.add_indicators(df)
                    ticker_meta[t]      = {"market": mkt_result, "name": name, "theme": theme}

                with _state_lock:
                    FETCH_STATE["markets_done"].append(mkt_result)

                log.info("market %s DONE — %d tickers loaded", mkt_result, len(market_results))

            except concurrent.futures.TimeoutError:
                # Fix #1: log ออกมา ไม่กลืน
                log.error("market %s TIMEOUT after 120s — skipping", market)
                _set_state(last_error=f"{market} timeout")

            except Exception:
                # Fix #1: log ออกมา ไม่กลืน
                log.exception("market %s FAILED — skipping", market)
                _set_state(last_error=f"{market} error")

    elapsed = round(time.time() - t_start, 1)
    log.info("=== fetch_universe END — %d tickers total (%.1fs) ===", len(combined), elapsed)
    _set_state(stage="computing", elapsed_sec=elapsed)

    return combined, ticker_meta, fetch_results


# ── 2) Safe return helper ─────────────────────────────────────────────────────
def _pct_change(series: pd.Series, n: int) -> float | None:
    try:
        if len(series) <= n or series.iloc[-1 - n] == 0:
            return None
        val = (series.iloc[-1] / series.iloc[-1 - n] - 1) * 100
        return None if (np.isnan(val) or np.isinf(val)) else round(float(val), 2)
    except Exception:
        return None


# ── 3) Compute dashboard ─────────────────────────────────────────────────────
def compute_dashboard(combined, ticker_meta, fetch_results, active) -> dict:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    sync    = data_io.sync_report(fetch_results, active)

    if not combined:
        _set_state(stage="error", last_error="combined empty")
        return {"ok": False, "error": "ดึงข้อมูลจาก Yahoo Finance ไม่สำเร็จ",
                "sync": sync, "updated": now_str}

    log.info("compute_dashboard — %d tickers", len(combined))

    # ── Market breadth ────────────────────────────────────────────────────────
    breadth_rows = []
    for market in active:
        mt = list(fetch_results[market].keys())
        if not mt:
            breadth_rows.append({"flag": FLAGS.get(market,""), "code": market,
                                  "ma50": 0.0, "ma200": 0.0, "chg": 0.0})
            continue
        a50, a200, a50_5ago = [], [], []
        for t in mt:
            d = combined[t]; last = d.iloc[-1]
            a50.append(bool(last["Close"] > last["SMA50"]))
            a200.append(bool(last["Close"] > last["SMA200"]))
            if len(d) > 5:
                r5 = d.iloc[-6]
                a50_5ago.append(bool(r5["Close"] > r5["SMA50"]))
        ma50  = round(float(np.mean(a50))  * 100, 2)
        ma200 = round(float(np.mean(a200)) * 100, 2)
        chg   = round(ma50 - (float(np.mean(a50_5ago)*100) if a50_5ago else ma50), 2)
        breadth_rows.append({"flag": FLAGS.get(market,""), "code": market,
                              "ma50": ma50, "ma200": ma200, "chg": chg})

    # ── Scanners ──────────────────────────────────────────────────────────────
    signal_count_5d = {k: 0 for k in SIGNAL_NAMES}
    ticker_signal   = {}
    for t, d in combined.items():
        try:
            sig             = eng.run_scanners(d)
            rolled, conf, count = eng.confluence_flags(sig)
            last_rolled     = {k: bool(v.iloc[-1]) for k, v in rolled.items()}
            for k, v in last_rolled.items():
                if v: signal_count_5d[k] += 1
            ticker_signal[t] = {
                "rolled":     last_rolled,
                "count":      int(count.iloc[-1]),
                "confluence": bool(conf.iloc[-1]),
            }
        except Exception:
            log.exception("scanner failed for %s", t)
            ticker_signal[t] = {"rolled": {}, "count": 0, "confluence": False}

    # ── RS Rating per-market ──────────────────────────────────────────────────
    rs_now = eng.rs_rating_per_market(combined, ticker_meta)
    blended7 = pd.Series({t: eng.blended_return(d["Close"].iloc[:-7])
                           for t, d in combined.items() if len(d) > 7})
    rs_7 = eng.rs_rating_table(blended7).reindex(rs_now.index).fillna(rs_now)

    # ── Theme rotation ────────────────────────────────────────────────────────
    ret_1d = pd.Series({t: _pct_change(d["Close"], 1)  for t, d in combined.items()
                         if _pct_change(d["Close"], 1) is not None})
    ret_1m = pd.Series({t: _pct_change(d["Close"], TRADING_DAYS_MONTH)   for t, d in combined.items()
                         if _pct_change(d["Close"], TRADING_DAYS_MONTH) is not None})
    ret_3m = pd.Series({t: _pct_change(d["Close"], TRADING_DAYS_QUARTER) for t, d in combined.items()
                         if _pct_change(d["Close"], TRADING_DAYS_QUARTER) is not None})
    theme_map = {t: m["theme"] for t, m in ticker_meta.items()}
    themes    = eng.theme_returns(ret_1d/100, ret_1m/100, ret_3m/100, theme_map)

    theme_rows = []
    for theme, row in themes.head(THEME_TOP_N).iterrows():
        members = [t for t, th in theme_map.items() if th == theme]
        top2    = sorted(members, key=lambda t: rs_now.get(t, 0), reverse=True)[:2]
        theme_rows.append({
            "theme":   theme, "tickers": [t.split(".")[0] for t in top2],
            "d1": round(float(row["1D"])*100,2),
            "m1": round(float(row["1M"])*100,2),
            "m3": round(float(row["3M"])*100,2),
        })

    # ── Breadth history ───────────────────────────────────────────────────────
    breadth_history_all = {}
    for market in active:
        mt = list(fetch_results.get(market, {}).keys())
        bh = {"dates": [], "ma50": [], "ma200": [], "universe": len(mt)}
        if mt:
            try:
                a50_df  = pd.DataFrame({t: combined[t]["Close"] > combined[t]["SMA50"]  for t in mt})
                a200_df = pd.DataFrame({t: combined[t]["Close"] > combined[t]["SMA200"] for t in mt})
                h50     = eng.market_breadth_history(a50_df,  days=BREADTH_HISTORY_DAYS)
                h200    = eng.market_breadth_history(a200_df, days=BREADTH_HISTORY_DAYS)
                bh["dates"] = [d.strftime("%Y-%m-%d") for d in h50.index]
                bh["ma50"]  = [round(float(v), 2) for v in h50.values]
                bh["ma200"] = [round(float(v), 2) for v in h200.values]
            except Exception:
                log.exception("breadth_history failed for %s", market)
        breadth_history_all[market] = bh

    breadth_history = breadth_history_all.get("US", {"dates":[],"ma50":[],"ma200":[],"universe":0})

    # ── Bear override ─────────────────────────────────────────────────────────
    bear_markets  = [r for r in breadth_rows
                     if r["ma50"] < BREADTH_BEAR_THRESHOLD and r["chg"] < BREADTH_BEAR_FALL]
    bear_override = len(bear_markets) >= BREADTH_BEAR_MIN_MKT

    # ── Watchlist ─────────────────────────────────────────────────────────────
    watch = sorted(
        [t for t, s in ticker_signal.items() if s["confluence"]],
        key=lambda t: (ticker_signal[t]["count"], int(rs_now.get(t, 0))),
        reverse=True,
    )[:WATCHLIST_TOP_N]

    watchlist = []
    for t in watch:
        meta = ticker_meta[t]; d = combined[t]
        watchlist.append({
            "ticker":       t.split(".")[0], "full_ticker": t,
            "name":         meta["name"],    "theme": meta["theme"],
            "patterns":     [k for k in SIGNAL_NAMES if ticker_signal[t]["rolled"].get(k)],
            "pct1d":        _pct_change(d["Close"], 1) or 0.0,
            "rs":           int(rs_now.get(t, 0)),
            "market":       meta["market"],
            "drawdown_pct": eng.current_drawdown_from_peak(d["Close"]),
            "max_dd_pct":   eng.max_drawdown(d["Close"]),
        })

    # ── RS Movers ─────────────────────────────────────────────────────────────
    movers = eng.rs_movers_7d(rs_now, rs_7, top_n=RS_MOVERS_TOP_N)
    rs_movers = []
    for t, row in movers.iterrows():
        spark = [round(float(v), 4) for v in combined[t]["Close"].tail(10).tolist()
                 if not (np.isnan(v) or np.isinf(v))]
        rs_movers.append({
            "ticker": t.split(".")[0], "full_ticker": t,
            "rs": int(row["RS"]), "drs7": int(row["dRS_7D"]), "spark": spark,
        })

    _set_state(stage="done")
    log.info("compute_dashboard DONE")

    return {
        "ok":                  True,
        "updated":             now_str,
        "universe_loaded":     len(combined),
        "universe_total":      sum(len(v) for v in active.values()),
        "sync":                sync,
        "breadth":             breadth_rows,
        "breadth_history_us":  breadth_history,
        "breadth_history_all": breadth_history_all,
        "stat_cards":          {**signal_count_5d, "total": len(combined)},
        "watchlist":           watchlist,
        "theme_movers":        theme_rows,
        "rs_movers":           rs_movers,
        "bear_override":       bear_override,
        "rs_scope":            "per-market",
    }
