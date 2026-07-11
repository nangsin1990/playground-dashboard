"""
Economic Calendar Engine
========================
ดึงข้อมูล Economic Events ฟรี 100% จาก:
  1. FRED API  — CPI, NFP, GDP, PCE, PPI, Retail Sales release dates
  2. yfinance  — Earnings dates ของ top tickers
  3. Static Fed meeting schedule — อัปเดตทุกปี

v2 fixes:
  - Static fallback dates extended to 2026
  - MAX_EVENTS raised to 30, HIGH importance sorted first
  - LOOK_AHEAD_DAYS extended to 120 days
"""

from __future__ import annotations
import json
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta
from functools import lru_cache
from typing import Optional
import os # ✨ NEW: Import os to get environment variables
try:
    from cache_utils import ttl_cache
except ImportError:
    # Fallback: simple TTL wrapper (no cache_utils available)
    import time
    import functools
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

CACHE_TTL       = 30 * 60   # 30 min
MAX_EVENTS      = 30         # ↑ from 20 (macro alone = ~22 in 90d)
LOOK_AHEAD_DAYS = 120        # ↑ from 90
LOOK_BACK_DAYS  = 7

# ─────────────────────────────────────────────────────────────────────────────
# 1) STATIC FED MEETING SCHEDULE
#    Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# ─────────────────────────────────────────────────────────────────────────────
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

def _fomc_minutes_dates() -> list[tuple[str, str, str]]:
    out = []
    for ds, _, _ in FED_MEETINGS_ALL:
        d = datetime.strptime(ds, "%Y-%m-%d") + timedelta(weeks=3)
        out.append((d.strftime("%Y-%m-%d"), "FOMC Minutes", "Minutes Release"))
    return out

# ─────────────────────────────────────────────────────────────────────────────
# 2) FRED API
# ─────────────────────────────────────────────────────────────────────────────
FRED_RELEASES = {
    "10":  ("CPI",    "Consumer Price Index",   "HIGH"),
    "19":  ("NFP",    "Nonfarm Payrolls",        "HIGH"),
    "53":  ("GDP",    "GDP Advance Estimate",    "HIGH"),
    "31":  ("PPI",    "Producer Price Index",    "MEDIUM"),
    "56":  ("RETAIL", "Retail Sales",            "MEDIUM"),
    "82":  ("PCE",    "PCE / Personal Income",   "HIGH"),
}
FRED_BASE = "https://api.stlouisfed.org/fred"
# ✨ FIX: อ่าน API Key จาก Environment Variable, ถ้าไม่มีให้ใช้ None
FRED_API_KEY = os.environ.get("FRED_API_KEY", None)

def _fred_fetch(path: str, params: dict) -> Optional[dict]:
    # ✨ FIX: เพิ่มการตรวจสอบว่ามี API Key หรือไม่ก่อนเรียกใช้
    if not FRED_API_KEY:
        return None # ถ้าไม่มี Key, ไม่ต้องพยายามเรียก API เลย

    params["api_key"] = FRED_API_KEY
    params["file_type"] = "json"
    qs = urllib.parse.urlencode(params)
    url = f"{FRED_BASE}/{path}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PlaygroundDashboard/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception:
        return None

def _fetch_fred_release_dates(release_id: str) -> list[str]:
    today = date.today()
    params = {
        "release_id": release_id,
        "realtime_start": (today - timedelta(days=LOOK_BACK_DAYS)).isoformat(),
        "realtime_end":   (today + timedelta(days=LOOK_AHEAD_DAYS)).isoformat(),
        "limit": "10",
        "sort_order": "asc",
    }
    data = _fred_fetch("release/dates", params)
    if not data or "release_dates" not in data:
        return []
    return [rd["date"] for rd in data["release_dates"]]

