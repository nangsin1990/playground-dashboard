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

def _get_all_rows(mode: str) -> list[dict]:
    """ใช้ universe ทั้งก้อนจาก leadership ไม่ใช่แค่แท็บ Top-N."""
    import leadership as lb

    result = lb.build_leadership_board(mode=mode)
    if not result.get("ok"):
        return []

    universe = result.get("universe") or []
    if universe:
        seen, rows = set(), []
        for r in universe:
            t = r.get("ticker")
            if not t or t in seen:
                continue
            seen.add(t)
            rows.append(r)
        return rows

    seen, rows = set(), []
    for key in ["overall", "top_rs", "theme_leaders", "top_momentum", "near_breakout",
                "volume_proxy", "institutional", "volume_surge", "trend_template"]:
        for r in result.get(key) or []:
            t = r.get("ticker")
            if t and t not in seen:
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
    r1d_min = n("r1d_min")
    r1d_max = n("r1d_max")
    price_min = n("price_min")
    price_max = n("price_max")
    dd_max = n("drawdown_max")
    drs7_min = n("drs7_min")
    market = (params.get("market") or "").strip()
    theme = (params.get("theme") or "").strip().lower()
    signal = (params.get("signal") or "").strip().upper()

    ready_min = n("vcp_ready_min")
    pe_max = n("pe_max")
    pe_min = n("pe_min")
    roe_min = n("roe_min")
    mcap_min = n("mcap_min")
    mcap_max = n("mcap_max")
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
        if r1m_min is not None:
            v = r.get("r1m")
            if v is None or v < r1m_min:
                continue
        if r3m_min is not None:
            v = r.get("r3m")
            if v is None or v < r3m_min:
                continue
        if r1d_min is not None:
            v = r.get("r1d")
            if v is None or v < r1d_min:
                continue
        if r1d_max is not None:
            v = r.get("r1d")
            if v is None or v > r1d_max:
                continue
        if price_min is not None:
            v = r.get("price")
            if v is None or v < price_min:
                continue
        if price_max is not None:
            v = r.get("price")
            if v is None or v > price_max:
                continue
        if dd_max is not None:
            v = r.get("drawdown_pct")
            if v is None:
                continue
            if abs(v) > dd_max:
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
        if pe_min is not None:
            v = r.get("pe")
            if v is None or v < pe_min:
                continue
        if pe_max is not None:
            v = r.get("pe")
            if v is None or v > pe_max:
                continue
        if roe_min is not None:
            v = r.get("roe")
            if v is None or v < roe_min:
                continue
        if mcap_min is not None:
            v = r.get("market_cap")
            if v is None or v < mcap_min:
                continue
        if mcap_max is not None:
            v = r.get("market_cap")
            if v is None or v > mcap_max:
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

    fund_keys = ("pe_min", "pe_max", "roe_min", "mcap_min", "mcap_max")
    tech_params = {k: v for k, v in params.items() if k not in fund_keys}
    filtered_rows = apply_filters(rows, tech_params)
    try:
        from fundamentals import attach_fundamentals
        attach_fundamentals(filtered_rows, limit_fetch=min(120, max(len(filtered_rows), 1)))
    except Exception:
        pass
    filtered_rows = apply_filters(filtered_rows, params)

    def sort_key(r):
        v = r.get(sort_by)
        if v is None:
            return float("-inf") if sort_desc else float("inf")
        try:
            return float(v)
        except (TypeError, ValueError):
            return float("-inf") if sort_desc else float("inf")

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
