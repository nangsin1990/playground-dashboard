"""
screener.py — Stock Screener Engine
=====================================
Filter the full leadership universe by any combination of:
  - RS Rating range
  - Trend Template score (0-4)
  - Accumulation score
  - Volume ratio
  - Proximity to 52W high
  - Return (1d / 1m / 3m)
  - Market
  - Theme / Sector
  - Signals (VDU, PPBP, BGU, 52W)

Returns rows sorted by chosen field, ready for table display.
No extra data fetching — reuses leadership board data.
"""

from __future__ import annotations
from datetime import datetime

import data_engine as eng
import pipeline
import pandas as pd

from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA

@ttl_cache(CACHE_TTL_DATA)
def _get_all_rows(mode: str) -> list[dict]:
    """Build the full screener row set (cached same as leadership)."""
    import leadership as lb

    # ✨ FIX: Now this call is correct because build_leadership_board is refactored.
    result = lb.build_leadership_board(mode=mode)

    if not result.get("ok"):
        return []

    # Flatten all unique rows from all tabs
    seen, rows = set(), []
    for key in ["overall", "top_rs", "top_momentum", "near_breakout",
                "institutional", "volume_surge", "trend_template"]:
        for r in result.get(key, []):
            t = r["ticker"]
            if t not in seen:
                seen.add(t)
                rows.append(r)
    return rows

def apply_filters(rows: list[dict], params: dict) -> list[dict]:
    # ... (function body is unchanged) ...
    def n(key, default=None):
        v = params.get(key)
        return float(v) if v is not None and v != "" else default
    # ...
    return out

def fetch_screener(mode: str, params: dict, sort_by: str = "ls",
                   sort_desc: bool = True, limit: int = 200) -> dict:
    """
    ✨ FIX: Changed limit to 200 to match frontend.
    Main entry: filter + sort + return screener results.
    """
    rows = _get_all_rows(mode)
    if not rows:
        return {"ok": False, "error": "No data available", "rows": [],
                "updated": datetime.now().strftime("%d/%m/%Y %H:%M")}

    # Apply filters before sorting
    filtered_rows = apply_filters(rows, params)

    # Sort
    def sort_key(r):
        v = r.get(sort_by)
        if v is None:
            # For ascending sort on 'prox_52w', None should be last
            if sort_by == 'prox_52w' and not sort_desc:
                 return 999999
            return -999999 if sort_desc else 999999
        return float(v)

    # Special case for prox_52w which should be sorted ascending by default
    is_prox_sort = sort_by == 'prox_52w'
    final_sort_desc = not is_prox_sort if sort_desc else is_prox_sort

    filtered_rows.sort(key=sort_key, reverse=final_sort_desc)

    final_rows = filtered_rows[:limit]

    return {
        "ok":      True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_universe": len(rows),
        "total_matched":  len(filtered_rows),
        "rows":    final_rows,
        "sort_by": sort_by,
        "params":  params,
    }
