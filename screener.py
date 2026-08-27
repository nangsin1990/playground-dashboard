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

def _get_all_rows(mode: str) -> list[dict]:
    """Build the full screener row set (cached same as leadership)."""
    import leadership as lb

    # ✨ FIX: การเรียก lb.build_leadership_board(mode=mode) ตอนนี้ถูกต้องแล้ว
    # เพราะ leadership.py ที่แก้ไขใหม่สามารถจัดการ request นี้ได้
    result = lb.build_leadership_board(mode=mode)

    if not result.get("ok"):
        return []

    # Flatten all unique rows from all tabs
    seen, rows = set(), []
    # ✨ FIX: ใช้ 'ticker' เป็น key ในการ de-duplicate เพราะ 'symbol' อาจซ้ำกันข้ามตลาด
    for key in ["overall", "top_rs", "top_momentum", "near_breakout",
                "institutional", "volume_surge", "trend_template"]:
        for r in result.get(key, []):
            t = r["ticker"]
            if t not in seen:
                seen.add(t)
                rows.append(r)
    return rows

def apply_filters(rows: list[dict], params: dict) -> list[dict]:
    def n(key, default=None):
        v = params.get(key)
        if v is None or v == "":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    rs_min = n("rs_min")
    rs_max = n("rs_max")
    trend_min = n("trend_min")
    accum_min = n("accum_min")
    vol_min = n("vol_min")
    prox_max = n("prox_max")
    ls_min = n("ls_min")
    r1m_min = n("r1m_min")
    r3m_min = n("r3m_min")
    drs7_min = n("drs7_min")
    market = (params.get("market") or "").strip()
    theme = (params.get("theme") or "").strip().lower()
    signal = (params.get("signal") or "").strip().upper()

    ready_min = n("vcp_ready_min")
    signal_flag = {
        "VDU": "is_vdu",
        "PPBP": "is_pocket",
        "BGU": "is_bgu",
        "52W": "is_near_52w",
        "VCP": "is_vcp",
    }.get(signal)

    out = []
    for r in rows:
        if rs_min is not None and (r.get("rs") or 0) < rs_min:
            continue
        if rs_max is not None and (r.get("rs") or 0) > rs_max:
            continue
        if trend_min is not None and (r.get("trend_score") or 0) < trend_min:
            continue
        if accum_min is not None and (r.get("accum_score") or 0) < accum_min:
            continue
        if vol_min is not None and (r.get("vol_ratio") or 0) < vol_min:
            continue
        if prox_max is not None and (r.get("prox_52w") or 0) > prox_max:
            continue
        if ls_min is not None and (r.get("ls") or 0) < ls_min:
            continue
        if r1m_min is not None and (r.get("r1m") if r.get("r1m") is not None else -9999) < r1m_min:
            continue
        if r3m_min is not None and (r.get("r3m") if r.get("r3m") is not None else -9999) < r3m_min:
            continue
        if drs7_min is not None and (r.get("drs7") or 0) < drs7_min:
            continue
        if market and r.get("market") != market:
            continue
        if theme and theme not in (r.get("theme") or "").lower():
            continue
        if signal_flag and not r.get(signal_flag):
            continue
        if ready_min is not None and (r.get("vcp_ready") or 0) < ready_min:
            continue
        out.append(r)
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

    def sort_key(r):
        v = r.get(sort_by)
        if v is None:
            return -999999 if sort_desc else 999999
        return float(v)

    filtered_rows.sort(key=sort_key, reverse=sort_desc)

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
