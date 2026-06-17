"""
Market Regime Engine
====================
Classifies the current market environment using a multi-factor scoring system
modeled on what Hedge Funds / CTAs actually use.

Signals used (all from existing data — no new APIs):
  1.  Trend        — S&P 500 price vs SMA50/150/200 + slope of SMA200
  2.  Breadth      — % stocks above MA50 & MA200 (global + US)
  3.  Momentum     — S&P 500 rate-of-change (1M, 3M)
  4.  Volatility   — VIX level + VIX 20-day change
  5.  Credit       — HYG/LQD ratio (risk-on proxy: high yield vs investment grade)
  6.  Yield Curve  — 10Y-3M spread (inversion = warning)
  7.  Risk Assets  — Copper/Gold ratio (Dr. Copper economic signal)
  8.  Internals    — New highs expanding vs breadth divergence

Output:
  regime        : "Bull Market" | "Risk-On" | "Neutral" | "Correction" |
                  "High Volatility" | "Risk-Off" | "Bear Market"
  confidence    : 0-100  (how many signals agree)
  score         : raw composite -100 to +100
  exposure_pct  : suggested position exposure 0-100%
  cash_pct      : suggested cash 0-100%
  signal_table  : list of individual signals with their contribution
  description   : Thai-language interpretation
"""

from __future__ import annotations
from datetime import datetime
import numpy as np
import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache

CACHE_TTL = 15 * 60

# ── Ticker map ──────────────────────────────────────────────────────────────
_TICKERS = {
    "sp500":   "^GSPC",
    "nasdaq":  "^IXIC",
    "vix":     "^VIX",
    "hyg":     "HYG",    # High Yield Bond ETF  → risk-on proxy
    "lqd":     "LQD",    # Investment Grade Bond → risk-off
    "t10y":    "^TNX",   # 10Y Treasury yield
    "t3m":     "^IRX",   # 3M T-Bill yield
    "copper":  "HG=F",   # Copper futures
    "gold":    "GC=F",   # Gold futures
    "tlt":     "TLT",    # Long-duration bond (flight-to-safety)
}


def _download(tickers: list[str], period="1y") -> dict[str, pd.DataFrame]:
    try:
        raw = yf.download(tickers, period=period, interval="1d",
                          group_by="ticker", auto_adjust=True,
                          threads=True, progress=False)
    except Exception:
        return {}
    out = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for t in tickers:
            try:
                df = raw[t].dropna()
                if len(df) >= 20:
                    out[t] = df
            except Exception:
                continue
    else:
        if len(tickers) == 1:
            df = raw.dropna()
            if len(df) >= 20:
                out[tickers[0]] = df
    return out


def _sma(series: pd.Series, n: int) -> float:
    if len(series) < n:
        return float(series.mean())
    return float(series.tail(n).mean())


def _slope_pct(series: pd.Series, n: int = 20) -> float:
    """% change of SMA over last n bars — positive = rising."""
    if len(series) < n + 1:
        return 0.0
    tail = series.tail(n + 1)
    return (tail.iloc[-1] - tail.iloc[0]) / tail.iloc[0] * 100


def _roc(series: pd.Series, n: int) -> float | None:
    if len(series) <= n:
        return None
    return (series.iloc[-1] / series.iloc[-n] - 1) * 100


# ── Individual signal scorers ─────────────────────────────────────────────
# Each returns (score: float -10..+10, label: str, value: str, direction: str)

def _sig_trend(close: pd.Series):
    """S&P 500 price vs SMA50/150/200 (Minervini Trend Template)."""
    p   = close.iloc[-1]
    s50 = _sma(close, 50)
    s150= _sma(close, 150)
    s200= _sma(close, 200)
    slope200 = _slope_pct(close.rolling(200).mean().dropna(), 20)

    score = 0
    if p > s50:  score += 2.5
    if p > s150: score += 2.5
    if p > s200: score += 2.5
    if slope200 > 0: score += 2.5
    # Invert if below key MAs
    if p < s200: score -= 5
    if p < s150 and p < s200: score -= 5

    conds = sum([p > s50, p > s150, p > s200, slope200 > 0])
    label = f"Price > SMA50/150/200 + slope ({conds}/4)"
    val   = f"P={p:.0f} / MA50={s50:.0f} / MA200={s200:.0f}"
    return (score, "Trend", label, val, "up" if score > 0 else "down")


