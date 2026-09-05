# economic_calendar.py
"""Economic Calendar — FRED dates + prints, Yahoo earnings, static Fed/OPEX/holidays."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

try:
    from cache_utils import ttl_cache
except ImportError:
    import functools
    import time

    def ttl_cache(ttl=900):
        def decorator(func):
            store = {}

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                key = (args, tuple(sorted(kwargs.items())))
                now = time.time()
                if key in store and (now - store[key][0]) < ttl:
                    return store[key][1]
                val = func(*args, **kwargs)
                store[key] = (now, val)
                return val

            wrapper.cache_clear = store.clear
            return wrapper

        return decorator

try:
    from constants import (
        CACHE_TTL_CALENDAR as CACHE_TTL,
        CAL_LOOK_AHEAD_DAYS as LOOK_AHEAD_DAYS,
        CAL_LOOK_BACK_DAYS as LOOK_BACK_DAYS,
        CAL_MAX_EVENTS as MAX_EVENTS,
    )
except ImportError:
    CACHE_TTL = 30 * 60
    MAX_EVENTS = 90
    LOOK_AHEAD_DAYS = 120
    LOOK_BACK_DAYS = 7

TZ = ZoneInfo("Asia/Bangkok")
FRED_BASE = "https://api.stlouisfed.org/fred"
FRED_API_KEY = os.environ.get("FRED_API_KEY") or None

FED_MEETINGS_2025 = [
    ("2025-01-29", "FOMC Meeting", "Rate Decision — Jan 28-29"),
    ("2025-03-19", "FOMC Meeting", "Rate Decision — Mar 18-19"),
    ("2025-05-07", "FOMC Meeting", "Rate Decision — May 6-7"),
    ("2025-06-18", "FOMC Meeting", "Rate Decision — Jun 17-18"),
    ("2025-07-30", "FOMC Meeting", "Rate Decision — Jul 29-30"),
    ("2025-09-17", "FOMC Meeting", "Rate Decision — Sep 16-17"),
    ("2025-10-29", "FOMC Meeting", "Rate Decision — Oct 28-29"),
    ("2025-12-10", "FOMC Meeting", "Rate Decision — Dec 9-10"),
]
FED_MEETINGS_2026 = [
    ("2026-01-28", "FOMC Meeting", "Rate Decision — Jan 27-28"),
    ("2026-03-18", "FOMC Meeting", "Rate Decision — Mar 17-18"),
    ("2026-04-29", "FOMC Meeting", "Rate Decision — Apr 28-29"),
    ("2026-06-17", "FOMC Meeting", "Rate Decision — Jun 16-17"),
    ("2026-07-29", "FOMC Meeting", "Rate Decision — Jul 28-29"),
    ("2026-09-16", "FOMC Meeting", "Rate Decision — Sep 15-16"),
    ("2026-10-28", "FOMC Meeting", "Rate Decision — Oct 27-28"),
    ("2026-12-16", "FOMC Meeting", "Rate Decision — Dec 15-16"),
]
FED_MEETINGS_ALL = FED_MEETINGS_2025 + FED_MEETINGS_2026

# release_id จากเอกสาร FRED — ห้ามใช้ 19/82 สำหรับ NFP/PCE
FRED_RELEASES = {
    "10": {"cat": "CPI", "title": "Consumer Price Index", "importance": "HIGH", "series": "CPIAUCSL", "unit": "index"},
    "50": {"cat": "NFP", "title": "Nonfarm Payrolls", "importance": "HIGH", "series": "PAYEMS", "unit": "k jobs"},
    "54": {"cat": "PCE", "title": "PCE / Personal Income", "importance": "HIGH", "series": "PCEPILFE", "unit": "index"},
    "53": {"cat": "GDP", "title": "GDP Advance Estimate", "importance": "HIGH", "series": "A191RL1Q225SBEA", "unit": "%"},
    "46": {"cat": "PPI", "title": "Producer Price Index", "importance": "MEDIUM", "series": "PPIACO", "unit": "index"},
    "9":  {"cat": "RETAIL", "title": "Retail Sales", "importance": "MEDIUM", "series": "RSAFS", "unit": "$"},
}

FRED_EXTRA = {
    "CLAIMS": {"title": "Initial Jobless Claims", "importance": "HIGH", "series": "ICSA", "unit": "claims", "icon_cat": "CLAIMS"},
    "JOLTS": {"title": "JOLTS Job Openings", "importance": "MEDIUM", "series": "JTSJOL", "unit": "k", "icon_cat": "JOLTS"},
    "ISM": {"title": "ISM Manufacturing PMI", "importance": "HIGH", "series": "NAPM", "unit": "index", "icon_cat": "ISM"},
    "MICH": {"title": "Michigan Sentiment", "importance": "MEDIUM", "series": "UMCSENT", "unit": "index", "icon_cat": "MICH"},
    "HOUSING": {"title": "Housing Starts", "importance": "MEDIUM", "series": "HOUST", "unit": "k", "icon_cat": "HOUSING"},
}

EARNINGS_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "GOOG", "GOOGL", "META", "AMZN", "TSLA",
    "JPM", "BAC", "GS", "BRK.B", "V", "MA", "AMD", "INTC", "AVGO", "MU",
    "UNH", "JNJ", "LLY", "XOM", "CVX",
    "AMAT", "ARM", "COHR", "CRWD", "CSCO", "KLAC", "LRCX", "NBIS", "NVO", "OKTA", "TSM", "VRT",
]
_ETF_BLACKLIST = {"SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "HYG", "QQQM", "JEPQ", "SMH", "DBJP"}
_HIGH_IMPACT = {"AAPL", "MSFT", "NVDA", "GOOG", "GOOGL", "META", "AMZN", "TSLA", "JPM", "AVGO", "TSM", "MU"}

CATEGORY_ICONS = {
    "FOMC": "🏦", "MINUTES": "📋", "CPI": "📊", "NFP": "👷",
    "GDP": "📈", "PPI": "🏭", "RETAIL": "🛒", "PCE": "💳", "EARNINGS": "💰",
    "CLAIMS": "📥", "JOLTS": "🪧", "ISM": "🏭", "MICH": "🙂", "HOUSING": "🏠",
    "OPEX": "📆", "HOLIDAY": "🇺🇸", "TH_CPI": "🇹🇭", "AUCTION": "🏦",
}
CATEGORY_COLORS = {
    "FOMC": "#6366f1", "MINUTES": "#8b5cf6", "CPI": "#f59e0b", "NFP": "#10b981",
    "GDP": "#3b82f6", "PPI": "#64748b", "RETAIL": "#ec4899", "PCE": "#f97316", "EARNINGS": "#2dd4bf",
    "CLAIMS": "#0ea5e9", "JOLTS": "#14b8a6", "ISM": "#7c3aed", "MICH": "#db2777", "HOUSING": "#92400e",
    "OPEX": "#334155", "HOLIDAY": "#475569", "TH_CPI": "#dc2626", "AUCTION": "#0369a1",
}
WEEKDAYS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _today() -> date:
    return datetime.now(TZ).date()


def _watched_tickers() -> list[str]:
    out = []
    try:
        from personal_watchlist import PERSONAL_TICKERS, ETF_SKIP_EARNINGS
        skip = set(_ETF_BLACKLIST) | set(ETF_SKIP_EARNINGS)
        for t in PERSONAL_TICKERS:
            if t not in skip and t not in out:
                out.append(t)
    except Exception:
        pass
    if "MU" not in out:
        out.append("MU")
    return out


def _earnings_universe() -> list[str]:
    seen = []
    skip = set(_ETF_BLACKLIST)
    try:
        from personal_watchlist import ETF_SKIP_EARNINGS
        skip |= set(ETF_SKIP_EARNINGS)
    except Exception:
        pass
    for t in list(EARNINGS_WATCHLIST) + _watched_tickers():
        if t in skip or t in seen:
            continue
        seen.append(t)
    return seen


def _fomc_minutes_dates() -> list[tuple[str, str, str]]:
    out = []
    for ds, _, _ in FED_MEETINGS_ALL:
        d = datetime.strptime(ds, "%Y-%m-%d") + timedelta(weeks=3)
        out.append((d.strftime("%Y-%m-%d"), "FOMC Minutes", "Minutes Release"))
    return out


def _fred_fetch(path: str, params: dict) -> Optional[dict]:
    if not FRED_API_KEY:
        return None
    params = dict(params)
    params["api_key"] = FRED_API_KEY
    params["file_type"] = "json"
    url = f"{FRED_BASE}/{path}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PlaygroundDashboard/2.1"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _fetch_fred_release_dates(release_id: str) -> list[str]:
    today = _today()
    data = _fred_fetch("release/dates", {
        "release_id": release_id,
        "realtime_start": (today - timedelta(days=LOOK_BACK_DAYS)).isoformat(),
        "realtime_end": (today + timedelta(days=LOOK_AHEAD_DAYS)).isoformat(),
        "limit": "12",
        "sort_order": "asc",
    })
    if not data or "release_dates" not in data:
        return []
    return [rd["date"] for rd in data["release_dates"] if rd.get("date")]


def _latest_print(series_id: str) -> dict:
    data = _fred_fetch("series/observations", {
        "series_id": series_id,
        "sort_order": "desc",
        "limit": "4",
    })
    if not data or "observations" not in data:
        return {}
    vals = []
    for row in data["observations"]:
        raw = row.get("value")
        if raw in (None, ".", ""):
            continue
        try:
            vals.append({"date": row.get("date"), "value": float(raw)})
        except (TypeError, ValueError):
            continue
        if len(vals) >= 2:
            break
    if not vals:
        return {}
    latest = vals[0]
    prev = vals[1] if len(vals) > 1 else None
    change = None
    if prev and prev["value"] is not None:
        change = round(latest["value"] - prev["value"], 3)
    return {
        "actual": latest["value"],
        "actual_date": latest["date"],
        "previous": None if prev is None else prev["value"],
        "previous_date": None if prev is None else prev["date"],
        "change": change,
        "forecast": None,
    }


def _as_date(val) -> date | None:
    if val is None:
        return None
    try:
        if hasattr(val, "date") and callable(val.date):
            return val.date()
        return date.fromisoformat(str(val)[:10])
    except Exception:
        return None


def _session_flag(ts) -> str | None:
    try:
        import pandas as pd
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            return None
        hour = t.tz_convert("America/New_York").hour
        if hour < 9:
            return "BMO"
        if hour >= 16:
            return "AMC"
    except Exception:
        return None
    return None


def _fetch_earnings_yf() -> list[dict]:
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        return []
    today = _today()
    cutoff_start = today - timedelta(days=LOOK_BACK_DAYS)
    cutoff_end = today + timedelta(days=LOOK_AHEAD_DAYS)
    events = []
    for raw_sym in _earnings_universe():
        yf_sym = raw_sym.replace(".", "-")
        try:
            t = yf.Ticker(yf_sym)
            edf = getattr(t, "earnings_dates", None)
            used = False
            if edf is not None and hasattr(edf, "index") and len(edf.index):
                used = True
                frame = edf.reset_index()
                idx_col = frame.columns[0]
                for _, row in frame.iterrows():
                    ed = _as_date(row.get(idx_col))
                    if ed is None or not (cutoff_start <= ed <= cutoff_end):
                        continue
                    est = row.get("EPS Estimate")
                    act = row.get("Reported EPS")
                    sur = row.get("Surprise(%)")
                    try:
                        est_n = float(est) if est is not None and str(est) not in ("nan", "None") else None
                    except (TypeError, ValueError):
                        est_n = None
                    try:
                        act_n = float(act) if act is not None and str(act) not in ("nan", "None") else None
                    except (TypeError, ValueError):
                        act_n = None
                    try:
                        sur_n = float(sur) if sur is not None and str(sur) not in ("nan", "None") else None
                    except (TypeError, ValueError):
                        sur_n = None
                    beat = None
                    if sur_n is not None:
                        beat = sur_n > 0
                    elif est_n is not None and act_n is not None:
                        beat = act_n > est_n
                    events.append({
                        "date_obj": ed,
                        "ticker": raw_sym,
                        "eps_est": est_n,
                        "eps_actual": act_n,
                        "surprise": None if sur_n is None else round(sur_n, 1),
                        "beat": beat,
                        "session": _session_flag(row.get(idx_col)),
                    })
            if used:
                continue
            cal = getattr(t, "calendar", None)
            dates = []
            if isinstance(cal, dict) and cal.get("Earnings Date") is not None:
                raw = cal.get("Earnings Date")
                dates = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
            elif cal is not None and hasattr(cal, "empty") and not cal.empty and "Earnings Date" in getattr(cal, "columns", []):
                dates = list(cal["Earnings Date"])
            for d in dates:
                ed = _as_date(d)
                if ed is None or not (cutoff_start <= ed <= cutoff_end):
                    continue
                events.append({
                    "date_obj": ed, "ticker": raw_sym, "eps_est": None,
                    "eps_actual": None, "surprise": None, "beat": None,
                    "session": _session_flag(d),
                })
        except Exception:
            continue
    seen, out = set(), []
    for e in events:
        key = (e["date_obj"], e["ticker"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return sorted(out, key=lambda x: x["date_obj"])


def _format_event(d: date, category: str, title: str, subtitle: str,
                  importance: str = "HIGH", source: str = "",
                  tickers: list[str] | None = None,
                  estimated: bool | None = None, extra: dict | None = None) -> dict:
    today = _today()
    da = (d - today).days
    if estimated is None:
        blob = f"{title} {subtitle}".lower()
        estimated = "estimated" in blob
    payload = {
        "date": d.isoformat(),
        "weekday_en": WEEKDAYS_EN[d.weekday()],
        "days_away": da,
        "category": category,
        "icon": CATEGORY_ICONS.get(category, "📌"),
        "color": CATEGORY_COLORS.get(category, "#6b7280"),
        "title": title,
        "subtitle": subtitle,
        "importance": importance,
        "source": source,
        "is_past": da < 0,
        "is_today": da == 0,
        "tickers": tickers or [],
        "estimated": bool(estimated),
        "status": "estimated" if estimated else "confirmed",
        "actual": None,
        "previous": None,
        "forecast": None,
        "change": None,
        "impact": IMPACT.get(category, ""),
        "watched": False,
        "session": None,
    }
    if extra:
        payload.update(extra)
    watched = set(_watched_tickers())
    payload["watched"] = bool(set(payload["tickers"]) & watched)
    payload["today"] = today.isoformat()
    return payload


IMPACT = {
    "FOMC": "ขยับหุ้น พันธบัตร ดอลลาร์ ทอง ทั้งชุด โดยเฉพาะช่วงแถลง",
    "MINUTES": "ดูโทนกรรมการ ขยับน้อยกว่าวันมติ",
    "CPI": "สูงกว่าคาดตลาดเสี่ยงมักกดดัน ดอกเบี้ยคาดว่าอยู่สูงต่อ",
    "NFP": "งานมากค่าจ้างเร่งเฟดไม่รีบลดดอก งานน้อยตลาดเสี่ยงมักโล่ง",
    "PCE": "ตัวที่เฟดใช้จริง ใกล้ประชุมแล้วคลาดคาดได้ผลแรง",
    "GDP": "ภาพใหญ่รายไตรมาส แรงน้อยกว่าเงินเฟ้อและงาน",
    "PPI": "เงินเฟ้อฝั่งผู้ผลิต มักนำซีพีไอเดือนถัดไป",
    "RETAIL": "วัดการใช้จ่าย กระทบกลุ่มค้าปลีก",
    "EARNINGS": "ขยับหุ้นตัวนั้นก่อน กลุ่มใหญ่ลามทั้งภาคได้",
    "CLAIMS": "งานรายสัปดาห์ ดูทิศแรงงานระหว่างรายงานใหญ่",
    "JOLTS": "ตำแหน่งว่างที่เฟดดูคู่กับงาน",
    "ISM": "ภาคผลิต ตลาดพันธบัตรและดอลลาร์ตอบเร็ว",
    "MICH": "ความเชื่อมั่นผู้บริโภค",
    "HOUSING": "บ้านเริ่มสร้าง ดูวงจรดอกเบี้ย",
    "OPEX": "วันหมดอายุสิทธิ สภาพคล่องมักหนา",
    "HOLIDAY": "ตลาดสหรัฐปิด",
    "TH_CPI": "เงินเฟ้อไทย",
    "AUCTION": "ประมูลพันธบัตร ยีลด์ขยับได้ก่อนตัวเลขใหญ่",
}


def _third_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    fridays = 0
    while True:
        if d.weekday() == 4:
            fridays += 1
            if fridays == 3:
                return d
        d += timedelta(days=1)


def _us_holidays(year: int) -> list[tuple[date, str]]:
    def observed(d: date) -> date:
        if d.weekday() == 5:
            return d - timedelta(days=1)
        if d.weekday() == 6:
            return d + timedelta(days=1)
        return d

    def nth_weekday(y, m, weekday, n):
        d = date(y, m, 1)
        seen = 0
        while True:
            if d.weekday() == weekday:
                seen += 1
                if seen == n:
                    return d
            d += timedelta(days=1)

    last_monday_may = date(year, 5, 31)
    while last_monday_may.weekday() != 0:
        last_monday_may -= timedelta(days=1)
    first_monday_sep = date(year, 9, 1)
    while first_monday_sep.weekday() != 0:
        first_monday_sep += timedelta(days=1)
    # Thanksgiving 4th Thursday Nov
    thanks = nth_weekday(year, 11, 3, 4)
    return [
        (observed(date(year, 1, 1)), "New Year"),
        (nth_weekday(year, 1, 0, 3), "Martin Luther King Jr. Day"),
        (nth_weekday(year, 2, 0, 3), "Presidents Day"),
        (last_monday_may, "Memorial Day"),
        (observed(date(year, 6, 19)), "Juneteenth"),
        (observed(date(year, 7, 4)), "Independence Day"),
        (first_monday_sep, "Labor Day"),
        (thanks, "Thanksgiving"),
        (observed(date(year, 12, 25)), "Christmas"),
    ]


def _static_macro_rows() -> list[tuple]:
    return [
        ("2026-08-12", "CPI", "Consumer Price Index", "July 2026 CPI", "HIGH", "bls.gov"),
        ("2026-09-10", "CPI", "Consumer Price Index", "August 2026 CPI", "HIGH", "bls.gov"),
        ("2026-08-07", "NFP", "Nonfarm Payrolls", "July 2026 Jobs", "HIGH", "bls.gov"),
        ("2026-09-04", "NFP", "Nonfarm Payrolls", "August 2026 Jobs", "HIGH", "bls.gov"),
        ("2026-08-28", "PCE", "PCE / Personal Income", "July 2026 PCE", "HIGH", "bea.gov"),
        ("2026-07-31", "GDP", "GDP Advance Estimate", "Q2 2026 GDP", "HIGH", "bea.gov"),
        ("2026-10-29", "GDP", "GDP Advance Estimate", "Q3 2026 GDP", "HIGH", "bea.gov"),
    ]


def _first_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _approx_month_events(y: int, m: int) -> list[tuple]:
    pce_day = 27 if m == 2 else 28
    return [
        (_first_friday(y, m).isoformat(), "NFP", "Nonfarm Payrolls", f"{y}-{m:02d} Jobs Report (estimated)", "HIGH", "bls.gov"),
        (date(y, m, 12).isoformat(), "CPI", "Consumer Price Index", f"{y}-{m:02d} CPI (estimated)", "HIGH", "bls.gov"),
        (date(y, m, pce_day).isoformat(), "PCE", "PCE / Personal Income", f"{y}-{m:02d} PCE (estimated)", "HIGH", "bea.gov"),
    ]


def _static_fallback_for(missing_cats: set[str], cutoff_start: date, cutoff_end: date) -> list[dict]:
    rows = list(_static_macro_rows())
    last = max(date.fromisoformat(r[0]) for r in rows)
    y, m = last.year, last.month + 1
    if m > 12:
        y, m = y + 1, 1
    while date(y, m, 1) <= cutoff_end:
        rows.extend(_approx_month_events(y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    out = []
    for ds, cat, title, subtitle, importance, source in rows:
        if cat not in missing_cats:
            continue
        d = date.fromisoformat(ds)
        if cutoff_start <= d <= cutoff_end:
            out.append(_format_event(d, cat, title, subtitle, importance, source, estimated=True))
    return out


@ttl_cache(CACHE_TTL)
def fetch_economic_calendar() -> dict:
    try:
        return _fetch_economic_calendar()
    except Exception as e:
        return {"ok": False, "error": str(e), "events": []}


def _fetch_economic_calendar() -> dict:
    today = _today()
    cutoff_start = today - timedelta(days=LOOK_BACK_DAYS)
    cutoff_end = today + timedelta(days=LOOK_AHEAD_DAYS)
    events: list[dict] = []
    prints: dict[str, dict] = {}
    fred_by_cat: dict[str, bool] = {}

    for ds, title, subtitle in FED_MEETINGS_ALL:
        d = date.fromisoformat(ds)
        if cutoff_start <= d <= cutoff_end:
            events.append(_format_event(d, "FOMC", title, subtitle, "HIGH", "federalreserve.gov"))
    for ds, title, subtitle in _fomc_minutes_dates():
        d = date.fromisoformat(ds)
        if cutoff_start <= d <= cutoff_end:
            events.append(_format_event(d, "MINUTES", title, subtitle, "MEDIUM", "federalreserve.gov"))

    for rel_id, meta in FRED_RELEASES.items():
        dates = _fetch_fred_release_dates(rel_id)
        fred_by_cat[meta["cat"]] = bool(dates)
        if meta.get("series"):
            prints[meta["cat"]] = _latest_print(meta["series"])
        for ds in dates:
            d = date.fromisoformat(ds)
            if cutoff_start <= d <= cutoff_end:
                extra = dict(prints.get(meta["cat"]) or {})
                events.append(_format_event(
                    d, meta["cat"], meta["title"], f"FRED release {rel_id}",
                    meta["importance"], "fred.stlouisfed.org", extra=extra,
                ))

    for cat, meta in FRED_EXTRA.items():
        pack = _latest_print(meta["series"])
        if not pack:
            continue
        ds = pack.get("actual_date")
        if not ds:
            continue
        d = date.fromisoformat(ds)
        if cutoff_start <= d <= cutoff_end:
            events.append(_format_event(
                d, cat, meta["title"], f"series {meta['series']}",
                meta["importance"], "fred.stlouisfed.org", extra=pack,
            ))

    missing = {cat for cat in ("CPI", "NFP", "PCE", "GDP", "PPI", "RETAIL") if not fred_by_cat.get(cat)}
    if missing:
        events.extend(_static_fallback_for(missing, cutoff_start, cutoff_end))

    earnings = _fetch_earnings_yf()
    yf_ok = len(earnings) > 0
    earn_by_date: dict[date, list[dict]] = defaultdict(list)
    for e in earnings:
        earn_by_date[e["date_obj"]].append(e)
    for ed, rows in sorted(earn_by_date.items()):
        tickers = [r["ticker"] for r in rows]
        bits = []
        for r in rows[:6]:
            piece = r["ticker"]
            if r.get("eps_est") is not None:
                piece += f" est {r['eps_est']}"
            if r.get("surprise") is not None:
                piece += f" {r['surprise']}%"
            if r.get("session"):
                piece += f" {r['session']}"
            bits.append(piece)
        tick_str = ", ".join(bits) + ("…" if len(rows) > 6 else "")
        is_high = any(t in _HIGH_IMPACT or t in _watched_tickers() for t in tickers)
        session = next((r.get("session") for r in rows if r.get("session")), None)
        extra = {
            "earnings_rows": [
                {"ticker": r["ticker"], "eps_est": r.get("eps_est"), "eps_actual": r.get("eps_actual"),
                 "surprise": r.get("surprise"), "beat": r.get("beat"), "session": r.get("session")}
                for r in rows
            ],
            "session": session,
        }
        events.append(_format_event(
            ed, "EARNINGS", "Earnings Release", tick_str,
            "HIGH" if is_high else "MEDIUM", "yfinance", tickers, extra=extra,
        ))

    y = today.year
    for year in (y, y + 1):
        for month in range(1, 13):
            d = _third_friday(year, month)
            if cutoff_start <= d <= cutoff_end:
                quad = month in (3, 6, 9, 12)
                events.append(_format_event(
                    d, "OPEX", "Quad Witching" if quad else "Monthly Options Expiration",
                    "วันหมดอายุสิทธิ" + (" รายไตรมาส" if quad else ""),
                    "HIGH" if quad else "MEDIUM", "calculated",
                ))
        for d, name in _us_holidays(year):
            if cutoff_start <= d <= cutoff_end:
                events.append(_format_event(d, "HOLIDAY", "US Market Holiday", name, "MEDIUM", "nyse"))

    for month_delta in range(0, 5):
        m0 = today.month + month_delta
        yy = today.year + (m0 - 1) // 12
        mm = ((m0 - 1) % 12) + 1
        d = date(yy, mm, 7)
        if cutoff_start <= d <= cutoff_end:
            events.append(_format_event(d, "TH_CPI", "Thailand CPI", f"{yy}-{mm:02d} (estimated)", "MEDIUM", "nso.go.th", estimated=True))

    seen, unique = set(), []
    events.sort(key=lambda x: (x["date"], x["category"]))
    for e in events:
        key = (e["date"], e["category"], e["title"])
        if key not in seen:
            seen.add(key)
            unique.append(e)

    importance_order = {"HIGH": 0, "MEDIUM": 1}
    unique.sort(key=lambda x: (x["is_past"], x["days_away"], importance_order.get(x["importance"], 2)))
    final = unique[: max(MAX_EVENTS, 80)]

    def _next(cat: str):
        return next((e for e in unique if e["category"] == cat and not e["is_past"]), None)

    return {
        "ok": True,
        "updated": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
        "today": today.isoformat(),
        "timezone": "Asia/Bangkok",
        "events": final,
        "total": len(final),
        "next_fomc": _next("FOMC"),
        "next_cpi": _next("CPI"),
        "next_nfp": _next("NFP"),
        "watched_tickers": _watched_tickers(),
        "fred_connected": any(fred_by_cat.values()),
        "fred_ok_by_cat": fred_by_cat,
        "yf_earnings_connected": yf_ok,
        "source_status": "fred" if any(fred_by_cat.values()) else "fallback",
        "is_fallback": not any(fred_by_cat.values()),
        "prints": prints,
        "categories": CATEGORY_ICONS,
        "colors": CATEGORY_COLORS,
    }
