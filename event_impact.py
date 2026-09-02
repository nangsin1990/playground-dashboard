# FILE: event_impact.py
"""Daily-session impact study around scheduled macro events.
ไม่เลียนแบบตลาด Kalshi และไม่ใช้แท่ง 1 ชั่วโมง — ใช้ราคาวันจาก yfinance เท่านั้น
"""
from __future__ import annotations
from datetime import datetime, date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA
from economic_calendar import FED_MEETINGS_ALL

ASSETS = {
    "NAS100": "QQQ",
    "S&P500": "SPY",
    "Gold": "GLD",
    "Bitcoin": "BTC-USD",
    "Oil WTI": "CL=F",
    "USDJPY": "JPY=X",
    "EURUSD": "EURUSD=X",
}


def _naive_index(idx) -> pd.DatetimeIndex:
    out = pd.DatetimeIndex(pd.to_datetime(idx, errors="coerce"))
    if getattr(out, "tz", None) is not None:
        out = out.tz_convert("UTC").tz_localize(None)
    return out


def _close_frame(raw: pd.DataFrame) -> pd.DataFrame | None:
    if raw is None or getattr(raw, "empty", True):
        return None
    df = raw
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = {str(x) for x in df.columns.get_level_values(0)}
        if "Close" in lvl0:
            df = df["Close"]
        else:
            try:
                df = df.xs("Close", axis=1, level=-1)
            except Exception:
                return None
    elif "Close" in df.columns:
        df = df["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame()
    if df is None or df.empty:
        return None
    df = df.copy()
    df.index = _naive_index(df.index)
    df.columns = [str(c) for c in df.columns]
    return df


def _first_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _thursdays(start: date, end: date) -> list[date]:
    d = start
    while d.weekday() != 3:
        d += timedelta(days=1)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    return out


def _event_dates() -> list[dict]:
    today = date.today()
    start = date(today.year - 2, 1, 1)
    end = date(today.year, 12, 31)
    events = []
    for y in range(start.year, end.year + 1):
        for m in range(1, 13):
            events.append({"code": "NFP", "name": "การจ้างงานนอกภาคเกษตร NFP", "date": _first_friday(y, m), "approx": False})
            events.append({"code": "CPI", "name": "เงินเฟ้อ CPI", "date": date(y, m, 12), "approx": True})
            events.append({"code": "PPI", "name": "เงินเฟ้อผู้ผลิต PPI", "date": date(y, m, 13), "approx": True})
            events.append({"code": "PCE", "name": "เงินเฟ้อ PCE", "date": date(y, m, 28) if m != 2 else date(y, m, 27), "approx": True})
            events.append({"code": "RETAIL", "name": "ยอดค้าปลีก", "date": date(y, m, 15), "approx": True})
            events.append({"code": "DURABLES", "name": "ยอดสั่งซื้อสินค้าคงทน", "date": date(y, m, 24), "approx": True})
            events.append({"code": "JOLTS", "name": "ตำแหน่งงานเปิดรับ JOLTS", "date": date(y, m, 2) if m != 2 else date(y, 2, 4), "approx": True})
            events.append({"code": "ADP", "name": "จ้างงานเอกชน ADP", "date": date(y, m, 3), "approx": True})
    for ds, title, _ in FED_MEETINGS_ALL:
        events.append({"code": "FOMC", "name": title, "date": date.fromisoformat(ds), "approx": False})
    for d in _thursdays(start, min(end, today + timedelta(days=30))):
        events.append({"code": "CLAIMS", "name": "ผู้ขอรับสวัสดิการว่างงาน", "date": d, "approx": False})
    return [e for e in events if start <= e["date"] <= end]


@ttl_cache(CACHE_TTL_DATA * 2)
def fetch_event_impact() -> dict:
    try:
        catalog = _event_dates()
        today = date.today()
        tickers = list(ASSETS.values())
        raw = yf.download(tickers, period="2y", auto_adjust=True, group_by="ticker", threads=True, progress=False, timeout=45)
        close = _close_frame(raw)
        if close is None or close.empty:
            return {"ok": False, "error": "ไม่ดึงราคาสินทรัพย์ได้"}
        try:
            rets = close.pct_change(fill_method=None) * 100
        except TypeError:
            rets = close.pct_change() * 100

        by_code: dict[str, list] = {}
        for ev in catalog:
            by_code.setdefault(ev["code"], []).append(ev)

        cards = []
        for code, items in by_code.items():
            past = [e for e in items if e["date"] <= today]
            future = [e for e in items if e["date"] > today]
            next_ev = min(future, key=lambda x: x["date"]) if future else None
            approx = any(e.get("approx") for e in items)
            past_dates = {pd.Timestamp(e["date"]).normalize() for e in past if not e.get("approx")}
            asset_rows = []
            vol_rows = []
            if approx:
                days_away = (next_ev["date"] - today).days if next_ev else None
                cards.append({
                    "code": code,
                    "name": items[0]["name"],
                    "approx": True,
                    "next_date": str(next_ev["date"]) if next_ev else None,
                    "days_away": days_away,
                    "sample": 0,
                    "assets": [],
                    "vol": [],
                    "skip_reason": "วันประกาศจริงเลื่อนทุกเดือน จึงไม่คำนวณสถิติจากวันตายตัว",
                })
                continue
            for label, tk in ASSETS.items():
                if tk not in rets.columns:
                    continue
                series = rets[tk].dropna()
                idx = _naive_index(series.index).normalize()
                series = series.copy()
                series.index = idx
                event_mask = series.index.isin(past_dates)
                ev_abs = series[event_mask].abs()
                base_abs = series[~event_mask].abs()
                if ev_abs.empty or base_abs.empty:
                    continue
                signed = series[event_mask]
                mult = float(ev_abs.median() / base_abs.median()) if base_abs.median() else None
                asset_rows.append({
                    "asset": label,
                    "ticker": tk,
                    "mean": round(float(signed.mean()), 2),
                    "n": int(signed.count()),
                })
                vol_rows.append({
                    "asset": label,
                    "ticker": tk,
                    "mult": None if mult is None else round(mult, 1),
                    "mean": round(float(ev_abs.mean()), 2),
                    "median": round(float(ev_abs.median()), 2),
                    "max": round(float(ev_abs.max()), 2),
                    "normal": round(float(base_abs.median()), 2),
                    "n": int(ev_abs.count()),
                })
            asset_rows.sort(key=lambda r: abs(r["mean"]), reverse=True)
            vol_rows.sort(key=lambda r: r["mult"] or 0, reverse=True)
            days_away = (next_ev["date"] - today).days if next_ev else None
            cards.append({
                "code": code,
                "name": items[0]["name"],
                "approx": False,
                "next_date": str(next_ev["date"]) if next_ev else None,
                "days_away": days_away,
                "sample": max((r["n"] for r in vol_rows), default=len(past)),
                "assets": asset_rows,
                "vol": vol_rows,
            })
        cards.sort(key=lambda c: (c["days_away"] is None, c["days_away"] if c["days_away"] is not None else 99))
        return {
            "ok": True,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "cards": cards,
            "disclaimer": "ไม่มีตลาดคาด Kalshi และไม่มีแท่ง 1 ชั่วโมงหลังประกาศ — ตัวเลขคือค่าเฉลี่ยวันประกาศจากราคาวัน yfinance ไม่ใช่คำทำนายรอบนี้",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