def _sig_breadth(breadth_pct_us_50: float | None, breadth_pct_us_200: float | None):
    """US Market Breadth — % of S&P 500 stocks above MA50 / MA200."""
    if breadth_pct_us_50 is None:
        return (0, "Breadth", "US Market Breadth", "N/A", "neutral")
    score = 0
    if breadth_pct_us_50 > 60:   score += 5
    elif breadth_pct_us_50 > 40: score += 2
    else:                         score -= 5
    if breadth_pct_us_200 is not None:
        if breadth_pct_us_200 > 55:   score += 5
        elif breadth_pct_us_200 > 35: score += 2
        else:                          score -= 5
    val = f"MA50={breadth_pct_us_50:.1f}% / MA200={breadth_pct_us_200:.1f}%" if breadth_pct_us_200 else f"MA50={breadth_pct_us_50:.1f}%"
    return (score, "Breadth", "US Breadth (% above MA50/200)", val, "up" if score > 0 else "down")


def _sig_momentum(close: pd.Series):
    """S&P 500 Rate of Change — 1M and 3M."""
    r1m = _roc(close, 21)
    r3m = _roc(close, 63)
    score = 0
    if r1m is not None:
        if r1m > 2:    score += 3
        elif r1m > 0:  score += 1
        elif r1m > -3: score -= 2
        else:           score -= 5
    if r3m is not None:
        if r3m > 5:    score += 3
        elif r3m > 0:  score += 1
        elif r3m > -8: score -= 2
        else:           score -= 4
    r1s = f"{r1m:+.1f}%" if r1m else "–"
    r3s = f"{r3m:+.1f}%" if r3m else "–"
    return (score, "Momentum", "S&P 500 Rate of Change 1M/3M", f"1M={r1s} / 3M={r3s}", "up" if score > 0 else "down")


def _sig_volatility(vix_close: pd.Series):
    """VIX level + 20-day change."""
    vix = float(vix_close.iloc[-1])
    vix20 = _roc(vix_close, 20)
    score = 0
    if vix < 15:    score += 8
    elif vix < 20:  score += 5
    elif vix < 25:  score += 1
    elif vix < 30:  score -= 4
    elif vix < 40:  score -= 7
    else:            score -= 10
    # Rising VIX = bad
    if vix20 and vix20 > 20: score -= 2
    vix20s = f"{vix20:+.0f}%" if vix20 else "–"
    return (score, "Volatility", "VIX Level + 20D Change", f"VIX={vix:.1f} / 20D Chg={vix20s}", "up" if score > 0 else "down")


def _sig_credit(hyg: pd.Series, lqd: pd.Series):
    """HYG/LQD ratio — risk appetite proxy."""
    if hyg is None or lqd is None or len(hyg) < 21 or len(lqd) < 21:
        return (0, "Credit", "HY/IG Bond Ratio (Risk Appetite)", "N/A", "neutral")
    ratio = hyg["Close"] / lqd["Close"]
    ratio_roc = _roc(ratio, 21)
    score = 0
    if ratio_roc is not None:
        if ratio_roc > 1:    score += 7   # HY outperforming = risk-on
        elif ratio_roc > 0:  score += 3
        elif ratio_roc > -1: score -= 2
        else:                 score -= 7
    rs = f"{ratio_roc:+.2f}%" if ratio_roc else "–"
    return (score, "Credit", "HYG/LQD Ratio (Risk Appetite)", f"1M Chg={rs}", "up" if score > 0 else "down")


def _sig_yield_curve(t10y: pd.Series, t3m: pd.Series):
    """10Y vs 3M T-Bill spread."""
    if t10y is None or t3m is None or len(t10y) < 2 or len(t3m) < 2:
        return (0, "Yield Curve", "10Y - 3M Spread", "N/A", "neutral")
    y10 = float(t10y["Close"].iloc[-1])
    y3m = float(t3m["Close"].iloc[-1])
    spread = y10 - y3m
    score = 0
    if spread > 1.5:   score += 6
    elif spread > 0.5: score += 3
    elif spread > 0:   score += 1
    elif spread > -0.5:score -= 3
    else:               score -= 7
    return (score, "Yield Curve", "US 10Y − 3M Spread", f"{spread:+.2f}% ({'inverted ⚠️' if spread < 0 else 'normal ✅'})", "up" if score > 0 else "down")


