"""
ETF Board Data Fetcher
ดึงข้อมูล ETF ทั้งหมดจาก yfinance แล้วคำนวณ:
- RS Rating (1-99) ภายในกลุ่ม ETF
- Volume ratio vs 50-day avg
- Returns 1D / 1W / 1M / 3M / YTD
- Category breakdown
"""
from __future__ import annotations
from datetime import datetime
import numpy as np
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
from etf_meta import ETF_META, ETF_TICKERS, CAT_COLORS, CATEGORIES

CACHE_TTL = 15 * 60   # 15 min


def _batch_history(tickers: list[str], period="1y") -> dict[str, pd.DataFrame]:
    """Download OHLCV history for all ETF tickers in one call."""
    if not tickers:
        return {}
    try:
        raw = yf.download(tickers, period=period, interval="1d",
                          group_by="ticker", auto_adjust=True,
                          threads=True, progress=False)
    except Exception:
        return {}
    if raw is None or raw.empty:
        return {}

    out = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            try:
                df = raw[t][["Open","High","Low","Close","Volume"]].dropna()
                if len(df) >= 30:
                    out[t] = df
            except Exception:
                continue
    else:
        if len(tickers) == 1:
            try:
                df = raw[["Open","High","Low","Close","Volume"]].dropna()
                if len(df) >= 30:
                    out[tickers[0]] = df
            except Exception:
                pass
    return out


def _blended_return(close: pd.Series) -> float:
    """Blended RS return: 40% 3M + 20% 6M + 20% 9M + 20% 12M"""
    def r(n):
        if len(close) <= n:
            return 0.0
        p = close.iloc[-1 - n]
        return (close.iloc[-1] / p - 1) if p else 0.0
    return 0.4*r(63) + 0.2*r(126) + 0.2*r(189) + 0.2*r(252)


def _ret(close: pd.Series, n: int) -> float | None:
    if len(close) <= n:
        return None
    p = close.iloc[-1 - n]
    return round((close.iloc[-1] / p - 1) * 100, 2) if p else None


@ttl_cache(CACHE_TTL)
def fetch_etf_board() -> dict:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Download 1-year daily history for all ETFs
    history = _batch_history(ETF_TICKERS, period="1y")

    rows = []
    blended = {}
    for sym in ETF_TICKERS:
        df = history.get(sym)
        if df is None or df.empty:
            continue
        meta = ETF_META.get(sym, {})
        close = df["Close"]
        vol   = df["Volume"]

        # Price & 1D change
        price = float(close.iloc[-1])
        prev  = float(close.iloc[-2]) if len(close) >= 2 else price
        chg1d = round((price - prev) / prev * 100, 2) if prev else 0.0

        # Volume metrics
        vol_today = float(vol.iloc[-1])
        vol_avg50 = float(vol.tail(51).iloc[:-1].mean()) if len(vol) >= 51 else float(vol.mean())
        vol_ratio = round(vol_today / vol_avg50, 2) if vol_avg50 else 1.0

        # Returns
        r1w  = _ret(close, 5)
        r1m  = _ret(close, 21)
        r3m  = _ret(close, 63)
        r6m  = _ret(close, 126)
        r1y  = _ret(close, 252)

        # YTD return (approx from start of year)
        current_year = datetime.now().year
        ytd_df = df[df.index.year == current_year]
        r_ytd = round((close.iloc[-1] / ytd_df["Close"].iloc[0] - 1) * 100, 2) if len(ytd_df) >= 2 else None

        bl = _blended_return(close)
        blended[sym] = bl

        rows.append({
            "symbol": sym,
            "name":   meta.get("name", sym),
            "cat":    meta.get("cat",  "Other"),
            "sub":    meta.get("sub",  ""),
            "index":  meta.get("index",""),
            "er":     meta.get("er",   None),
            "aum":    meta.get("aum",  None),
            "desc":   meta.get("desc", ""),
            "color":  CAT_COLORS.get(meta.get("cat",""), "#6b7280"),
            "price":  round(price, 2),
            "chg1d":  chg1d,
            "r1w":    r1w,
            "r1m":    r1m,
            "r3m":    r3m,
            "r6m":    r6m,
            "r1y":    r1y,
            "r_ytd":  r_ytd,
            "vol_today": int(vol_today),
            "vol_ratio": vol_ratio,
            "direction": "up" if chg1d >= 0 else "down",
        })

    if not rows:
        return {"ok": False, "error": "ดึงข้อมูล ETF ไม่สำเร็จ", "updated": now_str}

    # RS Rating (1-99) across all ETFs
    bl_series = pd.Series(blended)
    rs_pct    = bl_series.rank(pct=True, method="average")
    rs_rating = (rs_pct * 98 + 1).round().astype(int).clip(1, 99)
    for r in rows:
        r["rs"] = int(rs_rating.get(r["symbol"], 50))

    # Sort screener by RS descending
    rows_sorted = sorted(rows, key=lambda x: x["rs"], reverse=True)

    # Top Gainers / Losers (by 1D%)
    by_1d = sorted(rows, key=lambda x: x["chg1d"], reverse=True)
    gainers = by_1d[:5]
    losers  = by_1d[-5:][::-1]

    # Volume Surge (vol_ratio >= 2.0, sorted by ratio)
    surge = sorted([r for r in rows if r["vol_ratio"] >= 2.0],
                   key=lambda x: x["vol_ratio"], reverse=True)[:10]

    # Sector Rotation — only Sector category ETFs, sorted by 1M
    sector_etfs = [r for r in rows if r["cat"] == "Sector" and r["r1m"] is not None]
    sector_rotation = sorted(sector_etfs, key=lambda x: x["r1m"], reverse=True)

    
    # Category summary
    cat_summary = {}
    for cat in CATEGORIES:
        cat_rows = [r for r in rows if r["cat"] == cat]

        # ✨ FIX: เพิ่ม Guard clause ป้องกัน Division by Zero กรณีที่ไม่มี ETF ใน category นั้นๆ
        if not cat_rows:
            continue

        avg1d = round(sum(r["chg1d"] for r in cat_rows) / len(cat_rows), 2)
        avg1m = None
        m1s = [r["r1m"] for r in cat_rows if r["r1m"] is not None]
        if m1s:
            avg1m = round(sum(m1s) / len(m1s), 2)
        top = sorted(cat_rows, key=lambda x: x["rs"], reverse=True)[:3]
        cat_summary[cat] = {
            "count": len(cat_rows),
            "avg1d": avg1d,
            "avg1m": avg1m,
            "color": CAT_COLORS.get(cat, "#6b7280"),
            "top":   [{"symbol":r["symbol"],"rs":r["rs"],"chg1d":r["chg1d"]} for r in top],
            "direction": "up" if avg1d >= 0 else "down",
        }

