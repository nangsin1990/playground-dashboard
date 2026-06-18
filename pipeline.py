"""
Orchestration layer: fetch the active universe via data_io (batched),
run it through data_engine, and assemble a single JSON-able dict for the
frontend. No web-framework dependency here -- backend.py just calls
`fetch_universe` then `compute_dashboard`.
"""

from __future__ import annotations
from datetime import datetime

import numpy as np
import pandas as pd

import data_engine as eng
import data_io
from universe import FLAGS, UNIVERSE

CORE_N = {"US": 40, "TH": 30, "HK": 16, "JP": 16, "KR": 12, "CN": 12}
BATCH_SIZE = 60
SIGNAL_NAMES = ["VDU", "PPBP", "BGU", "52W"]


def core_universe() -> dict:
    return {m: dict(list(UNIVERSE[m].items())[:CORE_N[m]]) for m in UNIVERSE}


def active_universe(mode: str) -> dict:
    return UNIVERSE if mode == "full" else core_universe()


# ----------------------------------------------------------------------
# 1) Fetch (batched)
# ----------------------------------------------------------------------
def fetch_universe(active: dict):
    combined: dict[str, pd.DataFrame] = {}
    ticker_meta: dict[str, dict] = {}
    fetch_results: dict[str, dict[str, pd.DataFrame]] = {m: {} for m in active}

    flat = [(t, m, name, theme) for m, tk in active.items() for t, (name, theme) in tk.items()]
    for batch in data_io.chunk(flat, BATCH_SIZE):
        tickers = tuple(t for t, *_ in batch)
        result = data_io.fetch_batch(tickers)
        for t, m, name, theme in batch:
            df = result.get(t)
            if df is None:
                continue
            fetch_results[m][t] = df
            combined[t] = eng.add_indicators(df)
            ticker_meta[t] = {"market": m, "name": name, "theme": theme}

    return combined, ticker_meta, fetch_results


