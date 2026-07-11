"""
Global Market data fetcher
Pulls: World Indices, Futures, Currencies, Commodities, Bond Yields, VIX
All via yfinance - no extra API keys needed
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
from cache_utils import ttl_cache

CACHE_TTL = 10 * 60  # 10 min for global market (faster refresh than universe)

# ── Tickers ────────────────────────────────────────────────────────────────

INDICES = {
    # Symbol: (display_name, flag, region)
    "^GSPC":    ("S&P 500",       "🇺🇸", "US"),
    "^IXIC":    ("Nasdaq 100",    "🇺🇸", "US"),
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
    "THBUSD=X":  ("USD/THB",           "🇹🇭"),
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
    "^IRX":  ("US 3M T-Bill",  "3M"),
    "^FVX":  ("US 5Y Yield",   "5Y"),
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

# Market session schedule (UTC hours)
SESSIONS = [
    {"name": "Sydney",    "flag": "🇦🇺", "open": 21, "close": 6,  "tz": "AEST"},
    {"name": "Tokyo",     "flag": "🇯🇵", "open": 0,  "close": 6,  "tz": "JST"},
    {"name": "Shanghai",  "flag": "🇨🇳", "open": 1,  "close": 7,  "tz": "CST"},
    {"name": "Hong Kong", "flag": "🇭🇰", "open": 1,  "close": 8,  "tz": "HKT"},
    {"name": "Bangkok",   "flag": "🇹🇭", "open": 3,  "close": 10, "tz": "ICT"},
    {"name": "Frankfurt", "flag": "🇩🇪", "open": 7,  "close": 15, "tz": "CET"},
    {"name": "London",    "flag": "🇬🇧", "open": 8,  "close": 16, "tz": "GMT"},
    {"name": "New York",  "flag": "🇺🇸", "open": 13, "close": 20, "tz": "EST"},
]


def _safe_quote(ticker: str) -> dict | None:
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = float(getattr(info, "last_price", None) or 0)
        prev  = float(getattr(info, "previous_close", None) or 0)
        if price <= 0:
            hist = t.history(period="2d", interval="1d")
            if hist.empty or len(hist) < 1:
                return None
            price = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
        chg_pct = ((price - prev) / prev * 100) if prev else 0.0
        return {"price": price, "prev": prev, "chg_pct": round(chg_pct, 2)}
    except Exception:
        return None


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
    return results


def _session_status() -> list[dict]:
    """Return open/close status for each market session."""
    now_utc = datetime.now(timezone.utc)
    hour_utc = now_utc.hour + now_utc.minute / 60
    result = []
    for s in SESSIONS:
        o, c = s["open"], s["close"]
        if o < c:
            is_open = o <= hour_utc < c
        else:  # crosses midnight
            is_open = hour_utc >= o or hour_utc < c
        # minutes until open or close
        def mins_to(target_h):
            diff = (target_h - hour_utc) % 24
            return int(diff * 60)
        if is_open:
            mins = mins_to(c)
            label = f"ปิดใน {mins//60}h {mins%60}m" if mins > 60 else f"ปิดใน {mins}m"
        else:
            mins = mins_to(o)
            label = f"เปิดใน {mins//60}h {mins%60}m" if mins > 60 else f"เปิดใน {mins}m"
        result.append({**s, "is_open": is_open, "label": label})
    return result

def _yield_curve(bond_data: dict) -> dict:
    """Calculate yield curve spread 10Y - 3M and inversion signal."""
    y10 = bond_data.get("^TNX", {}).get("price", 0)
    # ⚡ FIX: เปลี่ยนจาก `^FVX` (5Y) เป็น `^IRX` (3M) เพื่อคำนวณ Spread ที่ถูกต้อง
    y3m  = bond_data.get("^IRX", {}).get("price", 0)
    spread = round(y10 - y3m, 3) if y10 and y3m else None
    inverted = spread is not None and spread < 0
    # ⚡ FIX: อัปเดต Key ของ Dictionary ให้สะท้อนการเปลี่ยนแปลง
    return {"spread_10y_3m": spread, "inverted": inverted,
            "signal": "⚠️ Inverted — Recession Signal" if inverted else "✅ Normal"}

@ttl_cache(CACHE_TTL)
def fetch_global_market() -> dict:
    """Fetch all global market data and return a JSON-ready dict."""
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Flatten all tickers for one big batch download
    all_tickers = (list(INDICES) + list(FUTURES) + list(CURRENCIES) +
                   list(COMMODITIES) + list(BONDS) + list(FEAR_GREED))
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
    }
