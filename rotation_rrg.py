# FILE: rotation_rrg.py

from __future__ import annotations
from datetime import datetime

try:
    from personal_watchlist import PERSONAL_TICKERS
except Exception:
    PERSONAL_TICKERS = []

import pandas as pd

from cache_utils import ttl_cache
import data_engine as eng
import data_io
from universe import RRG_US_SECTORS, RRG_GLOBAL_UNIVERSE, RRG_US_THEMES, BENCHMARK
from constants import CACHE_TTL_DATA, RRG_MIN_TICKERS, RRG_MIN_HISTORY, RRG_CLAMP_LO, RRG_CLAMP_HI

UNIVERSE_MAP = {
    "GLOBAL": RRG_GLOBAL_UNIVERSE,
    "US_SECTORS": RRG_US_SECTORS,
    "US_THEMES": RRG_US_THEMES,
}


def _label(meta) -> str:
    if isinstance(meta, (tuple, list)):
        return str(meta[0])
    return str(meta or "")


def _weekly_from_batch(tickers: list[str]) -> pd.DataFrame | None:
    raw = data_io.fetch_batch(tuple(tickers))
    series = {}
    for t, df in (raw or {}).items():
        if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
            continue
        s = df["Close"].copy()
        s.index = pd.to_datetime(s.index, errors="coerce")
        s = s[~s.index.isna()].dropna()
        if s.empty:
            continue
        series[t] = s.resample("W-FRI").last()
    if len(series) < 2:
        return None
    out = pd.DataFrame(series).ffill().dropna(how="all")
    return out if not out.empty else None


def _fit_bounds(rrg_list: list[dict]) -> dict:
    xs, ys = [100.0], [100.0]
    for row in rrg_list:
        if row.get("rs_ratio") is not None:
            xs.append(float(row["rs_ratio"]))
        if row.get("rs_momentum") is not None:
            ys.append(float(row["rs_momentum"]))
        for pt in (row.get("tail") or [])[-12:]:
            if not pt or len(pt) < 2:
                continue
            if pt[0] is not None:
                xs.append(float(pt[0]))
            if pt[1] is not None:
                ys.append(float(pt[1]))
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    pad_x = max(4.0, (x1 - x0) * 0.18)
    pad_y = max(4.0, (y1 - y0) * 0.18)
    x0, x1 = x0 - pad_x, x1 + pad_x
    y0, y1 = y0 - pad_y, y1 + pad_y
    if x1 - x0 < 12:
        mid = (x0 + x1) / 2
        x0, x1 = mid - 6, mid + 6
    if y1 - y0 < 12:
        mid = (y0 + y1) / 2
        y0, y1 = mid - 6, mid + 6
    return {
        "lo": RRG_CLAMP_LO,
        "hi": RRG_CLAMP_HI,
        "x0": round(x0, 2),
        "x1": round(x1, 2),
        "y0": round(y0, 2),
        "y1": round(y1, 2),
    }


def _headline(rrg_list: list[dict]) -> str:
    counts = {"Leading": 0, "Weakening": 0, "Lagging": 0, "Improving": 0}
    moved = []
    for row in rrg_list:
        q = row.get("quadrant")
        if q in counts:
            counts[q] += 1
        if row.get("just_moved"):
            moved.append(f"{row.get('short')} จาก {row.get('prev_quadrant')} เข้า {q}")
    n = max(1, sum(counts.values()))
    if counts["Improving"] == 0 and counts["Lagging"] >= n * 0.45:
        base = "ส่วนใหญ่ยังแพ้เกณฑ์และยังไม่เริ่มฟื้น Lagging ไม่ได้แปลว่ากำลังหมุนเข้า"
    elif counts["Improving"] > 0 and counts["Leading"] <= counts["Lagging"]:
        base = "ยังมีตัวที่แพ้เกณฑ์ แต่เริ่มมีตัวโมเมนตัมพลิกขึ้น ดูแถวที่ติดป้ายเพิ่งย้ายช่อง"
    elif counts["Leading"] >= counts["Lagging"] and counts["Weakening"] >= counts["Leading"]:
        base = "ของที่นำเริ่มชะลอ วงจรปกติเดินจาก Leading ไป Weakening ก่อน"
    elif counts["Leading"] > 0 and counts["Improving"] > 0:
        base = "มีทั้งตัวที่นำอยู่และตัวที่เริ่มฟื้นจากช่องแพ้"
    else:
        base = "ดูทิศหางประกอบช่อง ไม่ใช่แค่ว่าอยู่ Leading หรือ Lagging"
    if moved[:3]:
        return base + " · " + " | ".join(moved[:3])
    return base