# ----------------------------------------------------------------------
# 2) Compute -> JSON-able dict
# ----------------------------------------------------------------------
def compute_dashboard(combined: dict[str, pd.DataFrame], ticker_meta: dict[str, dict],
                       fetch_results: dict[str, dict[str, pd.DataFrame]], active: dict) -> dict:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    sync = data_io.sync_report(fetch_results, active)

    if not combined:
        return {"ok": False, "error": "ดึงข้อมูลจาก Yahoo Finance ไม่สำเร็จเลย", "sync": sync, "updated": now_str}

    # --- Market breadth -------------------------------------------------
    breadth_rows = []
    for market in active:
        mtickers = list(fetch_results[market].keys())
        if not mtickers:
            breadth_rows.append({"flag": FLAGS[market], "code": market, "ma50": 0.0, "ma200": 0.0, "chg": 0.0})
            continue
        a50, a200, a50_5ago = [], [], []
        for t in mtickers:
            d = combined[t]
            last = d.iloc[-1]
            a50.append(bool(last["Close"] > last["SMA50"]))
            a200.append(bool(last["Close"] > last["SMA200"]))
            if len(d) > 5:
                row5 = d.iloc[-6]
                a50_5ago.append(bool(row5["Close"] > row5["SMA50"]))
        ma50, ma200 = float(np.mean(a50) * 100), float(np.mean(a200) * 100)
        chg = ma50 - (float(np.mean(a50_5ago) * 100) if a50_5ago else ma50)
        breadth_rows.append({"flag": FLAGS[market], "code": market, "ma50": ma50, "ma200": ma200, "chg": chg})

    # --- Scanners / confluence ------------------------------------------
    signal_count_5d = {k: 0 for k in SIGNAL_NAMES}
    ticker_signal: dict[str, dict] = {}
    for t, d in combined.items():
        sig = eng.run_scanners(d)
        rolled, conf, count = eng.confluence_flags(sig)
        last_rolled = {k: bool(v.iloc[-1]) for k, v in rolled.items()}
        for k, v in last_rolled.items():
            if v:
                signal_count_5d[k] += 1
        ticker_signal[t] = {"rolled": last_rolled, "count": int(count.iloc[-1]), "confluence": bool(conf.iloc[-1])}

    # --- RS Rating (1-99) + 7d-ago for dRS -------------------------------
    blended = pd.Series({t: eng.blended_return(d["Close"]) for t, d in combined.items()})
    rs_now = eng.rs_rating_table(blended)
    blended_7 = pd.Series({t: eng.blended_return(d["Close"].iloc[:-7]) for t, d in combined.items() if len(d) > 7})
    rs_7 = eng.rs_rating_table(blended_7).reindex(rs_now.index).fillna(rs_now)

    # --- Theme / sector rotation ------------------------------------------
    ret_1d = pd.Series({t: d["Close"].iloc[-1] / d["Close"].iloc[-2] - 1 for t, d in combined.items() if len(d) > 1})
    ret_1m = pd.Series({t: d["Close"].iloc[-1] / d["Close"].iloc[-21] - 1 for t, d in combined.items() if len(d) > 21})
    ret_3m = pd.Series({t: d["Close"].iloc[-1] / d["Close"].iloc[-63] - 1 for t, d in combined.items() if len(d) > 63})
    theme_map = {t: m["theme"] for t, m in ticker_meta.items()}
    themes = eng.theme_returns(ret_1d, ret_1m, ret_3m, theme_map)

    theme_rows = []
    for theme, row in themes.head(5).iterrows():
        members = [t for t, th in theme_map.items() if th == theme]
        top2 = sorted(members, key=lambda t: rs_now.get(t, 0), reverse=True)[:2]
        theme_rows.append({
            "theme": theme,
            "tickers": [t.split(".")[0] for t in top2],
            "d1": float(row["1D"]) * 100, "m1": float(row["1M"]) * 100, "m3": float(row["3M"]) * 100,
        })

    # --- Breadth history for ALL markets (20d) --------------------------------
    breadth_history_all = {}
    for market in active:
        mt = list(fetch_results.get(market, {}).keys())
        bh = {"dates": [], "ma50": [], "ma200": [], "universe": len(mt)}
        if mt:
            a50_df = pd.DataFrame({t: combined[t]["Close"] > combined[t]["SMA50"] for t in mt})
            a200_df = pd.DataFrame({t: combined[t]["Close"] > combined[t]["SMA200"] for t in mt})
            h50 = eng.market_breadth_history(a50_df, days=20)
            h200 = eng.market_breadth_history(a200_df, days=20)
            bh["dates"] = [d.strftime("%Y-%m-%d") for d in h50.index]
            bh["ma50"] = [round(float(v), 2) for v in h50.values]
            bh["ma200"] = [round(float(v), 2) for v in h200.values]
        breadth_history_all[market] = bh
    # backward-compat alias
    breadth_history = breadth_history_all.get("TH", {"dates": [], "ma50": [], "ma200": [], "universe": 0})
    # Bear Market Override: >=3 markets with MA50 breadth <40% AND falling >5%
    bear_markets = [r for r in breadth_rows if r["ma50"] < 40 and r["chg"] < -5]
    bear_override = len(bear_markets) >= 3

    # --- Confluence Watchlist (top 10) -------------------------------------
    watch = sorted(
        [t for t, s in ticker_signal.items() if s["confluence"]],
        key=lambda t: (ticker_signal[t]["count"], int(rs_now.get(t, 0))),
        reverse=True,
    )[:10]
    watchlist = []
    for t in watch:
        meta = ticker_meta[t]
        d = combined[t]
        pct1d = (d["Close"].iloc[-1] / d["Close"].iloc[-2] - 1) * 100 if len(d) > 1 else 0.0
        watchlist.append({
            "ticker": t.split(".")[0], "full_ticker": t, "name": meta["name"], "theme": meta["theme"],
            "patterns": [k for k in SIGNAL_NAMES if ticker_signal[t]["rolled"].get(k)],
            "pct1d": round(float(pct1d), 2), "rs": int(rs_now.get(t, 0)), "market": meta["market"],
        })

    # --- Top RS Movers (dRS 7D, top 5) --------------------------------------
    movers = eng.rs_movers_7d(rs_now, rs_7, top_n=5)
    rs_movers = []
    for t, row in movers.iterrows():
        rs_movers.append({
            "ticker": t.split(".")[0], "full_ticker": t,
            "rs": int(row["RS"]), "drs7": int(row["dRS_7D"]),
            "spark": [round(float(v), 4) for v in combined[t]["Close"].tail(10).tolist()],
        })

    return {
        "ok": True,
        "updated": now_str,
        "universe_loaded": len(combined),
        "universe_total": sum(len(v) for v in active.values()),
        "sync": sync,
        "breadth": breadth_rows,
        "breadth_history_th": breadth_history,
        "breadth_history_all": breadth_history_all,
        "stat_cards": {**signal_count_5d, "total": len(combined)},
        "watchlist": watchlist,
        "theme_movers": theme_rows,
        "rs_movers": rs_movers,
        "bear_override": bear_override,
    }
