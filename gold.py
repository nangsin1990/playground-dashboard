# FILE: gold.py
"""
gold.py — Gold Command Center engine (V1.2)
Endpoint: /api/gold

Decision-support heuristic, not a predictive/backtested system.
Do not present scores as win-rate or probability of profit.
yfinance is delayed ~15 min and is not an execution feed.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

import data_engine as eng
import technical_analysis as ta
import global_market as gm
import economic_calendar as ec
from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_GOLD,
    THAI_GOLD_FACTOR,
    GOLD_PREMIUM_USD,
    GOLD_FLAT_PCT,
)

log = logging.getLogger("playground.gold")

SPOT   = "XAUUSD=X"
FUT    = "GC=F"
DXY    = "DX-Y.NYB"
USDTHB = "USDTHB=X"
TNX    = "^TNX"
VIX    = "^VIX"
GLD    = "GLD"
GDX    = "GDX"
SILVER = "SI=F"
COPPER = "HG=F"

GOLD_EVENT_CATEGORIES = getattr(
    ec, "GOLD_EVENT_CATEGORIES", {"FOMC", "MINUTES", "CPI", "NFP", "PCE", "PPI"}
)

TREND_SCORE = {
    "Strong Bullish": 100, "Bullish": 85, "Pullback": 60,
    "Reversal": 55, "Bearish": 20, "Strong Bearish": 0, "N/A": None,
}


def _family_of(label: str) -> str:
    s = (label or "").lower()
    if s.startswith("daily"):
        return "daily_pivot"
    if s.startswith("weekly"):
        return "weekly_pivot"
    if s.startswith("monthly"):
        return "monthly_pivot"
    if s.startswith("h4"):
        return "h4_pivot"
    if "swing fib" in s:
        return "swing_fib"
    if "swing" in s:
        return "swing"
    if s.startswith("sma") or s.startswith("ema"):
        return "moving_average"
    if "52w" in s:
        return "range_52w"
    if s.startswith("pd"):
        return "prev_day"
    if s.startswith("pw"):
        return "prev_week"
    if s.startswith("pm"):
        return "prev_month"
    if s.startswith("bb"):
        return "bollinger"
    if "vwap" in s:
        return "vwap"
    if "poc" in s or "hvn" in s:
        return "volume_profile"
    if "breakout" in s or "rejection" in s:
        return "structure"
    return "other"


def _clamp(v, lo=0, hi=100):
    if v is None:
        return None
    try:
        f = float(v)
    except Exception:
        return None
    if not np.isfinite(f):
        return None
    return max(lo, min(hi, f))


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return None if not np.isfinite(f) else f
    except Exception:
        return None


def _hist(ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    return ta.fetch_ohlcv(ticker, period=period, interval=interval)


def _hist_many(tickers: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        df = _hist(t, period, interval)
        if df is not None and len(df) >= 5:
            out[t] = df
    return out


def _pct_change_n(series: pd.Series, n: int) -> Optional[float]:
    if series is None or len(series) < n + 1:
        return None
    a, b = _safe_float(series.iloc[-1 - n]), _safe_float(series.iloc[-1])
    if a is None or b is None or a == 0:
        return None
    return round((b / a - 1) * 100, 2)


def _norm_yield_pct(v) -> Optional[float]:
    """Yahoo ^TNX is usually 4.25; sometimes 42.5. Normalize each point on its own."""
    f = _safe_float(v)
    if f is None:
        return None
    if abs(f) > 20:
        f = f / 10.0
    if abs(f) > 20:
        return None
    return f


def _bps_change_n(series: pd.Series, n: int) -> Optional[float]:
    """Absolute change of ^TNX quote (4.25 = 4.25%) in basis points."""
    if series is None or len(series) < n + 1:
        return None
    a = _norm_yield_pct(series.iloc[-1 - n])
    b = _norm_yield_pct(series.iloc[-1])
    if a is None or b is None:
        return None
    return round((b - a) * 100.0, 1)


def _last_ts(df: Optional[pd.DataFrame]) -> Optional[str]:
    if df is None or len(df) == 0:
        return None
    idx = df.index[-1]
    try:
        ts = pd.Timestamp(idx)
        if getattr(ts, "tzinfo", None) is not None:
            ts = ts.tz_convert("UTC")
        if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
            return ts.strftime("%Y-%m-%d")
        return ts.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(idx)


def _completed_ohlcv(df: Optional[pd.DataFrame], interval: str = "60m") -> Optional[pd.DataFrame]:
    """Drop the last bar only when it is still forming.

    Daily: drop if the last stamp is today (UTC). Hourly/H4: drop last bar
    only when its timestamp is inside the current bucket.
    """
    if df is None or len(df) < 3:
        return df
    try:
        last = pd.Timestamp(df.index[-1])
        now = pd.Timestamp.now("UTC")
        if getattr(last, "tzinfo", None) is not None:
            last = last.tz_convert("UTC")
        else:
            now = now.tz_localize(None)
        if interval == "1d":
            last_day = last.normalize()
            today = now.normalize()
            return df.iloc[:-1] if last_day >= today else df
        # Intrabar: last stamp newer than ~80% of the bucket → still forming
        age_min = (now - last).total_seconds() / 60.0
        bucket = 60.0 if interval in ("60m", "1h") else 240.0
        if age_min < bucket * 0.85:
            return df.iloc[:-1]
        return df
    except Exception:
        return df.iloc[:-1]


def _trend_label_ema(df: Optional[pd.DataFrame]) -> str:
    if df is None or len(df) < 25:
        return "N/A"
    close = df["Close"]
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=min(50, max(20, len(close) - 1)), adjust=False).mean().iloc[-1]
    last = _safe_float(close.iloc[-1])
    if last is None:
        return "N/A"
    if ema20 >= ema50:
        return "Bullish" if last >= ema20 else "Pullback"
    return "Reversal" if last >= ema20 else "Bearish"


def _d1_structure(df: pd.DataFrame) -> tuple[str, Optional[float]]:
    if df is None or len(df) < 25:
        return "N/A", None
    close = df["Close"]
    last = _safe_float(close.iloc[-1])
    ema20 = _safe_float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = _safe_float(close.ewm(span=min(50, max(20, len(close) - 1)), adjust=False).mean().iloc[-1])
    if last is None or ema20 is None or ema50 is None:
        return "N/A", None
    sma50 = float(df["SMA50"].iloc[-1]) if "SMA50" in df.columns and pd.notna(df["SMA50"].iloc[-1]) else None
    sma200 = float(df["SMA200"].iloc[-1]) if "SMA200" in df.columns and pd.notna(df["SMA200"].iloc[-1]) else None
    slope_up = False
    if "SMA50" in df.columns and df["SMA50"].notna().sum() >= 12:
        slope_up = float(df["SMA50"].iloc[-1]) > float(df["SMA50"].iloc[-10])

    stacked = last > ema20 > ema50
    stacked_down = last < ema20 < ema50
    above_smmas = sma50 is not None and sma200 is not None and last > sma50 > sma200
    below_smmas = sma50 is not None and sma200 is not None and last < sma50 < sma200

    if stacked and above_smmas and slope_up:
        return "Strong Bullish", 100.0
    if stacked_down and below_smmas:
        return "Strong Bearish", 0.0
    if stacked and (sma200 is None or last > sma200):
        return "Bullish", 85.0
    if last >= ema20 >= ema50:
        return "Bullish", 80.0
    if ema20 >= ema50 and last < ema20:
        return "Pullback", 60.0
    if last >= ema20:
        return "Reversal", 55.0
    if stacked_down:
        return "Bearish", 20.0
    return "Bearish", 25.0


def _bb_width_pctile(df: pd.DataFrame, lookback: int = 60) -> tuple[Optional[float], Optional[float]]:
    need = {"BB_UPPER", "BB_LOWER", "BB_MID"}
    if df is None or not need.issubset(df.columns):
        return None, None
    width = (df["BB_UPPER"] - df["BB_LOWER"]) / df["BB_MID"].replace(0, np.nan) * 100
    width = width.dropna()
    if len(width) < 20:
        return None, None
    tail = width.tail(lookback)
    cur = float(tail.iloc[-1])
    pctile = float((tail <= cur).mean() * 100)
    return round(cur, 3), round(pctile, 1)


def _candidate_levels(df_d1: pd.DataFrame, pivots: dict, vp: dict) -> list[dict]:
    out = []

    def add(price, label):
        px = _safe_float(price)
        if px is not None and px > 0:
            out.append({"price": px, "label": label, "family": _family_of(label)})

    for scope in ("daily", "weekly", "monthly", "h4"):
        pack = pivots.get(scope)
        if not pack:
            continue
        for scheme in ("classic", "fibonacci"):
            lv = pack.get(scheme, {})
            for k in ("R3", "R2", "R1", "PP", "S1", "S2", "S3"):
                add(lv.get(k), f"{scope.capitalize()} {scheme[:3].title()} {k}")

    close = df_d1["Close"]
    if len(df_d1) >= 20:
        add(close.ewm(span=20, adjust=False).mean().iloc[-1], "EMA20")
    if "SMA50" in df_d1.columns:
        add(df_d1["SMA50"].iloc[-1], "SMA50")
    if "SMA200" in df_d1.columns:
        add(df_d1["SMA200"].iloc[-1], "SMA200")
    if "BB_UPPER" in df_d1.columns:
        add(df_d1["BB_UPPER"].iloc[-1], "BB Upper")
        add(df_d1["BB_LOWER"].iloc[-1], "BB Lower")
    if "VWAP" in df_d1.columns:
        add(df_d1["VWAP"].iloc[-1], "VWAP")

    if len(df_d1) >= 2:
        prev = df_d1.iloc[-2]
        add(prev["High"], "PDH")
        add(prev["Low"], "PDL")

    def _prev_hl(rule, lab_h, lab_l):
        try:
            bars = df_d1.resample(rule).agg({"High": "max", "Low": "min"}).dropna()
            if len(bars) >= 2:
                add(bars.iloc[-2]["High"], lab_h)
                add(bars.iloc[-2]["Low"], lab_l)
        except Exception:
            pass

    _prev_hl("W-FRI", "PWH", "PWL")
    _prev_hl("ME", "PMH", "PML")

    if len(df_d1) >= 60:
        tail60 = df_d1.tail(60)
        add(float(tail60["Low"].min()), "60D Swing Low")
        add(float(tail60["High"].max()), "60D Swing High")
        try:
            hi_idx = tail60["High"].idxmax()
            prior = tail60.drop(index=hi_idx)
            if len(prior):
                prior_high = float(prior["High"].max())
                if abs(prior_high - float(tail60["High"].max())) > 1e-6:
                    add(prior_high, "Prev Swing High")
        except Exception:
            pass
    if len(df_d1) >= 252:
        add(float(df_d1["High"].tail(252).max()), "52W High")
        add(float(df_d1["Low"].tail(252).min()), "52W Low")

    if len(df_d1) >= 40:
        tail = df_d1.tail(80)
        lo = float(tail["Low"].min())
        hi = float(tail["High"].max())
        rng = hi - lo
        if rng > 0:
            add(hi - 0.382 * rng, "Swing Fib 38.2%")
            add(hi - 0.500 * rng, "Swing Fib 50%")
            add(hi - 0.618 * rng, "Swing Fib 61.8%")

    if vp:
        add(vp.get("poc_price"), "VP POC")
        levels = vp.get("price_levels") or []
        pcts = vp.get("volume_pct") or []
        if levels and pcts and len(levels) == len(pcts):
            avg = sum(pcts) / len(pcts)
            poc = vp.get("poc_price") or 0
            for px, pct in zip(levels, pcts):
                if pct >= avg * 1.25 and abs(px - poc) > 1e-6:
                    add(px, "VP HVN")
    return out


def _cluster_zones(levels: list[dict], current_price: float, atr: float) -> list[dict]:
    """Cluster nearby levels. Confluence = unique families, not raw print count."""
    width = max(atr * 0.25, current_price * 0.0015) if atr else current_price * 0.003
    pts = sorted(levels, key=lambda x: x["price"])
    zones = []
    for p in pts:
        placed = False
        for z in zones:
            if abs(p["price"] - z["_center"]) <= width:
                z["_prices"].append(p["price"])
                z["labels"].append(p["label"])
                z["families"].add(p["family"])
                z["_center"] = sum(z["_prices"]) / len(z["_prices"])
                placed = True
                break
        if not placed:
            zones.append({
                "_prices": [p["price"]],
                "_center": p["price"],
                "labels": [p["label"]],
                "families": {p["family"]},
            })

    result = []
    for z in zones:
        lo, hi = min(z["_prices"]), max(z["_prices"])
        families = sorted(z["families"])
        confluence = len(families)
        extra = max(0, len(z["_prices"]) - confluence)
        strength = _clamp(round(25 + confluence * 18 + min(extra, 3) * 4)) or 25
        mid = (lo + hi) / 2
        if hi < current_price:
            side = "support"
        elif lo > current_price:
            side = "resistance"
        else:
            side = "inside"
        result.append({
            "low": round(lo, 2), "high": round(hi, 2),
            "confluence": confluence, "strength": strength,
            "labels": z["labels"][:6], "families": families[:6],
            "side": side, "mid": round(mid, 2),
        })
    result.sort(key=lambda z: abs(z["mid"] - current_price))
    return result


def _first_crossed_zone(zones: list[dict], prev: Optional[float], curr: Optional[float], direction: str) -> Optional[dict]:
    """First structural level crossed between two *closed* prints, not the zone nearest now."""
    if prev is None or curr is None or not zones:
        return None
    if direction == "up":
        crossed = [
            z for z in zones
            if z.get("side") in ("resistance", "inside") and prev <= z["high"] < curr
        ]
        crossed.sort(key=lambda z: z["high"])
        return crossed[0] if crossed else None
    crossed = [
        z for z in zones
        if z.get("side") in ("support", "inside") and prev >= z["low"] > curr
    ]
    crossed.sort(key=lambda z: -z["low"])
    return crossed[0] if crossed else None


def _to_thb_zone(usd_low: float, usd_high: float, usdthb: float, premium: float) -> dict:
    lo = (usd_low + premium) * usdthb * THAI_GOLD_FACTOR
    hi = (usd_high + premium) * usdthb * THAI_GOLD_FACTOR
    return {"low": round(lo), "high": round(hi)}


def _driver_decomposition(gold_pct: float, usdthb_pct: float) -> dict:
    g_flat = abs(gold_pct) < GOLD_FLAT_PCT
    f_flat = abs(usdthb_pct) < GOLD_FLAT_PCT
    g, f = gold_pct / 100.0, usdthb_pct / 100.0
    thai_total = ((1 + g) * (1 + f) - 1) * 100
    interaction = thai_total - gold_pct - usdthb_pct

    if g_flat and f_flat:
        tag, note = "FLAT", "ทองโลกและเงินบาทแทบไม่ขยับ — ยังไม่มีแรงขับทองไทยวันนี้"
    elif g_flat:
        tag, note = "MIXED_FLAT", "ทองโลกนิ่ง แรงวันนี้มาจากค่าเงินเป็นหลัก"
    elif f_flat:
        tag, note = "MIXED_FLAT", "ค่าเงินนิ่ง แรงวันนี้มาจากราคาทองโลกเป็นหลัก"
    elif gold_pct < 0 and usdthb_pct < 0:
        tag, note = "DOUBLE_PRESSURE", "ทองโลกร่วงและเงินบาทแข็งพร้อมกัน — แรงกดทองไทยมาจากสองด้าน"
    elif gold_pct > 0 and usdthb_pct > 0:
        tag, note = "DOUBLE_TAILWIND", "ทองโลกขึ้นและเงินบาทอ่อนพร้อมกัน — แรงหนุนสองเด้ง ห้ามไล่ราคา"
    elif gold_pct < 0 <= usdthb_pct:
        tag, note = "THAI_GOLD_CUSHIONED", "ทองโลกกำลังลง แต่เงินบาทอ่อนกำลังพยุงราคาทองไทยไว้"
    else:
        tag, note = "GOLD_UP_THAI_MUTED", "ทองโลกขึ้น แต่เงินบาทแข็งทำให้ทองไทยขึ้นน้อยกว่าทองโลก"

    return {
        "model_implied_thai_gold_pct": round(thai_total, 2),
        "thai_gold_pct": round(thai_total, 2),
        "gold_spot_effect_pct": round(gold_pct, 2),
        "baht_effect_pct": round(usdthb_pct, 2),
        "interaction_pct": round(interaction, 2),
        "state": tag,
        "note": note,
        "is_model_implied": True,
    }


def _momentum_score(snap: dict) -> Optional[float]:
    rsi = snap.get("rsi")
    if rsi is None:
        return None
    score = _clamp(40 + (rsi - 30) * 1.2)
    macd_hist = snap.get("macd_hist")
    if score is not None and macd_hist is not None:
        score = _clamp(score + (10 if macd_hist > 0 else -10))
    stoch = snap.get("stoch_k")
    if score is not None and stoch is not None:
        score = _clamp(score + (5 if 20 < stoch < 80 else -5))
    return score


def _weighted_avg(parts: list[tuple[Optional[float], float]]) -> Optional[float]:
    num = den = 0.0
    for val, w in parts:
        if val is None or w <= 0:
            continue
        num += float(val) * w
        den += w
    if den <= 0:
        return None
    clamped = _clamp(num / den)
    return None if clamped is None else round(clamped, 1)


def _vol_direction_pts(gold_chg: Optional[float], gc_vol_ratio: Optional[float]) -> Optional[float]:
    if gc_vol_ratio is None:
        return None
    thick = gc_vol_ratio >= 1.3
    thin = gc_vol_ratio < 0.8
    if gold_chg is None or abs(gold_chg) < GOLD_FLAT_PCT:
        return 55.0 if thick else 45.0
    if gold_chg > 0:
        if thick:
            return _clamp(50 + min(gc_vol_ratio, 2.5) * 20)
        if thin:
            return 40.0
        return 50.0
    if thick:
        return _clamp(50 - min(gc_vol_ratio, 2.5) * 20)
    return 48.0


def _crossed_up(prev: Optional[float], curr: Optional[float], level: float) -> bool:
    return prev is not None and curr is not None and prev <= level < curr


def _crossed_down(prev: Optional[float], curr: Optional[float], level: float) -> bool:
    return prev is not None and curr is not None and prev >= level > curr


def _market_state(d1, h4, h1, breakout, pullback, near_support, near_resistance,
                  breakout_confirmed, support_broken) -> tuple[str, str]:
    if breakout_confirmed:
        return "BREAKOUT_CONFIRMED", "🔥 BREAKOUT CONFIRMED"
    if support_broken and d1 in ("Bearish", "Strong Bearish") and h4 == "Bearish":
        return "BREAKDOWN", "🔴 BREAKDOWN"
    if d1 in ("Bearish", "Strong Bearish") and h4 == "Bearish":
        return "BEARISH_TREND", "🔴 BEARISH TREND"
    if pullback is not None and pullback >= 70:
        return "PULLBACK_RISK", "🟠 PULLBACK RISK"
    if breakout is not None and breakout >= 75 and near_resistance:
        return "BREAKOUT_WATCH", "🔵 BREAKOUT WATCH"
    if d1 in ("Bullish", "Strong Bullish") and h4 == "Pullback" and h1 in ("Reversal", "Bullish") and near_support:
        return "HEALTHY_PULLBACK", "🟢 HEALTHY PULLBACK"
    if d1 in ("Bullish", "Strong Bullish") and near_support:
        return "BUY_ZONE", "🟢 BUY ZONE"
    return "WAIT", "🟡 WAIT"


def _gold_macro_events() -> Optional[dict]:
    try:
        fn = getattr(ec, "next_gold_macro_event", None)
        if callable(fn):
            return fn()
    except Exception:
        log.exception("gold macro events failed")
    return None


def _fmt_zone(z) -> str | None:
    if not z:
        return None
    return f"${z['low']:.2f}–{z['high']:.2f}"


def _coverage_score(coverage: dict) -> float:
    """ok = 1.0, fallback_futures = 0.5, missing/other = 0. Matches UI pills."""
    if not coverage:
        return 0.0
    total = 0.0
    for v in coverage.values():
        if v == "ok":
            total += 1.0
        elif v == "fallback_futures":
            total += 0.5
    return round(100.0 * total / len(coverage), 1)


def _plan_for_state(state_key: str, price: float, atr: float,
                    nearest_support, nearest_resistance, breakout_level) -> dict:
    atr = atr or price * 0.01

    def px(v):
        return None if v is None else round(float(v), 2)

    trigger = "รอแท่ง H1 ปิดยืนยันทิศทาง"
    invalidation = f"H1 close หลุด ${px(price - atr)}"
    zone = None

    if state_key in ("BUY_ZONE", "HEALTHY_PULLBACK") and nearest_support:
        zone = _fmt_zone(nearest_support)
        trigger = f"H1 close กลับมาอยู่เหนือ ${nearest_support['high']:.2f}"
        invalidation = f"H1 close หลุด ${px(nearest_support['low'] - atr * 0.15)}"
    elif state_key == "PULLBACK_RISK":
        zone = _fmt_zone(nearest_support)
        trigger = "อย่าไล่ซื้อ — รอ H4 หยุดทำต่ำใหม่ หรือวอลุ่มขายเบาลง"
        invalidation = (
            f"H1 close หลุด ${px(nearest_support['low'] - atr * 0.15)}"
            if nearest_support else f"H1 close หลุด ${px(price - atr)}"
        )
    elif state_key == "BEARISH_TREND":
        zone = _fmt_zone(nearest_support)
        trigger = "ยังไม่ช้อน — D1 และ H4 ยังเป็นขาลง"
        invalidation = (
            f"H1 close กลับมาอยู่เหนือ ${nearest_support['high']:.2f}"
            if nearest_support else f"H1 close กลับมาอยู่เหนือ ${px(price + atr)}"
        )
    elif state_key == "BREAKOUT_WATCH" and nearest_resistance:
        zone = _fmt_zone(nearest_resistance)
        trigger = f"H1 close ยืนเหนือ ${nearest_resistance['high']:.2f}"
        invalidation = f"H1 close กลับมาต่ำกว่า ${nearest_resistance['low']:.2f}"
    elif state_key == "BREAKOUT_CONFIRMED" and breakout_level:
        zone = _fmt_zone(breakout_level)
        trigger = f"แท่ง H1 ปิดแล้วทะลุ ${breakout_level['high']:.2f} พร้อมวอลุ่ม GC"
        invalidation = f"H1 close กลับมาต่ำกว่า ${breakout_level['low']:.2f}"
    elif state_key == "BREAKDOWN" and nearest_support:
        zone = _fmt_zone(nearest_support)
        trigger = "อย่าช้อนจนกว่า H4 จะหยุดทำต่ำใหม่"
        invalidation = f"H1 close กลับมาอยู่เหนือ ${nearest_support['high']:.2f}"
    elif state_key == "WAIT":
        zone = None
        trigger = "ยังไม่มีโซน/ทิศชัด — รอปิดแท่ง H1 ที่แนว"
        invalidation = "ยังไม่เปิดออเดอร์"
    return {"trigger": trigger, "invalidation": invalidation, "zone": zone}


def _bust_gold_cache(fn) -> None:
    for attr in ("cache_clear", "clear", "invalidate"):
        cb = getattr(fn, attr, None)
        if callable(cb):
            try:
                cb()
                return
            except TypeError:
                try:
                    cb()
                except Exception:
                    pass
            except Exception:
                pass
    for attr in ("_cache", "cache", "store"):
        bag = getattr(fn, attr, None)
        if isinstance(bag, dict):
            bag.clear()
            return


def fetch_gold(refresh: bool = False) -> dict:
    """Public entry. refresh=True ล้าง TTL cache ถ้าตัวห่อรองรับ แล้วคำนวณใหม่."""
    if refresh:
        _bust_gold_cache(fetch_gold_cached)
    return fetch_gold_cached()


@ttl_cache(CACHE_TTL_GOLD)
def fetch_gold_cached() -> dict:
    return _compute_gold()


def _compute_gold() -> dict:
    try:
        now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
        coverage = {}

        df_spot_d1 = ta.fetch_ohlcv(SPOT, period="18mo", interval="1d")
        df_fut_d1 = ta.fetch_ohlcv(FUT, period="18mo", interval="1d")
        if df_spot_d1 is None or len(df_spot_d1) < 30:
            df_spot_d1 = df_fut_d1
            coverage["spot"] = "fallback_futures" if df_fut_d1 is not None else "missing"
        else:
            coverage["spot"] = "ok"
        coverage["futures"] = "ok" if df_fut_d1 is not None else "missing"
        if df_spot_d1 is None or len(df_spot_d1) < 30:
            return {"ok": False, "error": "Gold data temporarily unavailable"}

        df_spot_live = eng.add_technical_indicators(df_spot_d1)
        df_spot_d1 = _completed_ohlcv(df_spot_live, interval="1d")
        if df_spot_d1 is None or len(df_spot_d1) < 30:
            df_spot_d1 = df_spot_live

        df_hourly = _hist(SPOT, "60d", "60m")
        if df_hourly is None and coverage.get("spot") == "fallback_futures":
            df_hourly = _hist(FUT, "60d", "60m")
        coverage["hourly"] = "ok" if df_hourly is not None else "missing"

        df_h1_done = _completed_ohlcv(df_hourly, interval="60m")
        df_h4 = None
        if df_hourly is not None and len(df_hourly) >= 30:
            resample_h4 = getattr(eng, "resample_h4", None)
            if callable(resample_h4):
                df_h4 = resample_h4(df_hourly, align="comex")
            else:
                try:
                    df_h = df_hourly.copy()
                    idx = pd.to_datetime(df_h.index)
                    if getattr(idx, "tz", None) is None:
                        idx = idx.tz_localize("UTC")
                    df_h.index = idx.tz_convert("America/New_York")
                    df_h4 = df_h.resample("4h", offset="2h").agg(
                        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
                    ).dropna()
                except Exception:
                    df_h4 = df_hourly.resample("4h").agg(
                        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
                    ).dropna()
            if df_h4 is not None and len(df_h4) >= 4:
                df_h4 = _completed_ohlcv(df_h4, interval="4h")

        d1_label, d1_pts = _d1_structure(df_spot_d1)
        h4_label = _trend_label_ema(df_h4)
        h1_label = _trend_label_ema(df_h1_done)
        trend_scores = {
            "D1": d1_pts,
            "H4": TREND_SCORE.get(h4_label),
            "H1": TREND_SCORE.get(h1_label),
        }
        trend_labels = {"D1": d1_label, "H4": h4_label, "H1": h1_label}

        snap_live = eng.tech_snapshot(df_spot_live)
        snap = eng.tech_snapshot(df_spot_d1)
        _hourly = df_h1_done if df_h1_done is not None else df_hourly
        try:
            pivots = eng.pivot_pack(df_spot_d1, hourly=_hourly, h4_align="comex")
        except TypeError:
            pivots = eng.pivot_pack(df_spot_d1, hourly=_hourly)
        vp = {}
        try:
            vp = ta.volume_profile(df_spot_d1, days=60, buckets=12) or {}
        except Exception:
            vp = {}
        coverage["volume_profile"] = "ok" if vp.get("poc_price") else "missing"

        current_price = _safe_float(snap_live.get("price")) or _safe_float(snap.get("price"))
        if current_price is None:
            return {"ok": False, "error": "Gold data temporarily unavailable (no spot price)"}
        atr = _safe_float(snap.get("atr")) or (current_price * 0.01)
        gold_chg_pct = snap_live.get("change_pct")

        quotes = gm.batch_quotes([DXY, USDTHB, TNX, VIX, GLD, GDX, SILVER, COPPER, FUT]) or {}

        def _quote_px(sym: str) -> Optional[float]:
            return _safe_float((quotes.get(sym) or {}).get("price"))

        coverage["dxy_quote"] = "ok" if _quote_px(DXY) is not None else "missing"
        coverage["usdthb_quote"] = "ok" if _quote_px(USDTHB) is not None else "missing"
        coverage["us10y_quote"] = "ok" if _quote_px(TNX) is not None else "missing"
        coverage["gld_quote"] = "ok" if _quote_px(GLD) is not None else "missing"
        coverage["gdx_quote"] = "ok" if _quote_px(GDX) is not None else "missing"
        coverage["quotes"] = (
            "ok" if all(coverage[k] == "ok" for k in ("dxy_quote", "usdthb_quote", "us10y_quote"))
            else "missing"
        )

        macro = _hist_many([DXY, TNX, USDTHB, GLD, GDX], "3mo", "1d")
        dxy_hist = macro.get(DXY)
        tnx_hist = macro.get(TNX)
        thb_hist = macro.get(USDTHB)
        gld_hist = macro.get(GLD)
        gdx_hist = macro.get(GDX)
        coverage["dxy"] = "ok" if dxy_hist is not None else "missing"
        coverage["us10y"] = "ok" if tnx_hist is not None else "missing"
        coverage["usdthb"] = "ok" if quotes.get(USDTHB) or thb_hist is not None else "missing"
        coverage["gdx_gld"] = "ok" if gld_hist is not None and gdx_hist is not None else "missing"

        dxy_5d = _pct_change_n(dxy_hist["Close"], 5) if dxy_hist is not None else None
        tnx_5d_pct = _pct_change_n(tnx_hist["Close"], 5) if tnx_hist is not None else None
        tnx_5d_bps = _bps_change_n(tnx_hist["Close"], 5) if tnx_hist is not None else None
        usdthb_1d = quotes.get(USDTHB, {}).get("chg_pct")
        if usdthb_1d is None and thb_hist is not None:
            usdthb_1d = _pct_change_n(thb_hist["Close"], 1)

        gc_vol_ratio = None
        gc_vol_h1_ratio = None
        df_fut_h = _hist(FUT, "60d", "60m")
        coverage["gc_hourly"] = "ok" if df_fut_h is not None else "missing"
        if df_fut_d1 is not None and len(df_fut_d1) >= 22:
            vol = df_fut_d1["Volume"]
            done = vol.iloc[:-1] if len(vol) >= 22 else vol
            avg20 = done.tail(20).mean()
            last_done = float(done.iloc[-1]) if len(done) else None
            if avg20 and last_done:
                gc_vol_ratio = round(last_done / float(avg20), 2)
        if df_fut_h is not None and len(df_fut_h) >= 30:
            done_h = _completed_ohlcv(df_fut_h, interval="60m")
            if done_h is not None and len(done_h) >= 25:
                slot = done_h.index[-1]
                try:
                    slot_ts = pd.Timestamp(slot)
                    hour = slot_ts.hour
                    # เทียบชั่วโมงเดียวกัน และตัดวันเสาร์ที่สภาพคล่องไม่เทียบกัน
                    mask = [
                        pd.Timestamp(x).hour == hour and pd.Timestamp(x).dayofweek != 5
                        for x in done_h.index
                    ]
                    same = done_h.loc[mask, "Volume"]
                    if len(same) >= 8:
                        avg_slot = float(same.iloc[:-1].tail(20).mean())
                        last_slot = float(same.iloc[-1])
                        if avg_slot:
                            gc_vol_h1_ratio = round(last_slot / avg_slot, 2)
                except Exception:
                    gc_vol_h1_ratio = None
        coverage["gc_volume"] = "ok" if gc_vol_ratio is not None else "missing"
        coverage["gc_volume_h1"] = "ok" if gc_vol_h1_ratio is not None else "missing"
        vol_ratio_used = gc_vol_ratio if gc_vol_ratio is not None else gc_vol_h1_ratio

        def _rel(a: Optional[pd.DataFrame], b: Optional[pd.DataFrame], n: int) -> Optional[float]:
            if a is None or b is None:
                return None
            pa, pb = _pct_change_n(a["Close"], n), _pct_change_n(b["Close"], n)
            if pa is None or pb is None:
                return None
            return round(pa - pb, 2)

        gld_chg = quotes.get(GLD, {}).get("chg_pct")
        gdx_chg = quotes.get(GDX, {}).get("chg_pct")
        gdx_vs_gld = {
            "d1": (round(gdx_chg - gld_chg, 2) if gdx_chg is not None and gld_chg is not None else None),
            "d5": _rel(gdx_hist, gld_hist, 5),
            "d20": _rel(gdx_hist, gld_hist, 20),
        }
        gdx_confirm = gdx_vs_gld["d5"] if gdx_vs_gld["d5"] is not None else gdx_vs_gld["d20"]

        momentum_score = _momentum_score(snap)
        bb_width, bb_width_pctile = _bb_width_pctile(df_spot_d1)
        coverage["momentum"] = "ok" if momentum_score is not None else "missing"

        levels = _candidate_levels(df_spot_d1, pivots, vp)
        all_zones = _cluster_zones(levels, current_price, atr)
        support_zones = [z for z in all_zones if z["side"] == "support"]
        resistance_zones = [z for z in all_zones if z["side"] == "resistance"]
        inside_zones = [z for z in all_zones if z["side"] == "inside"][:2]
        for z in inside_zones:
            if z["mid"] <= current_price:
                support_zones.append(z)
            else:
                resistance_zones.append(z)
        support_zones = sorted(support_zones, key=lambda z: abs(z["mid"] - current_price))[:3]
        resistance_zones = sorted(resistance_zones, key=lambda z: abs(z["mid"] - current_price))[:3]
        nearest_support = support_zones[0] if support_zones else None
        nearest_resistance = resistance_zones[0] if resistance_zones else None
        near_support = bool(nearest_support and (current_price - nearest_support["high"]) <= atr * 0.5)
        near_resistance = bool(nearest_resistance and (nearest_resistance["low"] - current_price) <= atr * 0.5)
        dist_to_resistance_atr = (
            (nearest_resistance["low"] - current_price) / atr
            if (nearest_resistance and atr) else None
        )

        h1_prev = h1_curr = None
        if df_h1_done is not None and len(df_h1_done) >= 2:
            h1_curr = float(df_h1_done["Close"].iloc[-1])
            h1_prev = float(df_h1_done["Close"].iloc[-2])

        breakout_level = _first_crossed_zone(all_zones, h1_prev, h1_curr, "up")
        support_break_level = _first_crossed_zone(all_zones, h1_prev, h1_curr, "down")

        vol_ok = bool(
            (gc_vol_ratio is not None and gc_vol_ratio > 1.3)
            or (
                gc_vol_h1_ratio is not None
                and gc_vol_h1_ratio > 1.3
                and (gc_vol_ratio is None or gc_vol_ratio >= 1.0)
            )
        )
        breakout_confirmed = bool(
            breakout_level and vol_ok and h1_curr is not None and h1_curr > breakout_level["high"]
        )
        support_broken = bool(
            support_break_level and h1_curr is not None and h1_curr < support_break_level["low"]
        )

        h4_ema20 = None
        price_below_h4_ema20 = False
        if df_h4 is not None and len(df_h4) >= 20:
            h4_ema20 = float(df_h4["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
            last_h4 = float(df_h4["Close"].iloc[-1])
            price_below_h4_ema20 = last_h4 < h4_ema20

        atr_series = df_spot_d1["ATR"] if "ATR" in df_spot_d1.columns else None
        atr_rising = bool(
            atr_series is not None and len(atr_series.dropna()) >= 6
            and float(atr_series.iloc[-1]) > float(atr_series.iloc[-6])
        )

        dxy_pts = None if dxy_5d is None else _clamp(100 - (_clamp(50 + dxy_5d * 15) or 50))
        tnx_pts = None if tnx_5d_bps is None else _clamp(100 - (_clamp(50 + tnx_5d_bps * 0.6) or 50))
        thb_pts = None if usdthb_1d is None else _clamp(50 + usdthb_1d * 15)
        vol_pts = _vol_direction_pts(gold_chg_pct, vol_ratio_used)
        gdx_pts = None if gdx_confirm is None else _clamp(50 + gdx_confirm * 8)
        sr_score = nearest_support["strength"] if (nearest_support and near_support) else None

        global_bias = _weighted_avg([
            (trend_scores["D1"], 0.28),
            (trend_scores["H4"], 0.14),
            (momentum_score, 0.18),
            (sr_score, 0.10),
            (dxy_pts, 0.12),
            (tnx_pts, 0.10),
            (vol_pts, 0.05),
            (gdx_pts, 0.03),
        ])
        thai_bias = _weighted_avg([
            (global_bias, 0.75),
            (thb_pts, 0.25),
        ])

        squeeze_pts = None if bb_width_pctile is None else _clamp(100 - bb_width_pctile)
        prox_pts = None if dist_to_resistance_atr is None else _clamp(100 - dist_to_resistance_atr * 60)
        breakout_pressure = _weighted_avg([
            (trend_scores["D1"], 0.18),
            (trend_scores["H4"], 0.12),
            (momentum_score, 0.18),
            (prox_pts, 0.22),
            (squeeze_pts, 0.14),
            (vol_pts if (gold_chg_pct or 0) >= 0 else None, 0.10),
            (dxy_pts, 0.06),
        ])
        pullback_risk = _weighted_avg([
            (None if trend_scores["H4"] is None else 100 - trend_scores["H4"], 0.28),
            (None if momentum_score is None else 100 - momentum_score, 0.18),
            (70.0 if price_below_h4_ema20 else 25.0, 0.18),
            (None if dxy_5d is None else _clamp(50 + dxy_5d * 15), 0.12),
            (None if tnx_5d_bps is None else _clamp(50 + tnx_5d_bps * 0.6), 0.10),
            (85.0 if support_broken else 20.0, 0.10),
            (70.0 if atr_rising else 30.0, 0.04),
        ])
        buy_opportunity = _weighted_avg([
            (trend_scores["D1"], 0.22),
            (75.0 if (d1_label in ("Bullish", "Strong Bullish") and h4_label == "Pullback"
                       and h1_label in ("Reversal", "Bullish")) else 35.0, 0.20),
            (sr_score if near_support else 30.0, 0.22),
            (momentum_score, 0.12),
            (dxy_pts, 0.10),
            (tnx_pts, 0.08),
            (thb_pts, 0.06),
        ])

        data_confidence = _coverage_score(coverage)
        limited_data = data_confidence is not None and data_confidence < 60.0

        state_key, state_label = _market_state(
            d1_label, h4_label, h1_label,
            breakout_pressure, pullback_risk, near_support, near_resistance,
            breakout_confirmed, support_broken,
        )
        plan = _plan_for_state(state_key, current_price, atr, nearest_support,
                               nearest_resistance, breakout_level)

        usdthb_price = quotes.get(USDTHB, {}).get("price")
        if usdthb_price is None and thb_hist is not None:
            usdthb_price = float(thb_hist["Close"].iloc[-1])
        premium = float(GOLD_PREMIUM_USD)
        thai_fair_value = None
        if usdthb_price:
            thai_fair_value = round((current_price + premium) * usdthb_price * THAI_GOLD_FACTOR)

        def _zone_entry(z):
            entry = {k: z[k] for k in ("low", "high", "confluence", "strength", "labels", "families", "side")}
            if usdthb_price:
                entry["thb"] = _to_thb_zone(z["low"], z["high"], usdthb_price, premium)
            return entry

        order_map = {
            "buy_zones": [_zone_entry(z) for z in support_zones],
            "sell_zones": [_zone_entry(z) for z in resistance_zones],
            "breakout_level": _zone_entry(breakout_level) if breakout_level else None,
            "invalidation_level": _zone_entry(support_break_level) if support_break_level else (
                _zone_entry(nearest_support) if nearest_support else None
            ),
            "fx_used": usdthb_price,
            "fx_note": "Thai zones at current USD/THB + model premium",
        }

        driver = None
        if gold_chg_pct is not None and usdthb_1d is not None:
            driver = _driver_decomposition(gold_chg_pct, usdthb_1d)

        corr = None
        if thb_hist is not None:
            aligned = pd.concat(
                [df_spot_d1["Close"].rename("gold"), thb_hist["Close"].rename("fx")],
                axis=1, join="inner",
            ).dropna().tail(20)
            if len(aligned) >= 8:
                thai_series = (aligned["gold"] + premium) * aligned["fx"] * THAI_GOLD_FACTOR

                def _idx(s):
                    base = float(s.iloc[0])
                    return [round(float(v) / base * 100, 2) for v in s] if base else []

                corr = {
                    "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in aligned.index],
                    "gold": _idx(aligned["gold"]),
                    "usdthb": _idx(aligned["fx"]),
                    "model_thai_gold": _idx(thai_series),
                    "note": "Indexed to 100 at start of window — model-implied Thai gold, not shop price",
                }

        next_event = _gold_macro_events()

        why = []
        if near_support and nearest_support:
            fam = ", ".join(nearest_support.get("families") or [])
            why.append(
                f"ใกล้แนวรับ {nearest_support['confluence']} families ({fam}) strength {nearest_support['strength']}/100"
            )
        if d1_label in ("Bullish", "Strong Bullish"):
            why.append(f"D1 structure เป็น {d1_label}")
        if h4_label == "Pullback":
            why.append("H4 กำลังย่อในขาขึ้น")
        if snap.get("macd_signal") == "Bullish Crossover":
            why.append("MACD เพิ่งตัดขึ้น (Bullish Crossover)")
        elif (snap.get("macd_hist") or 0) > 0:
            why.append("MACD Histogram เป็นบวก")
        if dxy_5d is not None and dxy_5d < 0:
            why.append(f"DXY อ่อนตัว {dxy_5d:+.2f}% ใน 5 วัน — ช่วยทอง")
        elif dxy_5d is not None:
            why.append(f"⚠️ DXY แข็ง {dxy_5d:+.2f}% ใน 5 วัน — กดทอง")
        if tnx_5d_bps is not None and tnx_5d_bps < 0:
            why.append(f"US10Y ลง {tnx_5d_bps:+.1f} bps ใน 5 วัน — ช่วยทอง")
        elif tnx_5d_bps is not None:
            why.append(f"⚠️ US10Y ขึ้น {tnx_5d_bps:+.1f} bps ใน 5 วัน — กดทอง")
        if vol_ratio_used is not None and vol_ratio_used > 1.3:
            direction = "ซื้อ" if (gold_chg_pct or 0) > 0 else "ขาย" if (gold_chg_pct or 0) < 0 else "รอทิศ"
            why.append(f"GC volume {vol_ratio_used}x — ยืนยันแรง{direction} ไม่ได้แปลว่าทองขึ้น")
        if breakout_confirmed:
            why.append("แท่ง H1 ปิดแล้วทะลุแนวต้าน พร้อมวอลุ่ม")
        if support_broken:
            why.append("แท่ง H1 ปิดต่ำกว่าแนวรับ")
        if next_event:
            why.append(f"⚠️ {next_event['title']} อีก {next_event['days_away']} วัน")

        as_of = {
            "spot": _last_ts(df_spot_d1),
            "futures": _last_ts(df_fut_d1),
            "hourly": _last_ts(df_h1_done),
            "dxy": _last_ts(dxy_hist),
            "usdthb": _last_ts(thb_hist),
            "us10y": _last_ts(tnx_hist),
            "gc_hourly": _last_ts(_completed_ohlcv(df_fut_h) if df_fut_h is not None else None),
        }

        return {
            "ok": True,
            "updated": now_str,
            "note": "⏱ yfinance delayed ~15 min. Scores are heuristic, not probabilities. Thai Fair Value uses Model Premium, not live shop premium.",
            "as_of": as_of,
            "coverage": coverage,
            "data_confidence": data_confidence,
            "prices": {
                "spot": {"symbol": SPOT, "price": current_price, "chg_pct": gold_chg_pct},
                "futures": {"symbol": FUT, "price": quotes.get(FUT, {}).get("price"),
                            "chg_pct": quotes.get(FUT, {}).get("chg_pct")},
                "dxy": {"price": quotes.get(DXY, {}).get("price"), "chg_pct": quotes.get(DXY, {}).get("chg_pct"),
                        "chg_5d_pct": dxy_5d},
                "usdthb": {"price": usdthb_price, "chg_pct": usdthb_1d},
                "us10y": {"price": _norm_yield_pct(quotes.get(TNX, {}).get("price")),
                          "chg_pct": quotes.get(TNX, {}).get("chg_pct"),
                          "chg_5d_pct": tnx_5d_pct, "chg_5d_bps": tnx_5d_bps,
                          "label": "Nominal 10Y Yield — scoring uses bps, not percent return"},
                "vix": {"price": quotes.get(VIX, {}).get("price"), "chg_pct": quotes.get(VIX, {}).get("chg_pct"),
                        "role": "context"},
                "gld": {"price": quotes.get(GLD, {}).get("price"), "chg_pct": gld_chg},
                "gdx": {"price": quotes.get(GDX, {}).get("price"), "chg_pct": gdx_chg, "vs_gld": gdx_vs_gld},
                "silver": {"price": quotes.get(SILVER, {}).get("price"),
                           "chg_pct": quotes.get(SILVER, {}).get("chg_pct"), "role": "context"},
                "copper": {"price": quotes.get(COPPER, {}).get("price"),
                           "chg_pct": quotes.get(COPPER, {}).get("chg_pct"), "role": "context"},
                "gc_volume_ratio": gc_vol_ratio,
                "gc_volume_h1_ratio": gc_vol_h1_ratio,
            },
            "thai_gold": {
                "fair_value": thai_fair_value,
                "factor": round(THAI_GOLD_FACTOR, 10),
                "model_premium_usd": premium,
                "formula": "(Spot + ModelPremium) × USD/THB × (32.148 × 0.965 / 65.6)",
                "disclaimer": "ราคาประมาณการทางทฤษฎี ใช้ Model Premium ไม่ใช่พรีเมียมร้านทอง/สมาคมฯ — ไม่ใช่ราคาซื้อขายจริง",
            },
            "driver_decomposition": driver,
            "correlation": corr,
            "technical": {
                "trend": trend_labels,
                "rsi": snap.get("rsi"), "rsi_signal": snap.get("rsi_signal"),
                "macd_hist": snap.get("macd_hist"), "macd_signal": snap.get("macd_signal"),
                "stoch_k": snap.get("stoch_k"), "bb_pct": snap.get("bb_pct"),
                "bb_width": bb_width, "bb_width_pctile": bb_width_pctile,
                "vwap": snap.get("vwap"), "atr": snap.get("atr"), "atr_pct": snap.get("atr_pct"),
                "h4_ema20": None if h4_ema20 is None else round(h4_ema20, 2),
                "price_history": {
                    "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in df_spot_d1.tail(60).index],
                    "close": [round(float(v), 2) for v in df_spot_d1["Close"].tail(60)],
                },
            },
            "pivots": pivots,
            "volume_profile": {"poc": vp.get("poc_price"), "days": vp.get("days")},
            "order_map": order_map,
            "scores": {
                "global_gold_bias": global_bias,
                "thai_gold_bias": thai_bias,
                "buy_opportunity": buy_opportunity,
                "breakout_pressure": breakout_pressure,
                "pullback_risk": pullback_risk,
                "data_confidence": data_confidence,
                "limited_data": limited_data,
                "gold_bias": global_bias,
            },
            "market_state": {"key": state_key, "label": state_label, **plan},
            "why": why,
            "next_event": next_event,
        }
    except Exception as e:
        log.exception("fetch_gold failed: %s", e)
        return {
            "ok": False,
            "error": f"Gold data temporarily unavailable ({type(e).__name__})",
        }
