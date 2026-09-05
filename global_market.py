# FILE: global_market.py
"""
Global Market data fetcher
Pulls: World Indices, Futures, Currencies, Commodities, Bond Yields, VIX
All via yfinance - no extra API keys needed
"""
from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
from cache_utils import ttl_cache
from constants import CACHE_TTL_GLOBAL as CACHE_TTL

YIELD_TICKERS = {"^IRX", "^TNX", "^TYX", "^FVX"}

# ── Tickers ────────────────────────────────────────────────────────────────
# ... (ส่วน Tickers และ SESSIONS ไม่มีการเปลี่ยนแปลง)
INDICES = {
    "^GSPC":    ("S&P 500",       "🇺🇸", "US"),
    "^IXIC":    ("Nasdaq Composite", "🇺🇸", "US"),
    "^NDX":     ("Nasdaq 100",       "🇺🇸", "US"),
    "^DJI":     ("Dow Jones",     "🇺🇸", "US"),
    "^RUT":     ("Russell 2000",  "🇺🇸", "US"),
    "^SET.BK":  ("SET Index",     "🇹🇭", "TH"),
    "^HSI":     ("Hang Seng",     "🇭🇰", "HK"),
    "^N225":    ("Nikkei 225",    "🇯🇵", "JP"),
    "^KS11":    ("KOSPI",         "🇰🇷", "KR"),
    "000300.SS":("CSI 300",       "🇨🇳", "CN"),
    "^FTSE":    ("FTSE 100",      "🇬🇧", "EU"),
    "^GDAXI":   ("DAX",           "🇩🇪", "EU"),
    "^FCHI":    ("CAC 40",        "🇫🇷", "EU"),
    "^STOXX50E":("Euro Stoxx 50", "🇪🇺", "EU"),
    "^BSESN":   ("Sensex",        "🇮🇳", "AS"),
    "^AXJO":    ("ASX 200",       "🇦🇺", "AS"),
}

FUTURES = {
    "ES=F":  ("S&P 500 Fut.",  "🇺🇸"),
    "NQ=F":  ("Nasdaq Fut.",   "🇺🇸"),
    "YM=F":  ("Dow Fut.",      "🇺🇸"),
    "RTY=F": ("Russell Fut.",  "🇺🇸"),
    "CL=F":  ("Crude Oil Fut.","🛢️"),
    "GC=F":  ("Gold Fut.",     "🥇"),
}

CURRENCIES = {
    "DX-Y.NYB": ("DXY (Dollar Index)", "💵"),
    "USDTHB=X":  ("USD/THB",           "🇹🇭"),  # ✨ FIX: was THBUSD=X (inverse ticker) — mislabeled as USD/THB while actually quoting THB/USD, which flips FX direction for any downstream calc (e.g. Thai Gold fair value)
    "EURUSD=X":  ("EUR/USD",           "🇪🇺"),
    "GBPUSD=X":  ("GBP/USD",           "🇬🇧"),
    "USDJPY=X":  ("USD/JPY",           "🇯🇵"),
    "USDCNY=X":  ("USD/CNY",           "🇨🇳"),
    "USDKRW=X":  ("USD/KRW",           "🇰🇷"),
    "AUDUSD=X":  ("AUD/USD",           "🇦🇺"),
}

COMMODITIES = {
    "GC=F":  ("Gold",         "🥇", "$/oz"),
    "SI=F":  ("Silver",       "🥈", "$/oz"),
    "CL=F":  ("WTI Crude",    "🛢️", "$/bbl"),
    "BZ=F":  ("Brent Crude",  "🛢️", "$/bbl"),
    "NG=F":  ("Natural Gas",  "🔥", "$/MMBtu"),
    "HG=F":  ("Copper",       "🔶", "$/lb"),
    "ZW=F":  ("Wheat",        "🌾", "cents/bu"),
    "ZS=F":  ("Soybeans",     "🫘", "cents/bu"),
}

BONDS = {
    # ✨ FIX: เปลี่ยนจาก ^FVX (5Y) เป็น ^IRX (3M) เพื่อให้ตรงกับที่ _yield_curve ต้องการใช้
    "^IRX":  ("US 3M T-Bill",  "3M"),
    "^TNX":  ("US 10Y Yield",  "10Y"),
    "^TYX":  ("US 30Y Yield",  "30Y"),
}

FEAR_GREED = {
    "^VIX":   ("VIX (Fear Index)",    "😨"),
    "^VVIX":  ("VVIX (VIX of VIX)",  "📊"),
    "VIXY":   ("VIX ST Futures ETF",  "📈"),
    "HYG":    ("HY Bond (risk-on)",   "💰"),
    "LQD":    ("IG Bond",             "🏦"),
}