def _sig_copper_gold(copper: pd.Series, gold: pd.Series):
    """Copper/Gold ratio — Dr. Copper economic signal."""
    if copper is None or gold is None or len(copper) < 21 or len(gold) < 21:
        return (0, "Risk Assets", "Copper/Gold Ratio (Dr. Copper)", "N/A", "neutral")
    cg = copper["Close"] / gold["Close"]
    cg_roc = _roc(cg, 21)
    score = 0
    if cg_roc is not None:
        if cg_roc > 2:    score += 6
        elif cg_roc > 0:  score += 3
        elif cg_roc > -2: score -= 2
        else:              score -= 6
    rs = f"{cg_roc:+.2f}%" if cg_roc else "–"
    return (score, "Risk Assets", "Copper/Gold Ratio (Dr. Copper)", f"1M Chg={rs}", "up" if score > 0 else "down")


# ── Regime classification ────────────────────────────────────────────────
def _classify(score: float, vix: float) -> dict:
    """Map composite score → regime label + exposure recommendation."""
    # High volatility override
    if vix >= 35:
        return {"regime": "High Volatility 🔥", "regime_en": "High Volatility",
                "exposure": 20, "cash": 80,
                "color": "#ef4444", "bg": "rgba(239,68,68,0.08)",
                "desc": "ความผันผวนสูงมาก VIX ≥35 — ลดสัดส่วนการลงทุนให้เหลือน้อยที่สุด รอให้ตลาดสงบก่อน"}
    if vix >= 28:
        return {"regime": "Risk-Off ⚠️", "regime_en": "Risk-Off",
                "exposure": 35, "cash": 65,
                "color": "#f97316", "bg": "rgba(249,115,22,0.08)",
                "desc": "ความกลัวในตลาดสูง VIX ≥28 — ลดสัดส่วนหุ้น เพิ่ม Cash และ Defensive assets"}

    if score >= 55:
        return {"regime": "Bull Market 🚀", "regime_en": "Bull Market",
                "exposure": 100, "cash": 0,
                "color": "#10b981", "bg": "rgba(16,185,129,0.08)",
                "desc": "ตลาดกระทิงแข็งแกร่ง ทุกสัญญาณสนับสนุน — ลงทุนเต็มพอร์ต เพิ่มขนาด position ได้"}
    elif score >= 30:
        return {"regime": "Risk-On 📈", "regime_en": "Risk-On",
                "exposure": 80, "cash": 20,
                "color": "#10b981", "bg": "rgba(16,185,129,0.06)",
                "desc": "สภาวะ Risk-On สัญญาณส่วนใหญ่เป็นบวก — ลงทุนตามแผนปกติ ถือหุ้น Growth/Momentum ได้"}
    elif score >= 5:
        return {"regime": "Neutral / Choppy ➡️", "regime_en": "Neutral",
                "exposure": 60, "cash": 40,
                "color": "#f59e0b", "bg": "rgba(245,158,11,0.06)",
                "desc": "ตลาดยังไม่ชัดเจน สัญญาณขัดแย้งกัน — ถือ position ขนาดกลาง รอสัญญาณชัดก่อนเพิ่ม"}
    elif score >= -20:
        return {"regime": "Correction ⬇️", "regime_en": "Correction",
                "exposure": 40, "cash": 60,
                "color": "#f97316", "bg": "rgba(249,115,22,0.07)",
                "desc": "ตลาดอยู่ในช่วงปรับฐาน — ลดสัดส่วนหุ้น ไม่เพิ่ม position ใหม่ รอให้ breadth ฟื้น"}
    elif score >= -45:
        return {"regime": "Risk-Off ⚠️", "regime_en": "Risk-Off",
                "exposure": 25, "cash": 75,
                "color": "#ef4444", "bg": "rgba(239,68,68,0.07)",
                "desc": "สัญญาณลบเกิน 60% — ลด exposure มาก เพิ่ม Cash, Gold, Bond"}
    else:
        return {"regime": "Bear Market 🐻", "regime_en": "Bear Market",
                "exposure": 10, "cash": 90,
                "color": "#ef4444", "bg": "rgba(239,68,68,0.10)",
                "desc": "ตลาดหมี สัญญาณลบทุกด้าน — ถือ Cash เป็นหลัก ห้ามซื้อหุ้น Growth เด็ดขาด ☠️"}


