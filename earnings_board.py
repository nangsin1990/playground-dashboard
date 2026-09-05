# FILE: earnings_board.py
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA
from economic_calendar import EARNINGS_WATCHLIST, _ETF_BLACKLIST, _HIGH_IMPACT, load_ticker_earnings
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


def _session_for(sym: str, event: dict | None) -> str:
    sess = (event or {}).get("session")
    if sess in ("BMO", "AMC"):
        return sess
    return "AMC" if sym in _HIGH_IMPACT else "BMO"


def _reaction_slice(px: pd.DataFrame, ed: pd.Timestamp, session: str):
    idx = px.index
    if session == "AMC":
        nxt = px.loc[idx > ed]
        prev = px.loc[idx <= ed]
    else:
        nxt = px.loc[idx >= ed]
        prev = px.loc[idx < ed]
    return prev, nxt


def _gaps_from_history(px: pd.DataFrame, events: list[dict], sym: str) -> dict:
    empty = {"avg_gap": None, "max_gap": None, "max_gap_date": None, "n": 0, "gaps": []}
    if px is None or px.empty or not events:
        return empty
    px = px.copy()
    px.index = _naive_index(px.index)
    if "Close" not in px.columns:
        return empty
    open_col = "Open" if "Open" in px.columns else "Close"
    gaps = []
    for ev in events:
        try:
            ed = pd.Timestamp(ev.get("date_obj"))
            ed = ed.tz_localize(None) if getattr(ed, "tzinfo", None) else ed
            ed = ed.normalize()
        except Exception:
            continue
        prev, nxt = _reaction_slice(px, ed, _session_for(sym, ev))
        if prev.empty or nxt.empty:
            continue
        prev_close = float(prev["Close"].iloc[-1])
        nxt_open = float(nxt[open_col].iloc[0])
        if prev_close <= 0:
            continue
        gap = (nxt_open / prev_close - 1) * 100
        gaps.append({"date": str(nxt.index[0].date()), "gap": round(gap, 2), "session": _session_for(sym, ev)})
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


def _today_gap(px: pd.DataFrame, next_date: str | None, events: list[dict], sym: str) -> float | None:
    if px is None or len(px) < 2:
        return None
    last_day = pd.Timestamp(px.index[-1])
    if getattr(last_day, "tzinfo", None):
        last_day = last_day.tz_localize(None)
    last_day = last_day.normalize()
    if "Close" not in px.columns:
        return None
    marks = []
    if next_date:
        marks.append((pd.Timestamp(next_date).normalize(), _session_for(sym, None)))
    for ev in events[-4:]:
        try:
            marks.append((pd.Timestamp(ev["date_obj"]).normalize(), _session_for(sym, ev)))
        except Exception:
            continue
    for ed, sess in marks:
        prev, nxt = _reaction_slice(px, ed, sess)
        if nxt.empty:
            continue
        react = pd.Timestamp(nxt.index[0]).normalize()
        if abs((last_day - react).days) <= 1:
            prev_close = float(prev["Close"].iloc[-1]) if not prev.empty else float(px["Close"].iloc[-2])
            last_open = float(nxt["Open"].iloc[0]) if "Open" in nxt.columns else float(nxt["Close"].iloc[0])
            if prev_close > 0:
                return round((last_open / prev_close - 1) * 100, 2)
    return None


def _implied_move(sym: str, next_date: str | None, spot: float | None) -> float | None:
    if not next_date or not spot:
        return None
    try:
        days = (date.fromisoformat(next_date) - date.today()).days
        if days < 0 or days > 21:
            return None
        t = yf.Ticker(sym.replace(".", "-"))
        exps = list(t.options or [])
        if not exps:
            return None
        pick = min(exps, key=lambda x: abs((date.fromisoformat(x) - date.fromisoformat(next_date)).days))
        chain = t.option_chain(pick)
        calls, puts = chain.calls, chain.puts
        if calls is None or puts is None or calls.empty or puts.empty:
            return None
        calls = calls.copy()
        puts = puts.copy()
        calls["dist"] = (calls["strike"] - spot).abs()
        puts["dist"] = (puts["strike"] - spot).abs()
        c = float(calls.sort_values("dist").iloc[0].get("lastPrice") or 0)
        p = float(puts.sort_values("dist").iloc[0].get("lastPrice") or 0)
        if spot <= 0 or (c + p) <= 0:
            return None
        return round((c + p) / spot * 100, 2)
    except Exception:
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
        try:
            from economic_calendar import _today
            today = _today()
        except Exception:
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

        failed = []
        owned = set(PERSONAL_TICKERS)
        for sym in tickers:
            try:
                px = _slice(sym)
                if px is None or px.empty:
                    failed.append(sym)
                    continue
                events = load_ticker_earnings(sym)
                px.index = _naive_index(px.index)
                future = [e for e in events if e["date_obj"] >= today]
                past = [e for e in events if e["date_obj"] < today]
                next_ev = future[0] if future else None
                next_date = next_ev["date_obj"].isoformat() if next_ev else None
                timing = _session_for(sym, next_ev) if next_ev else (_session_for(sym, past[-1]) if past else "—")
                gap = _gaps_from_history(px, past[-12:], sym)
                tech = _tech_score(px)
                beats = [e for e in past if e.get("beat") is True]
                known = [e for e in past if e.get("beat") is not None]
                beat_rate = round(len(beats) / len(known) * 100) if known else None
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
                implied = _implied_move(sym, next_date, tech.get("price"))
                rows.append({
                    "ticker": sym,
                    "owned": sym in owned,
                    "next_date": next_date,
                    "days_away": days,
                    "timing": timing or "—",
                    "avg_gap": gap["avg_gap"],
                    "max_gap": gap["max_gap"],
                    "max_gap_date": gap["max_gap_date"],
                    "gap_n": gap["n"],
                    "gaps": gap["gaps"],
                    "beat_rate": beat_rate,
                    "tech_pts": tech["tech_pts"],
                    "earn_pts": earn_pts,
                    "tag": tag,
                    "notes": " · ".join(tech["notes"]),
                    "rsi": tech["rsi"],
                    "off_52w": tech["off_52w"],
                    "price": tech.get("price"),
                    "today_gap": _today_gap(px, next_date, events, sym),
                    "implied_move": implied,
                })
            except Exception:
                failed.append(sym)
                continue
        rows.sort(key=lambda r: (0 if r.get("owned") else 1, r["days_away"] is None, r["days_away"] if r["days_away"] is not None else 99, -(r["avg_gap"] or 0)))
        note = "AMC ใช้ราคาเปิดวันถัดไป · BMO ใช้เปิดวันประกาศ · ไม่ใช่แท่งนาทีหลังประกาศ"
        if failed:
            note += f" · โหลดไม่สำเร็จ {len(failed)} ตัว: " + ", ".join(failed[:8])
        return {
            "ok": True,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "count": len(rows),
            "failed": failed,
            "rows": rows,
            "note": note,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