# Risk tape only — not part of stock scanner universe
MACRO_TAPE = {
    "BTC-USD":  ("Bitcoin",              "₿"),
    "VOO":      ("Vanguard S&P 500",     "🇺🇸"),
    "IWB":      ("Russell 1000 ETF",     "🇺🇸"),
    "VTWO":     ("Russell 2000 ETF",     "🇺🇸"),
    "VXUS":     ("Total Intl ex-US",     "🌍"),
    "RSP":      ("S&P Equal Weight",     "⚖️"),
    "^VIX":     ("VIX",                  "😨"),
    "^TNX":     ("US 10Y Yield",         "10Y"),
    "^TYX":     ("US 30Y Yield",         "30Y"),
    "CL=F":     ("WTI Crude",            "🛢️"),
    "GC=F":     ("Gold",                 "🥇"),
    "USDJPY=X": ("USD/JPY",              "🇯🇵"),
}

SESSIONS = [
    {"name": "Sydney",    "flag": "🇦🇺", "open": (10, 0),  "close": (16, 0),  "tz": "Australia/Sydney",    "tz_label": "AET"},
    {"name": "Tokyo",     "flag": "🇯🇵", "open": (9, 0),   "close": (15, 0),  "tz": "Asia/Tokyo",          "tz_label": "JST"},
    {"name": "Shanghai",  "flag": "🇨🇳", "open": (9, 30),  "close": (15, 0),  "tz": "Asia/Shanghai",       "tz_label": "CST"},
    {"name": "Hong Kong", "flag": "🇭🇰", "open": (9, 30),  "close": (16, 0),  "tz": "Asia/Hong_Kong",      "tz_label": "HKT"},
    {"name": "Bangkok",   "flag": "🇹🇭", "open": (10, 0),  "close": (16, 30), "tz": "Asia/Bangkok",        "tz_label": "ICT"},
    {"name": "Frankfurt", "flag": "🇩🇪", "open": (9, 0),   "close": (17, 30), "tz": "Europe/Berlin",       "tz_label": "CET"},
    {"name": "London",    "flag": "🇬🇧", "open": (8, 0),   "close": (16, 30), "tz": "Europe/London",       "tz_label": "UK"},
    {"name": "New York",  "flag": "🇺🇸", "open": (9, 30),  "close": (16, 0),  "tz": "America/New_York",    "tz_label": "ET"},
]

# ... (ส่วน _safe_quote, _batch_quotes, _session_status ไม่มีการเปลี่ยนแปลง)
def _norm_yield_value(v):
    """Normalize one Yahoo treasury print. 42.1 → 4.21. Each point on its own."""
    try:
        f = float(v)
    except Exception:
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    if abs(f) > 20:
        f = f / 10.0
    if abs(f) > 20:
        return None
    return f


def _norm_yield_quote(sym: str, q: dict) -> dict:
    """Yahoo บางครั้งส่ง treasury เป็น x10 (เช่น 42.1 = 4.21%)."""
    if not q or sym not in YIELD_TICKERS:
        return q
    price = _norm_yield_value(q.get("price"))
    prev = _norm_yield_value(q.get("prev"))
    if price is None:
        return q
    if prev:
        chg = (price - prev) / prev * 100
    else:
        chg = q.get("chg_pct", 0) or 0
    return {"price": price, "prev": prev or 0, "chg_pct": round(float(chg), 2)}