@ttl_cache(CACHE_TTL_DATA)
def fetch_rotation(mode: str = "core", market: str = "GLOBAL") -> dict:
    try:
        selected_universe = UNIVERSE_MAP.get(market)
        if not selected_universe:
            return {"ok": False, "error": f"Invalid market specified for RRG: {market}"}

        bench_map = {
            "GLOBAL": BENCHMARK.get("GLOBAL", "VT"),
            "US_SECTORS": "SPY",
            "US_THEMES": "SPY",
        }
        benchmark_ticker = bench_map.get(market) or BENCHMARK.get(market, "SPY")
        all_tickers = list(dict.fromkeys(list(selected_universe.keys()) + [benchmark_ticker]))

        df_weekly = _weekly_from_batch(all_tickers)
        if df_weekly is None:
            return {"ok": False, "error": f"Could not fetch weekly prices for {market}"}

        if benchmark_ticker not in df_weekly.columns or df_weekly[benchmark_ticker].dropna().empty:
            fallback = "SPY" if "SPY" in df_weekly.columns else df_weekly.columns[0]
            benchmark_ticker = str(fallback)

        valid_tickers = [
            t for t in selected_universe.keys()
            if t != benchmark_ticker
            and t in df_weekly.columns
            and df_weekly[t].notna().sum() > RRG_MIN_HISTORY
        ]
        if len(valid_tickers) < RRG_MIN_TICKERS:
            return {"ok": False, "error": "No assets with sufficient historical data found in the selected universe."}

        rrg_metrics = eng.calculate_rrg_metrics(df_weekly, valid_tickers, benchmark_ticker)
        if not rrg_metrics:
            return {"ok": False, "error": "RRG computation failed. Not enough historical data for comparison."}

        rrg_list = []
        for ticker, data in rrg_metrics.items():
            tail = data.get("tail") or []
            avg_rs = round(sum(p[0] for p in tail) / len(tail), 2) if tail else data["jrs"]
            rrg_list.append({
                "theme": _label(selected_universe.get(ticker, ticker)),
                "short": ticker,
                "quadrant": data["quadrant"],
                "rs_ratio": data["jrs"],
                "rs_momentum": data["jmo"],
                "tail": tail,
                "avg_rs": avg_rs,
                "persistence_weeks": data.get("persistence_weeks", 0),
                "quadrant_history": data.get("quadrant_history", []),
                "prev_quadrant": data.get("prev_quadrant"),
                "just_moved": data.get("just_moved", False),
                "ret_4w": data.get("ret_4w"),
                "ret_13w": data.get("ret_13w"),
            })

        q_rank = {"Leading": 0, "Improving": 1, "Weakening": 2, "Lagging": 3}
        rrg_list.sort(key=lambda r: (
            0 if r.get("just_moved") else 1,
            q_rank.get(r["quadrant"], 9),
            -(r.get("persistence_weeks") or 0),
            -(r.get("rs_ratio") or 0),
        ))

        n = max(1, len(rrg_list))
        by_rs = sorted((r.get("rs_ratio") or 0) for r in rrg_list)
        by_mo = sorted((r.get("rs_momentum") or 0) for r in rrg_list)
        owned = set(PERSONAL_TICKERS)
        weekday = datetime.now().weekday()
        last_bar_provisional = weekday < 4
        for row in rrg_list:
            rs = row.get("rs_ratio") or 0
            mo = row.get("rs_momentum") or 0
            row["rs_pct"] = round(sum(1 for x in by_rs if x <= rs) / n * 100)
            row["mo_pct"] = round(sum(1 for x in by_mo if x <= mo) / n * 100)
            row["owned"] = row.get("short") in owned
            row["last_bar_provisional"] = last_bar_provisional

        max_n = max((len(r.get("quadrant_history") or []) for r in rrg_list), default=0)
        breadth = []
        for i in range(max_n):
            counts = {"Leading": 0, "Weakening": 0, "Lagging": 0, "Improving": 0}
            for r in rrg_list:
                hist = r.get("quadrant_history") or []
                idx = i - (max_n - len(hist))
                if idx < 0:
                    continue
                q = hist[idx]
                if q in counts:
                    counts[q] += 1
            breadth.append(counts)

        return {
            "ok": True,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "market": market,
            "benchmark": benchmark_ticker,
            "rrg": rrg_list,
            "headline": _headline(rrg_list),
            "chart_bounds": _fit_bounds(rrg_list),
            "breadth": breadth,
            "last_bar_provisional": last_bar_provisional,
            "owned_tickers": sorted(owned),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
