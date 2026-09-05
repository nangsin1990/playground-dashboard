# FILE: earnings_board.py
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA
from economic_calendar import EARNINGS_WATCHLIST, _ETF_BLACKLIST
from personal_watchlist import PERSONAL_TICKERS, ETF_SKIP_EARNINGS


def _naive_index(idx) -> pd.DatetimeIndex:
    out = pd.DatetimeIndex(pd.to_datetime(idx, errors="coerce"))
    if getattr(out, "tz", None) is not None:
        out = out.tz_convert("UTC").tz_localize(None)
    return out


def _universe() -> list[str]:
    seen = []
    for t in list(EARNINGS_WATCHLIST) + list(PERSONAL_TICKERS):
        if t in _ETF_BLACKLIST or t in ETF_SKIP_EARNINGS:
            continue
        if t not in seen:
            seen.append(t)
    return seen


def _gaps_from_history(px: pd.DataFrame, earn_idx: list) -> dict:
    empty = {"avg_gap": None, "max_gap": None, "max_gap_date": None, "n": 0, "gaps": []}
    if px is None or px.empty or not earn_idx:
        return empty
    px = px.copy()
    px.index = _naive_index(px.index)
    if "Close" not in px.columns:
        return empty
    open_col = "Open" if "Open" in px.columns else "Close"
    gaps = []
    for raw in earn_idx:
        try:
            ed = pd.Timestamp(raw)
            if ed.tzinfo is not None:
                ed = ed.tz_convert("UTC").tz_localize(None)
            ed = ed.normalize()
        except Exception:
            continue
        prev = px.loc[px.index < ed]
        nxt = px.loc[px.index >= ed]
        if prev.empty or nxt.empty:
            continue
        prev_close = float(prev["Close"].iloc[-1])
        nxt_open = float(nxt[open_col].iloc[0])
        if prev_close <= 0:
            continue
        gap = (nxt_open / prev_close - 1) * 100
        gaps.append({"date": str(nxt.index[0].date()), "gap": round(gap, 2)})
    if not gaps:
        return empty
    abs_max = max(gaps, key=lambda g: abs(g["gap"]))
    return {
        "avg_gap": round(float(np.mean([g["gap"] for g in gaps])), 2),
        "max_gap": abs_max["gap"],
        "max_gap_date": abs_max["date"],
        "n": len(gaps),
        "gaps": gaps[-8:],
    }


def _today_gap(px: pd.DataFrame, next_date: str | None, past_dates: list) -> float | None:
    if px is None or len(px) < 2:
        return None
    last_day = pd.Timestamp(px.index[-1]).tz_localize(None) if getattr(px.index[-1], "tzinfo", None) else pd.Timestamp(px.index[-1])
    last_day = last_day.normalize()
    if "Close" not in px.columns:
        return None
    prev_close = float(px["Close"].iloc[-2])
    last_open = float(px["Open"].iloc[-1]) if "Open" in px.columns else float(px["Close"].iloc[-1])
    if prev_close <= 0:
        return None
    marks = []
    if next_date:
        marks.append(pd.Timestamp(next_date).normalize())
    marks.extend(pd.Timestamp(d).normalize() for d in past_dates[-3:])
    if any(abs((last_day - m).days) <= 1 for m in marks):
        return round((last_open / prev_close - 1) * 100, 2)
    return None


def _tech_score(px: pd.DataFrame) -> dict:
    out = {
        "above_ma50": False, "above_ma200": False, "rsi": None,
        "off_52w": None, "tech_pts": 0, "notes": [],
    }
    if px is None or len(px) < 30:
        return out
    close = px["Close"]
    last = float(close.iloc[-1])
    ma50 = float(close.rolling(50, min_periods=20).mean().iloc[-1])
    ma200 = float(close.rolling(200, min_periods=50).mean().iloc[-1]) if len(close) >= 50 else None
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if pd.notna(rs.iloc[-1]) else None
    high_52 = float(px["High"].tail(252).max()) if "High" in px else float(close.tail(252).max())
    off = (last / high_52 - 1) * 100 if high_52 else None
    pts = 0
    notes = []
    if last > ma50:
        pts += 1
        notes.append("เหนือ MA50")
    else:
        notes.append("ต่ำกว่า MA50")
    if ma200 is not None and last > ma200:
        pts += 1
        notes.append("เหนือ MA200")
    elif ma200 is not None:
        notes.append("ต่ำกว่า MA200")
    if rsi is not None:
        notes.append(f"RSI {rsi:.0f}")
        if 35 <= rsi <= 68:
            pts += 1
        elif rsi >= 70:
            notes.append("ร้อนเกิน")
        elif rsi <= 30:
            notes.append("ขายมาก")
    if off is not None:
        notes.append(f"ห่างจุดสูงสุด 52 สัปดาห์ {off:.1f}%")
        if off >= -15:
            pts += 1
    out.update({
        "above_ma50": last > ma50,
        "above_ma200": bool(ma200 is not None and last > ma200),
        "rsi": None if rsi is None else round(rsi, 1),
        "off_52w": None if off is None else round(off, 1),
        "tech_pts": pts,
        "notes": notes,
        "price": round(last, 2),
    })
    return out


