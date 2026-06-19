"""
Thematic Matrix Engine
======================
คำนวณ return per theme/sector สำหรับ Thematic Matrix หน้า

Output per theme:
  - theme name, market filter
  - 1D / 1M / 3M equal-weight average return
  - momentum score (1M+3M)
  - member list with per-stock returns
  - avg RS Rating of members
  - top tickers by RS
  - member count
"""

from __future__ import annotations
from datetime import datetime
from functools import lru_cache

import numpy as np
import pandas as pd

import data_engine as eng
import pipeline
from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_DATA, THEMATIC_TOP_TICKERS, THEMATIC_MAX_MEMBERS,
    TRADING_DAYS_MONTH, TRADING_DAYS_QUARTER,
)


  # 15 min


@ttl_cache(CACHE_TTL_DATA)
def fetch_thematic(mode: str = "core") -> dict:
    active = pipeline.active_universe(mode)
    combined, ticker_meta, fetch_results = pipeline.fetch_universe(active)

    if not combined:
        return {"ok": False, "error": "No data from yfinance"}

    # RS Ratings
    blended = pd.Series({t: eng.blended_return(d["Close"]) for t, d in combined.items()})
    rs_now = eng.rs_rating_table(blended)

    # Per-ticker returns
    rows = {}
    for t, d in combined.items():
        meta = ticker_meta.get(t, {})
        ret_1d = float(d["Close"].iloc[-1] / d["Close"].iloc[-2] - 1) * 100 if len(d) > 1 else 0.0
        ret_1m = float(d["Close"].iloc[-1] / d["Close"].iloc[-21] - 1) * 100 if len(d) > 21 else 0.0
        ret_3m = float(d["Close"].iloc[-1] / d["Close"].iloc[-63] - 1) * 100 if len(d) > 63 else 0.0
        rows[t] = {
            "ticker": t.split(".")[0],
            "name": meta.get("name", t),
            "theme": meta.get("theme", "Unknown"),
            "market": meta.get("market", "US"),
            "d1": round(ret_1d, 3),
            "m1": round(ret_1m, 3),
            "m3": round(ret_3m, 3),
            "rs": int(rs_now.get(t, 0)),
        }

    # Aggregate by theme
    theme_groups: dict[str, list] = {}
    for t, r in rows.items():
        th = r["theme"]
        if th not in theme_groups:
            theme_groups[th] = []
        theme_groups[th].append(r)

    themes = []
    for theme, members in theme_groups.items():
        if not members:
            continue
        d1 = float(np.mean([m["d1"] for m in members]))
        m1 = float(np.mean([m["m1"] for m in members]))
        m3 = float(np.mean([m["m3"] for m in members]))
        avg_rs = int(np.mean([m["rs"] for m in members]))
        # top tickers by RS
        top_tickers = [m["ticker"] for m in sorted(members, key=lambda x: x["rs"], reverse=True)[:THEMATIC_TOP_TICKERS]]
        # market from first member
        # Market: use majority vote (handles mixed ETF themes correctly)
        from collections import Counter
        market = Counter(m["market"] for m in members).most_common(1)[0][0] if members else "US" 
        # sort members by 1M return desc
        sorted_members = sorted(members, key=lambda x: x["m1"], reverse=True)

        themes.append({
            "theme": theme,
            "market": market,
            "count": len(members),
            "d1": round(d1, 3),
            "m1": round(m1, 3),
            "m3": round(m3, 3),
            "score": round(m1 + m3, 3),
            "avg_rs": avg_rs,
            "tickers": top_tickers,
            "members": [
                {"ticker": m["ticker"], "name": m["name"], "d1": m["d1"], "m1": m["m1"], "m3": m["m3"], "rs": m["rs"]}
                for m in sorted_members[:THEMATIC_MAX_MEMBERS]
            ],
        })

    # Sort by score desc
    themes.sort(key=lambda x: x["score"], reverse=True)

    return {
        "ok": True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "universe_loaded": len(combined),
        "themes": themes,
    }
