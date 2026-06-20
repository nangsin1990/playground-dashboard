"""
pipeline.py — Orchestration layer
v3: per-market RS rating, drawdown in watchlist, data lag in sync
"""

from __future__ import annotations
from datetime import datetime

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

SIGNAL_NAMES = ["VDU", "PPBP", "BGU", "52W"]


def core_universe() -> dict:
    return {m: dict(list(UNIVERSE[m].items())[:CORE_N[m]]) for m in CORE_N}


def active_universe(mode: str) -> dict:
    return UNIVERSE if mode == "full" else core_universe()


# ── 1) Fetch (batched with rate-limit delay) ──────────────────────────────────
def fetch_universe(active: dict):
    import time
    combined:      dict[str, pd.DataFrame] = {}
    ticker_meta:   dict[str, dict]         = {}
    fetch_results: dict[str, dict[str, pd.DataFrame]] = {m: {} for m in active}

    flat = [(t, m, name, theme)
            for m, tk in active.items()
            for t, (name, theme) in tk.items()]

    for i, batch in enumerate(data_io.chunk(flat, PIPELINE_BATCH_SIZE)):
        tickers = tuple(t for t, *_ in batch)
        result  = data_io.fetch_batch(tickers)

        for t, m, name, theme in batch:
            df = result.get(t)
            if df is None:
                continue
            fetch_results[m][t] = df
            combined[t]         = eng.add_indicators(df)
            ticker_meta[t]      = {"market": m, "name": name, "theme": theme}

        if i > 0 and FETCH_RATE_DELAY > 0:
            time.sleep(FETCH_RATE_DELAY)

    return combined, ticker_meta, fetch_results


# ── 2) Safe return helper (NaN-proof) ────────────────────────────────────────
def _pct_change(series: pd.Series, n: int) -> float | None:
    if len(series) <= n or series.iloc[-1 - n] == 0:
        return None
    val = (series.iloc[-1] / series.iloc[-1 - n] - 1) * 100
    return None if (np.isnan(val) or np.isinf(val)) else round(float(val), 2)