@ttl_cache(CACHE_TTL)
def compute_market_regime(
    breadth_us_ma50: float | None = None,
    breadth_us_ma200: float | None = None,
) -> dict:
    """
    Main entry point. Pass in pre-computed breadth from pipeline if available.
    Downloads its own price data for everything else.
    """
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # Download price history
    syms = list(_TICKERS.values())
    hist = _download(syms, period="1y")

    def get(key):
        sym = _TICKERS[key]
        return hist.get(sym)

    sp = get("sp500")
    vix_df = get("vix")
    hyg_df = get("hyg")
    lqd_df = get("lqd")
    t10_df = get("t10y")
    t3m_df = get("t3m")
    cu_df  = get("copper")
    au_df  = get("gold")

    if sp is None or vix_df is None:
        return {"ok": False, "error": "ดึงข้อมูล S&P 500 / VIX ไม่สำเร็จ", "updated": now_str}

    sp_close  = sp["Close"]
    vix_close = vix_df["Close"]
    vix_now   = float(vix_close.iloc[-1])

    # Run all signals
    signals_raw = [
        _sig_trend(sp_close),
        _sig_breadth(breadth_us_ma50, breadth_us_ma200),
        _sig_momentum(sp_close),
        _sig_volatility(vix_close),
        _sig_credit(hyg_df, lqd_df),
        _sig_yield_curve(t10_df, t3m_df),
        _sig_copper_gold(cu_df, au_df),
    ]

    # Weights (must sum to 1.0)
    weights = [0.22, 0.20, 0.15, 0.18, 0.12, 0.08, 0.05]
    max_score_per_signal = 10.0

    composite = 0.0
    signal_table = []
    for (score, category, label, value, direction), w in zip(signals_raw, weights):
        contribution = score * w
        composite += contribution
        pct = round(score / max_score_per_signal * 100)  # normalize -100..+100
        signal_table.append({
            "category":  category,
            "label":     label,
            "value":     value,
            "score":     round(score, 1),
            "weight":    round(w * 100),
            "contribution": round(contribution, 2),
            "direction": direction,
        })

    # Normalize composite to -100..+100
    max_composite = sum(w * max_score_per_signal for w in weights)
    normalized = composite / max_composite * 100

    # Confidence: fraction of signals pointing same direction as composite
    positive_signals = sum(1 for s in signals_raw if s[0] > 0)
    total_signals = len(signals_raw)
    confidence_raw = positive_signals / total_signals if normalized > 0 \
                     else (total_signals - positive_signals) / total_signals
    confidence = round(confidence_raw * 100)

    regime_data = _classify(normalized, vix_now)
    regime_data["confidence"] = confidence

    # Price context
    sp_price = float(sp_close.iloc[-1])
    sp_sma200 = _sma(sp_close, 200)
    sp_pct_above_200 = round((sp_price / sp_sma200 - 1) * 100, 1) if sp_sma200 else 0

    return {
        "ok":            True,
        "updated":       now_str,
        "score":         round(normalized, 1),
        "vix":           round(vix_now, 2),
        "sp500_price":   round(sp_price, 2),
        "sp500_vs_200":  sp_pct_above_200,
        "regime":        regime_data["regime"],
        "regime_en":     regime_data["regime_en"],
        "confidence":    regime_data["confidence"],
        "exposure_pct":  regime_data["exposure"],
        "cash_pct":      regime_data["cash"],
        "color":         regime_data["color"],
        "bg":            regime_data["bg"],
        "description":   regime_data["desc"],
        "signal_table":  signal_table,
        "breadth_us_ma50":  round(breadth_us_ma50, 1) if breadth_us_ma50 else None,
        "breadth_us_ma200": round(breadth_us_ma200, 1) if breadth_us_ma200 else None,
    }