# ─────────────────────────────────────────────────────────────────────────────
# 3) EARNINGS via yfinance
# ─────────────────────────────────────────────────────────────────────────────
EARNINGS_WATCHLIST = [
    "AAPL","MSFT","NVDA","GOOG","GOOGL","META","AMZN","TSLA",
    "JPM","BAC","GS","BRK.B","V","MA","AMD","INTC","AVGO",
    "UNH","JNJ","LLY","XOM","CVX",
]
_ETF_BLACKLIST = {"SPY","QQQ","IWM","DIA","GLD","SLV","TLT","HYG"}
_HIGH_IMPACT   = {"AAPL","MSFT","NVDA","GOOG","GOOGL","META","AMZN","TSLA","JPM"}

def _fetch_earnings_yf() -> list[dict]:
    try:
        import yfinance as yf
    except ImportError:
        return []
    today = date.today()
    cutoff_start = today - timedelta(days=LOOK_BACK_DAYS)
    cutoff_end   = today + timedelta(days=LOOK_AHEAD_DAYS)
    events = []
    for sym in EARNINGS_WATCHLIST:
        if sym in _ETF_BLACKLIST:
            continue
        try:
            t   = yf.Ticker(sym)
            cal = t.calendar
            if not cal or "Earnings Date" not in cal:
                continue
            raw = cal["Earnings Date"]
            dates = list(raw) if hasattr(raw, "__iter__") and not isinstance(raw, str) else [raw]
            for d in dates:
                try:
                    ed = d.date() if hasattr(d, "date") else date.fromisoformat(str(d)[:10])
                except Exception:
                    continue
                if cutoff_start <= ed <= cutoff_end:
                    events.append({"date_obj": ed, "ticker": sym,
                                   "eps_est": str(cal.get("EPS Estimate",""))})
        except Exception:
            continue
    seen, out = set(), []
    for e in events:
        key = (e["date_obj"], e["ticker"])
        if key not in seen:
            seen.add(key); out.append(e)
    return sorted(out, key=lambda x: x["date_obj"])

# ─────────────────────────────────────────────────────────────────────────────
# 4) Helpers
# ─────────────────────────────────────────────────────────────────────────────
CATEGORY_ICONS = {
    "FOMC":"🏦","MINUTES":"📋","CPI":"📊","NFP":"👷",
    "GDP":"📈","PPI":"🏭","RETAIL":"🛒","PCE":"💳","EARNINGS":"💰",
}
CATEGORY_COLORS = {
    "FOMC":"#6366f1","MINUTES":"#8b5cf6","CPI":"#f59e0b","NFP":"#10b981",
    "GDP":"#3b82f6","PPI":"#64748b","RETAIL":"#ec4899","PCE":"#f97316","EARNINGS":"#2dd4bf",
}
WEEKDAYS_EN = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

def _format_event(d: date, category: str, title: str, subtitle: str,
                  importance: str = "HIGH", source: str = "",
                  tickers: list[str] | None = None) -> dict:
    da = (d - date.today()).days
    return {
        "date":       d.isoformat(),
        "weekday_en": WEEKDAYS_EN[d.weekday()],
        "days_away":  da,
        "category":   category,
        "icon":       CATEGORY_ICONS.get(category, "📌"),
        "color":      CATEGORY_COLORS.get(category, "#6b7280"),
        "title":      title,
        "subtitle":   subtitle,
        "importance": importance,
        "source":     source,
        "is_past":    da < 0,
        "is_today":   da == 0,
        "tickers":    tickers or [],
    }