# ── 3) Compute dashboard ─────────────────────────────────────────────────────
def compute_dashboard(combined: dict, ticker_meta: dict,
                      fetch_results: dict, active: dict) -> dict:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    sync    = data_io.sync_report(fetch_results, active)

    if not combined:
        return {"ok": False, "error": "ดึงข้อมูลจาก Yahoo Finance ไม่สำเร็จเลย",
                "sync": sync, "updated": now_str}

    # ── Market breadth ───────────────────────────────────────────────────────
    breadth_rows = []
    for market in active:
        mtickers = list(fetch_results[market].keys())
        if not mtickers:
            breadth_rows.append({"flag": FLAGS.get(market, ""), "code": market,
                                  "ma50": 0.0, "ma200": 0.0, "chg": 0.0})
            continue
        a50, a200, a50_5ago = [], [], []
        for t in mtickers:
            d    = combined[t]
            last = d.iloc[-1]
            a50.append(bool(last["Close"] > last["SMA50"]))
            a200.append(bool(last["Close"] > last["SMA200"]))
            if len(d) > 5:
                r5 = d.iloc[-6]
                a50_5ago.append(bool(r5["Close"] > r5["SMA50"]))
        ma50  = round(float(np.mean(a50)  * 100), 2)
        ma200 = round(float(np.mean(a200) * 100), 2)
        chg   = round(ma50 - (float(np.mean(a50_5ago) * 100) if a50_5ago else ma50), 2)
        breadth_rows.append({"flag": FLAGS.get(market, ""), "code": market,
                              "ma50": ma50, "ma200": ma200, "chg": chg})

    # ── Scanners / confluence ────────────────────────────────────────────────
    signal_count_5d: dict[str, int] = {k: 0 for k in SIGNAL_NAMES}
    ticker_signal:   dict[str, dict] = {}
    for t, d in combined.items():
        sig                 = eng.run_scanners(d)
        rolled, conf, count = eng.confluence_flags(sig)
        last_rolled         = {k: bool(v.iloc[-1]) for k, v in rolled.items()}
        for k, v in last_rolled.items():
            if v:
                signal_count_5d[k] += 1
        ticker_signal[t] = {
            "rolled":     last_rolled,
            "count":      int(count.iloc[-1]),
            "confluence": bool(conf.iloc[-1]),
        }

    # ── RS Rating — per market (no cross-currency mixing) ────────────────────
    rs_now = eng.rs_rating_per_market(combined, ticker_meta)
    blended_7 = pd.Series({t: eng.blended_return(d["Close"].iloc[:-7])
                            for t, d in combined.items() if len(d) > 7})
    rs_7 = eng.rs_rating_table(blended_7).reindex(rs_now.index).fillna(rs_now)

    # ── Theme rotation ───────────────────────────────────────────────────────
    ret_1d = pd.Series({t: _pct_change(d["Close"], 1)  for t, d in combined.items()
                         if _pct_change(d["Close"], 1) is not None})
    ret_1m = pd.Series({t: _pct_change(d["Close"], TRADING_DAYS_MONTH)   for t, d in combined.items()
                         if _pct_change(d["Close"], TRADING_DAYS_MONTH) is not None})
    ret_3m = pd.Series({t: _pct_change(d["Close"], TRADING_DAYS_QUARTER) for t, d in combined.items()
                         if _pct_change(d["Close"], TRADING_DAYS_QUARTER) is not None})
    theme_map = {t: m["theme"] for t, m in ticker_meta.items()}
    themes    = eng.theme_returns(ret_1d / 100, ret_1m / 100, ret_3m / 100, theme_map)

    theme_rows = []
    for theme, row in themes.head(THEME_TOP_N).iterrows():
        members = [t for t, th in theme_map.items() if th == theme]
        top2    = sorted(members, key=lambda t: rs_now.get(t, 0), reverse=True)[:2]
        theme_rows.append({
            "theme":   theme,
            "tickers": [t.split(".")[0] for t in top2],
            "d1":  round(float(row["1D"]) * 100, 2),
            "m1":  round(float(row["1M"]) * 100, 2),
            "m3":  round(float(row["3M"]) * 100, 2),
        })

    # ── Breadth history (20d) ────────────────────────────────────────────────
    breadth_history_all = {}
    for market in active:
        mt = list(fetch_results.get(market, {}).keys())
        bh = {"dates": [], "ma50": [], "ma200": [], "universe": len(mt)}
        if mt:
            a50_df  = pd.DataFrame({t: combined[t]["Close"] > combined[t]["SMA50"]  for t in mt})
            a200_df = pd.DataFrame({t: combined[t]["Close"] > combined[t]["SMA200"] for t in mt})
            h50     = eng.market_breadth_history(a50_df,  days=BREADTH_HISTORY_DAYS)
            h200    = eng.market_breadth_history(a200_df, days=BREADTH_HISTORY_DAYS)
            bh["dates"] = [d.strftime("%Y-%m-%d") for d in h50.index]
            bh["ma50"]  = [round(float(v), 2) for v in h50.values]
            bh["ma200"] = [round(float(v), 2) for v in h200.values]
        breadth_history_all[market] = bh

    breadth_history = breadth_history_all.get("US", {"dates": [], "ma50": [], "ma200": [], "universe": 0})

    # ── Bear override ────────────────────────────────────────────────────────
    bear_markets  = [r for r in breadth_rows
                     if r["ma50"] < BREADTH_BEAR_THRESHOLD and r["chg"] < BREADTH_BEAR_FALL]
    bear_override = len(bear_markets) >= BREADTH_BEAR_MIN_MKT

    # ── Confluence Watchlist (with drawdown) ─────────────────────────────────
    watch = sorted(
        [t for t, s in ticker_signal.items() if s["confluence"]],
        key=lambda t: (ticker_signal[t]["count"], int(rs_now.get(t, 0))),
        reverse=True,
    )[:WATCHLIST_TOP_N]

    watchlist = []
    for t in watch:
        meta   = ticker_meta[t]
        d      = combined[t]
        pct1d  = _pct_change(d["Close"], 1) or 0.0
        dd     = eng.current_drawdown_from_peak(d["Close"])
        max_dd = eng.max_drawdown(d["Close"])
        watchlist.append({
            "ticker":       t.split(".")[0],
            "full_ticker":  t,
            "name":         meta["name"],
            "theme":        meta["theme"],
            "patterns":     [k for k in SIGNAL_NAMES if ticker_signal[t]["rolled"].get(k)],
            "pct1d":        pct1d,
            "rs":           int(rs_now.get(t, 0)),
            "market":       meta["market"],
            "drawdown_pct": dd,
            "max_dd_pct":   max_dd,
        })

    # ── RS Movers ────────────────────────────────────────────────────────────
    movers    = eng.rs_movers_7d(rs_now, rs_7, top_n=RS_MOVERS_TOP_N)
    rs_movers = []
    for t, row in movers.iterrows():
        spark = [round(float(v), 4) for v in combined[t]["Close"].tail(10).tolist()
                 if not (np.isnan(v) or np.isinf(v))]
        rs_movers.append({
            "ticker":      t.split(".")[0],
            "full_ticker": t,
            "rs":          int(row["RS"]),
            "drs7":        int(row["dRS_7D"]),
            "spark":       spark,
        })

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
        "rs_scope":            "per-market (no cross-currency comparison)",
    }
