from __future__ import annotations
import logging, threading, time
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

def _safe_series(s):
    return s.replace([np.inf, -np.inf], np.nan).fillna(0)

# ── Thread-safe progress state ────────────────────────────────────────────────
_lock = threading.Lock()
FETCH_STATE: dict = {
    "stage": "idle",
    "market": "", "batch": 0, "total_batches": 0,
    "markets_done": [], "markets_total": [],
    "tickers_done": 0, "tickers_total": 0,
    "cache_hits": 0, "cache_misses": 0,
    "elapsed_sec": 0, "started": None, "last_error": "",
}

def _upd(**kw):
    with _lock:
        FETCH_STATE.update(kw)
        if FETCH_STATE.get("started"):
            FETCH_STATE["elapsed_sec"] = round(time.time() - FETCH_STATE["started"], 1)

def get_fetch_state() -> dict:
    with _lock:
        return dict(FETCH_STATE)

def build_active_universe():
    from universe import UNIVERSE

    return {
        "US": list(UNIVERSE["US"].keys()),
        "HK": list(UNIVERSE["HK"].keys()),
        "JP": list(UNIVERSE["JP"].keys()),
        "KR": list(UNIVERSE["KR"].keys()),
        "CN": list(UNIVERSE["CN"].keys()),
    }

def core_universe():
    active = build_active_universe()
    return {m: active[m][:CORE_N[m]] for m in CORE_N}


def active_universe(mode: str) -> dict:
    return build_active_universe() if mode == "full" else core_universe()

def _fetch_market(market: str, ticker_dict):
    flat = list(ticker_dict.keys()) if isinstance(ticker_dict, dict) else list(ticker_dict)

    batches = list(data_io.chunk(flat, PIPELINE_BATCH_SIZE))
    n = len(batches)

    results = {}
    log.info("[%s] START — %d tickers / %d batches", market, len(flat), n)

    # FIX: KR/CN rate limit strict
    if market in ["KR", "CN"]:
        time.sleep(2)

    for i, batch in enumerate(batches, 1):
        tickers = tuple(batch)
        #tickers = tuple(t for t, _ in batch)
        #tickers = tuple(t for t in batch)
        _upd(market=market, batch=i, total_batches=n)
        log.info("[%s] batch %d/%d (%d tickers) ...", market, i, n, len(tickers))
        t0 = time.time()

        try:
            raw = data_io.fetch_batch(tickers)

            ok = []
            for k, v in raw.items():
              if v is not None and len(v) > 0:
                ok.append(k)

            print("OK =", market, len(ok), ok[:5])

            if not ok:
              log.warning("[%s] EMPTY BATCH %s", market, tickers)
              continue

        except Exception:
            log.exception("[%s] batch %d/%d FAILED", market, i, n)
            _upd(last_error=f"{market} batch {i} failed")
            continue  # ข้ามไปทำ batch ถัดไปหากดึงข้อมูลล้มเหลว

        ok_count = len(ok)

        with _lock:
            FETCH_STATE["cache_misses"] += 1
            FETCH_STATE["tickers_done"] += ok_count

        for t in ok:
          results[t] = raw[t]

        log.info("[%s] batch %d/%d DONE %.1fs", market, i, n, time.time()-t0)
        time.sleep(FETCH_RATE_DELAY)

    log.info("[%s] END — %d/%d loaded", market, len(results), len(flat))
    return market, results

def fetch_universe(active: dict):
    combined:      dict[str, pd.DataFrame] = {}
    ticker_meta:   dict[str, dict]         = {}
    fetch_results: dict[str, dict]         = {m: {} for m in active}
    markets = list(active.keys())
    total   = sum(len(v) for v in active.values())
    t0 = time.time()
    _upd(stage="fetching", market="", batch=0, total_batches=0,
         markets_done=[], markets_total=markets,
         tickers_done=0, tickers_total=total,
         started=t0, last_error="", cache_hits=0, cache_misses=0)
    log.info("=== fetch_universe START %s (%d tickers) ===", markets, total)

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
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
                    combined[t]      = eng.add_indicators(df)
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
        executor.shutdown(wait=False)  # KEY FIX: ไม่รอ thread ค้าง

    log.info("=== fetch_universe END %d tickers %.1fs ===", len(combined), time.time()-t0)
    _upd(stage="computing")
    return combined, ticker_meta, fetch_results