@ttl_cache(CACHE_TTL_DATA)
def fetch_earnings_board() -> dict:
    try:
        tickers = _universe()
        rows = []
        today = date.today()
        if not tickers:
            return {"ok": True, "updated": datetime.now().strftime("%d/%m/%Y %H:%M"), "count": 0, "rows": [], "note": "ไม่มีหุ้นในรายการงบ"}
        raw = yf.download(tickers, period="5y", auto_adjust=True, group_by="ticker", threads=True, progress=False, timeout=60)

        def _slice(sym: str):
            if raw is None or raw.empty:
                return None
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if sym not in raw.columns.get_level_values(0):
                        return None
                    px = raw[sym].copy()
                else:
                    px = raw.copy()
                px = px.dropna(how="all")
                if px.empty:
                    return None
                px.index = _naive_index(px.index)
                return px
            except Exception:
                return None

        def _calendar(sym: str):
            try:
                t = yf.Ticker(sym)
                return t.earnings_dates, t.earnings_history
            except Exception:
                return None, None

        calendars = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(_calendar, sym): sym for sym in tickers}
            for fut in as_completed(futs):
                calendars[futs[fut]] = fut.result()

        for sym in tickers:
            try:
                px = _slice(sym)
                if px is None or px.empty:
                    continue
                cal, earn_hist = calendars.get(sym, (None, None))
                px.index = _naive_index(px.index)
                next_date = None
                timing = None
                past_dates = []
                if cal is not None and not cal.empty:
                    idx = _naive_index(cal.index)
                    cal = cal.copy()
                    cal.index = idx
                    future = cal[cal.index.normalize() >= pd.Timestamp(today)]
                    if not future.empty:
                        next_date = str(future.index[0].date())
                        if "Event Type" in future.columns:
                            timing = str(future.iloc[0].get("Event Type") or "")
                    past_dates = list(cal.index[cal.index.normalize() < pd.Timestamp(today)])
                gap = _gaps_from_history(px, past_dates[-12:])
                tech = _tech_score(px)
                beat_rate = None
                beats = 0
                n_eps = 0
                if earn_hist is not None and not getattr(earn_hist, "empty", True):
                    for _, row in earn_hist.reset_index().iterrows():
                        est = row.get("EPS Estimate")
                        act = row.get("Reported EPS")
                        if pd.notna(est) and pd.notna(act):
                            if float(est) == 0:
                                continue
                            n_eps += 1
                            if float(act) > float(est):
                                beats += 1
                    beat_rate = round(beats / n_eps * 100) if n_eps else None
                earn_pts = 0
                if beat_rate is not None and beat_rate >= 60:
                    earn_pts += 2
                elif beat_rate is not None and beat_rate >= 40:
                    earn_pts += 1
                if gap["n"] >= 4 and abs(gap["avg_gap"] or 0) >= 1:
                    earn_pts += 1
                if next_date:
                    days = (date.fromisoformat(next_date) - today).days
                    if 0 <= days <= 7:
                        earn_pts += 1
                else:
                    days = None
                tag = "BUY" if tech["tech_pts"] >= 3 and earn_pts >= 2 else "HOLD"
                rows.append({
                    "ticker": sym,
                    "next_date": next_date,
                    "days_away": days,
                    "timing": timing or "—",
                    "avg_gap": gap["avg_gap"],
                    "max_gap": gap["max_gap"],
                    "max_gap_date": gap["max_gap_date"],
                    "gap_n": gap["n"],
                    "beat_rate": beat_rate,
                    "tech_pts": tech["tech_pts"],
                    "earn_pts": earn_pts,
                    "tag": tag,
                    "notes": " · ".join(tech["notes"]),
                    "rsi": tech["rsi"],
                    "off_52w": tech["off_52w"],
                    "price": tech.get("price"),
                    "today_gap": _today_gap(px, next_date, past_dates),
                })
            except Exception:
                continue
        rows.sort(key=lambda r: (r["days_away"] is None, r["days_away"] if r["days_away"] is not None else 99, -(r["avg_gap"] or 0)))
        return {
            "ok": True,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "count": len(rows),
            "rows": rows,
            "note": "Gap คำนวณจากราคาเปิดวันรายงานเทียบปิดวันก่อน ไม่ใช่แท่ง 1 นาทีหลังประกาศ",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