def _safe_quote(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = float(getattr(info, "last_price", None) or 0)
        prev  = float(getattr(info, "previous_close", None) or 0)
        if price <= 0:
            hist = t.history(period="5d", interval="1d")
            if hist is None or hist.empty:
                return None
            closes = hist["Close"].dropna()
            if closes.empty:
                return None
            price = float(closes.iloc[-1])
            prev  = float(closes.iloc[-2]) if len(closes) >= 2 else price
        chg_pct = ((price - prev) / prev * 100) if prev else 0.0
        return _norm_yield_quote(ticker, {"price": price, "prev": prev, "chg_pct": round(chg_pct, 2)})
    except Exception:
        return None


def batch_quotes(tickers: list[str]) -> dict[str, dict]:
    return _batch_quotes(tickers)


def _batch_quotes(tickers: list[str]) -> dict[str, dict]:
    """Batch download latest prices for a list of tickers."""
    if not tickers:
        return {}
    try:
        raw = yf.download(tickers, period="5d", interval="1d",
                          group_by="ticker", auto_adjust=True,
                          threads=True, progress=False)
    except Exception:
        return {}

    results = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            try:
                closes = raw[t]["Close"].dropna()
                if len(closes) < 2:
                    continue
                price = float(closes.iloc[-1])
                prev  = float(closes.iloc[-2])
                chg   = (price - prev) / prev * 100
                results[t] = {"price": price, "prev": prev, "chg_pct": round(chg, 2)}
            except Exception:
                continue
    else:
        if len(tickers) == 1:
            try:
                closes = raw["Close"].dropna()
                if len(closes) >= 2:
                    price, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                    results[tickers[0]] = {"price": price, "prev": prev,
                                           "chg_pct": round((price-prev)/prev*100, 2)}
            except Exception:
                pass
    missing = [t for t in tickers if t not in results]
    for t in missing:
        q = _safe_quote(t)
        if q:
            results[t] = q
    return {t: _norm_yield_quote(t, q) for t, q in results.items()}


def _session_status() -> list[dict]:
    """Open/close ตามเวลาท้องถิ่น + DST ไม่ใช้ชั่วโมง UTC ตายตัว."""
    result = []
    for s in SESSIONS:
        try:
            now = datetime.now(ZoneInfo(s["tz"]))
        except Exception:
            now = datetime.now(timezone.utc)
        oh, om = s["open"]
        ch, cm = s["close"]
        open_min = oh * 60 + om
        close_min = ch * 60 + cm
        now_min = now.hour * 60 + now.minute
        weekday = now.weekday()  # 0=Mon
        is_weekend = weekday >= 5
        is_open = (not is_weekend) and open_min <= now_min < close_min

        def mins_to(target_min: int, next_day: bool = False) -> int:
            delta = target_min - now_min
            if next_day or delta <= 0:
                delta += 24 * 60
            return int(delta)

        if is_weekend:
            days_ahead = 7 - weekday
            mins = days_ahead * 24 * 60 - now_min + open_min
            label = f"เปิดวันจันทร์ใน {mins//60}h"
            is_open = False
        elif is_open:
            mins = close_min - now_min
            label = f"ปิดใน {mins//60}h {mins%60}m" if mins >= 60 else f"ปิดใน {mins}m"
        else:
            until_open = open_min - now_min
            if until_open <= 0:
                until_open += 24 * 60
            label = f"เปิดใน {until_open//60}h {until_open%60}m" if until_open >= 60 else f"เปิดใน {until_open}m"
        result.append({
            "name": s["name"], "flag": s["flag"], "tz": s.get("tz_label") or s["tz"],
            "is_open": is_open, "label": label,
        })
    return result

def _yield_curve(bond_data: dict) -> dict:
    """Calculate yield curve spread 10Y - 3M and inversion signal."""
    y10 = bond_data.get("^TNX", {}).get("price") or 0
    y3m = bond_data.get("^IRX", {}).get("price") or 0
    spread = round(float(y10) - float(y3m), 3) if y10 and y3m else None
    inverted = spread is not None and spread < 0
    return {"spread_10y_3m": spread, "inverted": inverted,
            "signal": "⚠️ Inverted — Recession Signal" if inverted else "✅ Normal"}

@ttl_cache(CACHE_TTL)
def fetch_global_market() -> dict:
    # ... (ส่วนที่เหลือของฟังก์ชันเหมือนเดิม ไม่มีการเปลี่ยนแปลง)
    """Fetch all global market data and return a JSON-ready dict."""
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Flatten all tickers for one big batch download
    all_tickers = (list(INDICES) + list(FUTURES) + list(CURRENCIES) +
                   list(COMMODITIES) + list(BONDS) + list(FEAR_GREED) +
                   list(MACRO_TAPE))
    # Remove duplicates (GC=F, CL=F appear in both FUTURES and COMMODITIES)
    all_tickers = list(dict.fromkeys(all_tickers))

    quotes = _batch_quotes(all_tickers)

    def build_group(meta_dict, fmt="number"):
        rows = []
        for sym, info in meta_dict.items():
            q = quotes.get(sym)
            if q is None:
                continue
            name = info[0]; icon = info[1] if len(info) > 1 else ""
            unit = info[2] if len(info) > 2 else ""
            rows.append({
                "symbol": sym, "name": name, "icon": icon, "unit": unit,
                "price": q["price"], "chg_pct": q["chg_pct"],
                "direction": "up" if q["chg_pct"] >= 0 else "down",
            })
        return rows

    bond_data = {s: quotes[s] for s in BONDS if s in quotes}
    yc = _yield_curve(bond_data)

    # VIX level → fear/greed label
    vix_price = quotes.get("^VIX", {}).get("price", 0)
    if vix_price >= 40:   fg_label, fg_color = "Extreme Fear 😱", "#ef4444"
    elif vix_price >= 30: fg_label, fg_color = "Fear 😨",         "#f97316"
    elif vix_price >= 20: fg_label, fg_color = "Neutral 😐",      "#f59e0b"
    elif vix_price >= 15: fg_label, fg_color = "Greed 😊",        "#10b981"
    elif vix_price > 0:   fg_label, fg_color = "Extreme Greed 🤑","#059669"
    else:                 fg_label, fg_color = "N/A",             "#6b7280"

    return {
        "ok": True,
        "updated": now_str,
        "sessions": _session_status(),
        "indices":    build_group(INDICES),
        "futures":    build_group(FUTURES),
        "currencies": build_group(CURRENCIES),
        "commodities":build_group(COMMODITIES),
        "bonds":      build_group(BONDS),
        "fear_greed": {
            "vix": vix_price,
            "label": fg_label,
            "color": fg_color,
            "items": build_group(FEAR_GREED),
        },
        "yield_curve": yc,
        "macro_tape": build_group(MACRO_TAPE),
    }