# ─────────────────────────────────────────────────────────────────────────────
# 5) Main fetch
# ─────────────────────────────────────────────────────────────────────────────
@ttl_cache(CACHE_TTL)
def fetch_economic_calendar() -> dict:
    today = date.today()
    cutoff_start = today - timedelta(days=LOOK_BACK_DAYS)
    cutoff_end   = today + timedelta(days=LOOK_AHEAD_DAYS)
    events: list[dict] = []

    # A. FOMC Meetings
    for ds, title, subtitle in FED_MEETINGS_ALL:
        d = date.fromisoformat(ds)
        if cutoff_start <= d <= cutoff_end:
            events.append(_format_event(d, "FOMC", title, subtitle, "HIGH", "federalreserve.gov"))

    # B. FOMC Minutes
    for ds, title, subtitle in _fomc_minutes_dates():
        d = date.fromisoformat(ds)
        if cutoff_start <= d <= cutoff_end:
            events.append(_format_event(d, "MINUTES", title, subtitle, "MEDIUM", "federalreserve.gov"))

    # C. FRED Economic Releases
    fred_ok = False
    for rel_id, (cat, title, importance) in FRED_RELEASES.items():
        dates = _fetch_fred_release_dates(rel_id)
        if dates:
            fred_ok = True
        for ds in dates:
            d = date.fromisoformat(ds)
            if cutoff_start <= d <= cutoff_end:
                events.append(_format_event(d, cat, title, f"FRED Release #{rel_id}", importance, "fred.stlouisfed.org"))

    # D. Earnings (yfinance)
    earnings = _fetch_earnings_yf()
    yf_ok    = len(earnings) > 0
    from collections import defaultdict
    earn_by_date: dict[date, list[str]] = defaultdict(list)
    for e in earnings:
        earn_by_date[e["date_obj"]].append(e["ticker"])
    for ed, tickers in sorted(earn_by_date.items()):
        tick_str   = ", ".join(tickers[:6]) + ("…" if len(tickers) > 6 else "")
        is_high    = any(t in _HIGH_IMPACT for t in tickers)
        events.append(_format_event(ed, "EARNINGS", "Earnings Release", tick_str,
                                    "HIGH" if is_high else "MEDIUM", "yfinance", tickers))

    # E. Static fallback if FRED unreachable
    if not fred_ok:
        events.extend(_static_fallback(cutoff_start, cutoff_end))

    # Deduplicate
    seen, unique = set(), []
    events.sort(key=lambda x: (x["date"], x["category"]))
    for e in events:
        key = (e["date"], e["category"], e["title"])
        if key not in seen:
            seen.add(key); unique.append(e)

    # Sort: today first, future by date+importance, past last
    IMPORTANCE_ORDER = {"HIGH": 0, "MEDIUM": 1}
    unique.sort(key=lambda x: (
        x["is_past"],
        x["days_away"],
        IMPORTANCE_ORDER.get(x["importance"], 2)
    ))

    final = unique[:MAX_EVENTS]
    next_fomc = next((e for e in unique if e["category"] == "FOMC" and not e["is_past"]), None)

    return {
        "ok": True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "events": final,
        "total": len(final),
        "next_fomc": next_fomc,
        "fred_connected": fred_ok,
        "yf_earnings_connected": yf_ok,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Static fallback — 2025 + 2026 (updated to cover current date)
# ─────────────────────────────────────────────────────────────────────────────
_STATIC_MACRO = [
    # CPI 2025
    ("2025-07-11","CPI","Consumer Price Index","June CPI Release","HIGH","bls.gov"),
    ("2025-08-12","CPI","Consumer Price Index","July CPI Release","HIGH","bls.gov"),
    ("2025-09-11","CPI","Consumer Price Index","August CPI Release","HIGH","bls.gov"),
    ("2025-10-15","CPI","Consumer Price Index","September CPI Release","HIGH","bls.gov"),
    ("2025-11-13","CPI","Consumer Price Index","October CPI Release","HIGH","bls.gov"),
    ("2025-12-11","CPI","Consumer Price Index","November CPI Release","HIGH","bls.gov"),
    # CPI 2026 ← NEW
    ("2026-01-14","CPI","Consumer Price Index","December 2025 CPI","HIGH","bls.gov"),
    ("2026-02-11","CPI","Consumer Price Index","January 2026 CPI","HIGH","bls.gov"),
    ("2026-03-11","CPI","Consumer Price Index","February 2026 CPI","HIGH","bls.gov"),
    ("2026-04-10","CPI","Consumer Price Index","March 2026 CPI","HIGH","bls.gov"),
    ("2026-05-13","CPI","Consumer Price Index","April 2026 CPI","HIGH","bls.gov"),
    ("2026-06-10","CPI","Consumer Price Index","May 2026 CPI","HIGH","bls.gov"),
    ("2026-07-14","CPI","Consumer Price Index","June 2026 CPI","HIGH","bls.gov"),
    ("2026-08-12","CPI","Consumer Price Index","July 2026 CPI","HIGH","bls.gov"),
    ("2026-09-10","CPI","Consumer Price Index","August 2026 CPI","HIGH","bls.gov"),
    # NFP 2025
    ("2025-07-03","NFP","Nonfarm Payrolls","June Jobs Report","HIGH","bls.gov"),
    ("2025-08-01","NFP","Nonfarm Payrolls","July Jobs Report","HIGH","bls.gov"),
    ("2025-09-05","NFP","Nonfarm Payrolls","August Jobs Report","HIGH","bls.gov"),
    ("2025-10-03","NFP","Nonfarm Payrolls","September Jobs Report","HIGH","bls.gov"),
    ("2025-11-07","NFP","Nonfarm Payrolls","October Jobs Report","HIGH","bls.gov"),
    ("2025-12-05","NFP","Nonfarm Payrolls","November Jobs Report","HIGH","bls.gov"),
    # NFP 2026 ← NEW
    ("2026-01-09","NFP","Nonfarm Payrolls","December 2025 Jobs","HIGH","bls.gov"),
    ("2026-02-06","NFP","Nonfarm Payrolls","January 2026 Jobs","HIGH","bls.gov"),
    ("2026-03-06","NFP","Nonfarm Payrolls","February 2026 Jobs","HIGH","bls.gov"),
    ("2026-04-02","NFP","Nonfarm Payrolls","March 2026 Jobs","HIGH","bls.gov"),
    ("2026-05-08","NFP","Nonfarm Payrolls","April 2026 Jobs","HIGH","bls.gov"),
    ("2026-06-05","NFP","Nonfarm Payrolls","May 2026 Jobs","HIGH","bls.gov"),
    ("2026-07-02","NFP","Nonfarm Payrolls","June 2026 Jobs","HIGH","bls.gov"),
    ("2026-08-07","NFP","Nonfarm Payrolls","July 2026 Jobs","HIGH","bls.gov"),
    ("2026-09-04","NFP","Nonfarm Payrolls","August 2026 Jobs","HIGH","bls.gov"),
    # PCE 2026 ← NEW
    ("2026-01-30","PCE","PCE / Personal Income","December 2025 PCE","HIGH","bea.gov"),
    ("2026-02-27","PCE","PCE / Personal Income","January 2026 PCE","HIGH","bea.gov"),
    ("2026-03-27","PCE","PCE / Personal Income","February 2026 PCE","HIGH","bea.gov"),
    ("2026-04-30","PCE","PCE / Personal Income","March 2026 PCE","HIGH","bea.gov"),
    ("2026-05-29","PCE","PCE / Personal Income","April 2026 PCE","HIGH","bea.gov"),
    ("2026-06-26","PCE","PCE / Personal Income","May 2026 PCE","HIGH","bea.gov"),
    ("2026-07-31","PCE","PCE / Personal Income","June 2026 PCE","HIGH","bea.gov"),
    ("2026-08-28","PCE","PCE / Personal Income","July 2026 PCE","HIGH","bea.gov"),
    # GDP 2026 ← NEW
    ("2026-01-29","GDP","GDP Advance Estimate","Q4 2025 GDP","HIGH","bea.gov"),
    ("2026-04-29","GDP","GDP Advance Estimate","Q1 2026 GDP","HIGH","bea.gov"),
    ("2026-07-29","GDP","GDP Advance Estimate","Q2 2026 GDP","HIGH","bea.gov"),
    ("2026-10-29","GDP","GDP Advance Estimate","Q3 2026 GDP","HIGH","bea.gov"),
]

def _static_fallback(cutoff_start: date, cutoff_end: date) -> list[dict]:
    out = []
    for ds, cat, title, subtitle, importance, source in _STATIC_MACRO:
        d = date.fromisoformat(ds)
        if cutoff_start <= d <= cutoff_end:
            out.append(_format_event(d, cat, title, subtitle, importance, source))
    return out
