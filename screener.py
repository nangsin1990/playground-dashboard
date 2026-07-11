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

    # ✨ FIX: แก้ไขการเรียกใช้ `build_leadership_board` ให้ถูกต้อง
    # ฟังก์ชันนี้จัดการการดึงข้อมูลและคำนวณทั้งหมดด้วยตัวเองแล้ว
    result = lb.build_leadership_board(mode=mode)

    if not result.get("ok"):
        return []

    # Flatten all unique rows from all tabs
    seen, rows = set(), []
    for key in ["overall", "top_rs", "top_momentum", "near_breakout",
                "institutional", "volume_surge", "trend_template"]:
        for r in result.get(key, []):
            # ใช้ ticker เต็ม (เช่น 'AAPL') เป็น unique key
            t = r["ticker"]
            if t not in seen:
                seen.add(t)
                rows.append(r)
    return rows

def apply_filters(rows: list[dict], params: dict) -> list[dict]:
    """Apply screener filter params to row list."""

    def n(key, default=None):
        v = params.get(key)
        return float(v) if v is not None and v != "" else default

    rs_min      = n("rs_min", 0)
    rs_max      = n("rs_max", 99)
    trend_min   = n("trend_min", 0)
    accum_min   = n("accum_min", -1)
    vol_min     = n("vol_min", 0)
    prox_max    = n("prox_max", 100)   # % below 52W high (lower = closer to high)
    r1m_min     = n("r1m_min")
    r3m_min     = n("r3m_min")
    ls_min      = n("ls_min", 0)
    drs7_min    = n("drs7_min")

    market_f    = params.get("market", "")         # "US" | "" (all)
    theme_f     = params.get("theme", "").lower()  # partial match
    signal_f    = params.get("signal", "")         # "VDU"|"PPBP"|"BGU"|"52W"|""

    out = []
    for r in rows:
        if not (rs_min <= r.get("rs", 0) <= rs_max):
            continue
        if r.get("trend_score", 0) < trend_min:
            continue
        if r.get("accum_score", 0) < accum_min:
            continue
        if r.get("vol_ratio", 0) < vol_min:
            continue
        if r.get("prox_52w", 100) > prox_max:
            continue
        if r1m_min is not None and (r.get("r1m") or 0) < r1m_min:
            continue
        if r3m_min is not None and (r.get("r3m") or 0) < r3m_min:
            continue
        if r.get("ls", 0) < ls_min:
            continue
        if drs7_min is not None and r.get("drs7", 0) < drs7_min:
            continue
        if market_f and r.get("market", "") != market_f:
            continue
        if theme_f and theme_f not in r.get("theme", "").lower():
            continue
        if signal_f and not r.get(f"is_{signal_f.lower()}", False):
            continue
        out.append(r)

    return out


def fetch_screener(mode: str, params: dict, sort_by: str = "ls",
                   sort_desc: bool = True, limit: int = 100) -> dict:
    """
    Main entry: filter + sort + return screener results.
    """
    rows = _get_all_rows(mode)
    if not rows:
        return {"ok": False, "error": "No data available", "rows": [],
                "updated": datetime.now().strftime("%d/%m/%Y %H:%M")}

    filtered = apply_filters(rows, params)

    # Sort
    def sort_key(r):
        v = r.get(sort_by)
        if v is None:
            return -999999 if sort_desc else 999999
        return float(v)

    filtered.sort(key=sort_key, reverse=sort_desc)
    filtered = filtered[:limit]

    return {
        "ok":      True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total_universe": len(rows),
        "total_matched":  len(filtered),
        "rows":    filtered,
        "sort_by": sort_by,
        "params":  params,
    }