def _pct_change(series: pd.Series, n: int) -> float | None:
    try:
        if len(series) <= n or series.iloc[-1-n] == 0: return None
        val = (series.iloc[-1] / series.iloc[-1-n] - 1) * 100
        return None if (np.isnan(val) or np.isinf(val)) else round(float(val), 2)
    except Exception:
        return None

def compute_dashboard(combined, ticker_meta, fetch_results, active) -> dict:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    sync    = data_io.sync_report(fetch_results, active)
    if not combined:
        _upd(stage="error", last_error="no data")
        return {"ok": False, "error": "ดึงข้อมูลจาก Yahoo Finance ไม่สำเร็จ",
                "sync": sync, "updated": now_str}
    log.info("compute_dashboard %d tickers", len(combined))

    breadth_rows = []
    for market in active:
        mt = list(fetch_results[market].keys())
        if not mt:
            breadth_rows.append({"flag": FLAGS.get(market,""), "code": market,
                                  "ma50": 0.0, "ma200": 0.0, "chg": 0.0}); continue
        a50, a200, a50_5ago = [], [], []
        for t in mt:
            d = combined[t]; last = d.iloc[-1]
            a50.append(bool(last["Close"] > last["SMA50"]))
            a200.append(bool(last["Close"] > last["SMA200"]))
            if len(d) > 5:
                r5 = d.iloc[-6]
                a50_5ago.append(bool(r5["Close"] > r5["SMA50"]))
        ma50  = round(float(np.nanmean(a50)) * 100, 2) if len(a50) else 0.0
        ma200 = round(float(np.nanmean(a200)) * 100, 2) if len(a200) else 0.0
        chg   = round(ma50 - (float(np.mean(a50_5ago)*100) if a50_5ago else ma50), 2)
        breadth_rows.append({"flag": FLAGS.get(market,""), "code": market,
                              "ma50": ma50, "ma200": ma200, "chg": chg})

    signal_count_5d = {k: 0 for k in SIGNAL_NAMES}
    ticker_signal   = {}
    for t, d in combined.items():
        try:
            sig = eng.run_scanners(d)
            rolled, conf, count = eng.confluence_flags(sig)

            # guard กัน empty series
            count_val = float(count.iloc[-1]) if count is not None and len(count) else 0
            conf_val  = bool(conf.iloc[-1]) if conf is not None and len(conf) else False

            last_rolled = {k: bool(v.iloc[-1]) for k, v in rolled.items() if v is not None and len(v)}

            for k, v in last_rolled.items():
              if v:
                signal_count_5d[k] += 1

            ticker_signal[t] = {
              "rolled": last_rolled,
              "count": int(count_val),
              "confluence": conf_val
            }
        except Exception:
            log.exception("scanner failed: %s", t)
            ticker_signal[t] = {"rolled": {}, "count": 0, "confluence": False}

    rs_now = eng.rs_rating_per_market(
    {t: d for t, d in combined.items() if d is not None and len(d) > 5},
    ticker_meta
)
    blended7 = pd.Series({t: eng.blended_return(d["Close"].iloc[:-7])
                           for t, d in combined.items() if len(d) > 7})
    rs_7 = eng.rs_rating_table(blended7).reindex(rs_now.index).fillna(rs_now)

    ret_1d = pd.Series({
      t: (_pct_change(d["Close"], 1) or 0)
      for t, d in combined.items()
    })

    ret_1m = pd.Series({
      t: (_pct_change(d["Close"], TRADING_DAYS_MONTH) or 0)
      for t, d in combined.items()
    })

    ret_3m = pd.Series({
      t: (_pct_change(d["Close"], TRADING_DAYS_QUARTER) or 0)
      for t, d in combined.items()
    })

    print("RS START")
    rs_now = eng.rs_rating_per_market(combined, ticker_meta)
    print("RS DONE")

    theme_map = {t: m["theme"] for t, m in ticker_meta.items()}

    themes = eng.theme_returns(
      ret_1d / 100,
      ret_1m / 100,
      ret_3m / 100,
      theme_map
    )
    theme_rows = []
    for theme, row in themes.head(THEME_TOP_N).iterrows():
        members = [t for t, th in theme_map.items() if th == theme]
        top2    = sorted(members, key=lambda t: rs_now.get(t, 0), reverse=True)[:2]
        theme_rows.append({"theme": theme, "tickers": [t.split(".")[0] for t in top2],
            "d1": round(float(row["1D"])*100,2),
            "m1": round(float(row["1M"])*100,2),
            "m3": round(float(row["3M"])*100,2)})

    breadth_history_all = {}
    for market in active:
        mt = list(fetch_results.get(market, {}).keys())
        bh = {"dates": [], "ma50": [], "ma200": [], "universe": len(mt)}
        if mt:
            try:
                a50_df  = pd.DataFrame({t: combined[t]["Close"] > combined[t]["SMA50"]  for t in mt})
                a200_df = pd.DataFrame({t: combined[t]["Close"] > combined[t]["SMA200"] for t in mt})
                h50  = eng.market_breadth_history(a50_df,  days=BREADTH_HISTORY_DAYS)
                h200 = eng.market_breadth_history(a200_df, days=BREADTH_HISTORY_DAYS)
                bh["dates"] = [d.strftime("%Y-%m-%d") for d in h50.index]
                bh["ma50"]  = [round(float(v), 2) for v in h50.values]
                bh["ma200"] = [round(float(v), 2) for v in h200.values]
            except Exception:
                log.exception("breadth_history failed: %s", market)
        breadth_history_all[market] = bh

    breadth_history = breadth_history_all.get("US", {"dates":[],"ma50":[],"ma200":[],"universe":0})
    bear_markets  = [r for r in breadth_rows
                     if r["ma50"] < BREADTH_BEAR_THRESHOLD and r["chg"] < BREADTH_BEAR_FALL]
    bear_override = len(bear_markets) >= BREADTH_BEAR_MIN_MKT

    watch = sorted([t for t, s in ticker_signal.items() if s["confluence"]],
        key=lambda t: (ticker_signal[t]["count"], int(float(rs_now.get(t, 0) or 0))), reverse=True)[:WATCHLIST_TOP_N]
    watchlist = []
    for t in watch:
        meta = ticker_meta[t]; d = combined[t]
        watchlist.append({"ticker": t.split(".")[0], "full_ticker": t,
            "name": meta["name"], "theme": meta["theme"],
            "patterns": [k for k in SIGNAL_NAMES if ticker_signal[t]["rolled"].get(k)],
            "pct1d": _pct_change(d["Close"], 1) or 0.0, "rs": int(float(rs_now.get(t, 0) or 0)),
            "market": meta["market"],
            "drawdown_pct": eng.current_drawdown_from_peak(d["Close"]),
            "max_dd_pct":   eng.max_drawdown(d["Close"])})

    movers = eng.rs_movers_7d(rs_now, rs_7, top_n=RS_MOVERS_TOP_N)
    rs_movers = []
    for t, row in movers.iterrows():
        spark = [round(float(v), 4) for v in combined[t]["Close"].tail(10).tolist()
                 if not (np.isnan(v) or np.isinf(v))]
        rs_movers.append({"ticker": t.split(".")[0], "full_ticker": t,
            "rs": int(float(row["RS"] or 0)),
            "drs7": int(float(row["dRS_7D"] or 0)), "spark": spark})

    _upd(stage="done")
    log.info("compute_dashboard DONE")

    combined = {k: v.replace([np.inf, -np.inf], np.nan).fillna(0) if hasattr(v, "replace") else v for k, v in combined.items()}

    return {
        "ok": True,
        "updated": now_str,
        "universe_loaded": len(combined),
        "universe_total": sum(len(v) for v in active.values()),
        "sync": sync,
        "breadth": breadth_rows,
        "breadth_history_us": breadth_history,
        "breadth_history_all": breadth_history_all,
        "stat_cards": {**signal_count_5d, "total": len(combined)},
        "watchlist": watchlist,
        "theme_movers": theme_rows,
        "rs_movers": rs_movers,
        "bear_override": bear_override,
        "rs_scope": "per-market",
        "markets": {
    m: [
        t for t in combined.keys()
        if ticker_meta.get(t, {}).get("market") == m
    ]
    for m in ["US", "HK", "JP", "KR", "CN"]
},
    }
