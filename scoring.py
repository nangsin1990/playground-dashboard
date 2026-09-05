# -*- coding: utf-8 -*-
"""
Separated scorecards — Quality / Momentum / Valuation / Risk / Data Quality.

Winner composite is a research label, not a quality grade and not a buy order.
MWS v3.8.0
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

try:
    from .config import COMPOSITE_WEIGHTS, DQ_WEIGHTS, NEGATIVE_KW, POSITIVE_KW, SECTOR_FWD_PE_BANDS
    from .utils import is_num, normalize_debt_to_equity
except ImportError:
    from config import COMPOSITE_WEIGHTS, DQ_WEIGHTS, NEGATIVE_KW, POSITIVE_KW, SECTOR_FWD_PE_BANDS
    from utils import is_num, normalize_debt_to_equity


def score_clamp(score, lo=0, hi=100):
    return max(lo, min(hi, score))


def catalyst_to_points(
    catalyst: int,
    lo: int = -3,
    hi: int = 3,
    out: int = 10,
    news_available: bool = True,
) -> int:
    """Map clamped catalyst onto 0..out. No news → 0 points (no free mid-score)."""
    if not news_available:
        return 0
    try:
        c = int(catalyst)
    except (TypeError, ValueError):
        c = 0
    c = max(lo, min(hi, c))
    if hi == lo:
        return out // 2
    return int(round((c - lo) / (hi - lo) * out))


def headline_sentiment(text: str):
    t = (text or "").lower()
    pos = sum(1 for k in POSITIVE_KW if k in t)
    neg = sum(1 for k in NEGATIVE_KW if k in t)
    if pos > neg:
        label = "🟢 Positive"
    elif neg > pos:
        label = "🔴 Negative"
    else:
        label = "🟡 Neutral"
    return label, pos, neg


def earnings_growth_safe(fund: Dict[str, Any]) -> Optional[float]:
    eg = fund.get("earnings_growth")
    if eg is None or (isinstance(eg, float) and np.isnan(eg)):
        return fund.get("earnings_quarterly_growth")
    return eg


def estimate_roic_proxy(fund: Dict[str, Any]) -> Optional[float]:
    """
    Proxy only — not NOPAT / invested capital.
    Blend ROE/ROA; if highly levered, trust ROA more.
    Returns decimal or None.
    """
    roe = fund.get("roe")
    roa = fund.get("roa")
    de = normalize_debt_to_equity(fund.get("debt_to_equity"), fund.get("total_cash"), fund.get("total_debt"))
    if is_num(roa) and is_num(roe):
        inflated = (roe > 0.50) or (roa > 0 and roe > 2.5 * roa)
        if inflated or (is_num(de) and de > 150):
            return float(roa)
        return float(0.6 * roe + 0.4 * roa)
    if is_num(roa):
        return float(roa)
    if is_num(roe) and roe <= 0.50:
        return float(roe)
    return None


def quality_score(fund: Dict[str, Any]) -> Dict[str, Any]:
    detail = []
    pts = 0

    rg = fund.get("revenue_growth")
    eg = earnings_growth_safe(fund)
    fcf = fund.get("free_cashflow")
    ocf = fund.get("operating_cashflow")
    roe = fund.get("roe")
    roa = fund.get("roa")
    roic = estimate_roic_proxy(fund)
    gm = fund.get("gross_margins")
    om = fund.get("operating_margins")
    nm = fund.get("profit_margins")
    de = normalize_debt_to_equity(fund.get("debt_to_equity"), fund.get("total_cash"), fund.get("total_debt"))
    cr = fund.get("current_ratio")
    cash = fund.get("total_cash")
    debt = fund.get("total_debt")

    g = 0
    if is_num(rg):
        if rg > 0.20:
            g += 13
        elif rg > 0.10:
            g += 9
        elif rg > 0.03:
            g += 5
        elif rg < -0.05:
            g -= 6
        else:
            g += 2
    if is_num(eg):
        if eg > 0.20:
            g += 12
        elif eg > 0.08:
            g += 8
        elif eg > 0:
            g += 4
        elif eg < -0.10:
            g -= 6
    pts += max(-8, min(25, g))
    detail.append(f"growth={g}")

    p = 0
    if is_num(roic):
        if roic > 0.20:
            p += 12
        elif roic > 0.12:
            p += 8
        elif roic > 0.06:
            p += 4
        elif roic < 0:
            p -= 6
    elif is_num(roe):
        if roe > 0.20:
            p += 6
        elif roe > 0.12:
            p += 4
        elif roe < 0:
            p -= 5
        if is_num(de) and de > 150 and roe and roe > 0.15:
            p -= 4
            detail.append("ROE haircut (high D/E)")
    if is_num(om):
        if om > 0.20:
            p += 7
        elif om > 0.10:
            p += 4
        elif om < 0:
            p -= 4
    if is_num(gm):
        if gm > 0.50:
            p += 6
        elif gm > 0.30:
            p += 3
    pts += max(-8, min(25, p))
    detail.append(f"profit={p}")

    c = 0
    # Conversion score only when both cash-flow lines are meaningful:
    # OCF must be > 0 and FCF must be >= 0. Negative/negative ratio is not quality.
    if is_num(fcf) and is_num(ocf) and ocf > 0 and fcf >= 0:
        conv = fcf / ocf
        fund["fcf_conversion"] = conv
        if conv > 0.80:
            c += 12
        elif conv > 0.50:
            c += 7
        elif conv > 0:
            c += 3
        else:
            c -= 6
    elif is_num(fcf) and is_num(ocf) and ocf != 0:
        fund["fcf_conversion"] = fcf / ocf
        c -= 6
    elif is_num(fcf):
        if fcf > 0:
            c += 6
        else:
            c -= 6
    if is_num(nm) and is_num(fcf) and fcf > 0 and nm and nm > 0:
        c += 5
    if is_num(ocf) and ocf > 0:
        c += 4
    pts += max(-8, min(25, c))
    detail.append(f"cash={c}")

    b = 0
    if is_num(de):
        if de < 40:
            b += 10
        elif de < 80:
            b += 7
        elif de < 120:
            b += 4
        elif de > 200:
            b -= 8
        elif de > 150:
            b -= 4
    if is_num(cr):
        if cr >= 1.5:
            b += 6
        elif cr >= 1.0:
            b += 3
        elif cr < 0.8:
            b -= 5
    if is_num(cash) and is_num(debt):
        if cash >= debt:
            b += 6
        elif debt > 0 and cash / debt > 0.4:
            b += 3
        elif debt > 0 and cash / debt < 0.15:
            b -= 3
    elif is_num(cash) and cash > 0:
        b += 2
    pts += max(-8, min(25, b))
    detail.append(f"bs={b}")

    has = any(is_num(x) for x in [rg, eg, roe, roa, de, fcf, om, gm])
    raw = score_clamp(int(round(pts))) if has else 0
    return {
        "score": raw if has else 0,
        "available": has,
        "roic_proxy": roic,
        "earnings_growth_used": eg,
        "debt_to_equity_norm": de,
        "detail": detail,
        "verdict": (
            "⚪ Unknown" if not has
            else ("🟢 Strong" if raw >= 72 else ("🟡 Mixed" if raw >= 48 else "🔴 Weak"))
        ),
    }


def momentum_score(
    rs: Dict[str, Optional[float]],
    sec_detail: Dict[str, Any],
    price: Optional[float],
    mas: Dict[str, Any],
    tech: Dict[str, Any],
    catalyst: int,
    accum_proxy: int,
    news_available: bool = True,
) -> Dict[str, Any]:
    pts = 0

    rs_pts = 0
    rs_w = {"1M": 8, "3M": 11, "6M": 9, "12M": 7}
    avail = 0
    got = 0
    for k, w in rs_w.items():
        v = rs.get(k)
        if v is None:
            continue
        avail += w
        if v > 12:
            got += w
        elif v > 4:
            got += w * 0.75
        elif v > 0:
            got += w * 0.5
        elif v > -8:
            got += w * 0.2
    if avail > 0:
        rs_pts = got / avail * 35
    pts += rs_pts

    sec = 0
    if sec_detail.get("available"):
        trend = str(sec_detail.get("trend", ""))
        rot = str(sec_detail.get("rotation", ""))
        if "Strong Uptrend" in trend:
            sec += 5
        elif "Uptrend" in trend:
            sec += 3
        if "Strong Rotation" in rot:
            sec += 5
        elif "Accumulating" in rot:
            sec += 3
        elif "flowing out" in rot:
            sec -= 3
        s3 = sec_detail.get("stock_vs_sector_3M")
        if s3 is not None:
            if s3 > 8:
                sec += 5
            elif s3 > 0:
                sec += 2
            elif s3 < -8:
                sec -= 3
    pts += max(0, min(15, sec))

    trend_pts = 0
    stage = "N/A"
    tech_verdict = "🟡 Base"
    if price and mas.get("MA200"):
        above200 = price > mas["MA200"]
        above50 = mas.get("MA50") is not None and price > mas["MA50"]
        stack = mas.get("MA50") is not None and mas["MA50"] > mas["MA200"]
        above20 = mas.get("MA20") is not None and price > mas["MA20"]
        if above200 and stack and above50:
            stage = "Stage 2 — Uptrend"
            tech_verdict = "🟢 Uptrend"
            trend_pts = 18
            if above20:
                trend_pts += 3
        elif above200 and not above50:
            stage = "Correction — holding 200-day"
            tech_verdict = "🟡 Correction"
            trend_pts = 7
        elif above200:
            stage = "Stage 1/3 — Base / Range"
            tech_verdict = "🟡 Base"
            trend_pts = 10
        elif not above200 and mas.get("MA50") is not None and mas["MA50"] < mas["MA200"]:
            stage = "Stage 4 — Downtrend"
            tech_verdict = "🔴 Downtrend"
            trend_pts = 2
        else:
            stage = "Recovery vs 200-day"
            tech_verdict = "🟡 Recovery"
            trend_pts = 6
    elif price:
        trend_pts = 6
    rsi = tech.get("RSI")
    if rsi is not None:
        if 40 <= rsi <= 68:
            trend_pts += 3
        elif rsi > 75:
            trend_pts -= 3
        elif rsi < 30:
            trend_pts -= 2
    macd_hist = tech.get("MACD_hist")
    if macd_hist is not None and macd_hist > 0 and "Uptrend" in tech_verdict:
        trend_pts += 1
    pts += max(0, min(25, trend_pts))

    vol_pts = max(0, min(15, accum_proxy))
    pts += vol_pts

    cat = catalyst_to_points(catalyst, news_available=news_available)
    pts += max(0, min(10, cat))

    raw = score_clamp(int(round(pts)))
    avg_rs = None
    vals = [v for v in rs.values() if v is not None]
    if vals:
        avg_rs = float(np.nanmean(vals))
    recent_keys = [k for k in ("1M", "3M") if rs.get(k) is not None]
    recent_rs = float(np.nanmean([rs[k] for k in recent_keys])) if recent_keys else avg_rs
    rs_1m = rs.get("1M")
    if recent_rs is None:
        rs_verdict = "⚪ Unknown"
    elif (rs_1m is not None and rs_1m <= -8) or recent_rs <= -5:
        rs_verdict = "🔴 Laggard"
    elif recent_rs > 5 and (rs_1m is None or rs_1m > -5):
        rs_verdict = "🟢 Leader"
    else:
        rs_verdict = "🟡 Neutral"
    return {
        "score": raw,
        "stage": stage,
        "tech_verdict": tech_verdict,
        "rs_verdict": rs_verdict,
        "avg_rs": avg_rs,
        "recent_rs": recent_rs,
        "components": {
            "rs": round(rs_pts, 1),
            "sector": max(0, min(15, sec)),
            "trend": max(0, min(25, trend_pts)),
            "volume": vol_pts,
            "catalyst": max(0, min(10, cat)),
        },
    }


def valuation_score(
    fund: Dict[str, Any],
    peers: List[Dict],
    sector: str,
) -> Dict[str, Any]:
    fpe = fund.get("forward_pe")
    tpe = fund.get("trailing_pe")
    peg = fund.get("peg")
    fcf_y = fund.get("fcf_yield")
    ev = fund.get("ev_ebitda")
    ps = fund.get("price_to_sales")

    has = any(is_num(x) for x in [fpe, tpe, peg, fcf_y, ev, ps])
    if not has:
        return {"score": 0, "available": False, "verdict": "⚪ Unknown", "peer_med_pe": None}

    cheap, fair, rich = SECTOR_FWD_PE_BANDS.get(sector or "", SECTOR_FWD_PE_BANDS["default"])
    pts = 40

    if is_num(fpe) and fpe > 0:
        if fpe < cheap:
            pts += 18
        elif fpe < fair:
            pts += 10
        elif fpe < rich:
            pts += 2
        else:
            pts -= 12
    elif is_num(tpe) and tpe > 0:
        if tpe < cheap:
            pts += 10
        elif tpe > rich:
            pts -= 8

    if is_num(peg) and peg > 0:
        if peg < 1.0:
            pts += 12
        elif peg < 1.5:
            pts += 7
        elif peg < 2.5:
            pts += 2
        else:
            pts -= 6

    if is_num(fcf_y):
        if fcf_y > 6:
            pts += 12
        elif fcf_y > 3:
            pts += 6
        elif fcf_y > 1:
            pts += 2
        elif fcf_y < 0:
            pts -= 8

    if is_num(ev) and ev > 0:
        if ev < 10:
            pts += 6
        elif ev > 25:
            pts -= 4

    peer_med = None
    peer_pes = [p["forward_pe"] for p in peers if p.get("forward_pe") and p["forward_pe"] > 0]
    if peer_pes and is_num(fpe) and fpe > 0:
        peer_med = float(np.median(peer_pes))
        if fpe < peer_med * 0.85:
            pts += 8
        elif fpe > peer_med * 1.25:
            pts -= 8

    raw = score_clamp(int(round(pts)))
    if raw >= 72:
        verdict = "🟢 Attractive"
    elif raw >= 52:
        verdict = "🟡 Fair"
    elif raw >= 38:
        verdict = "🟠 Expensive"
    else:
        verdict = "🔴 Extreme"
    return {
        "score": raw,
        "available": True,
        "verdict": verdict,
        "peer_med_pe": peer_med,
        "sector_bands": (cheap, fair, rich),
    }


def risk_score(
    fund: Dict[str, Any],
    info: Dict[str, Any],
    extra: Dict[str, Any],
    tech: Dict[str, Any],
    price: Optional[float],
    mas: Dict[str, Any],
    regime: str,
) -> Dict[str, Any]:
    r = 20
    flags = []

    de = normalize_debt_to_equity(fund.get("debt_to_equity"), fund.get("total_cash"), fund.get("total_debt"))
    if is_num(de):
        if de > 200:
            r += 18
            flags.append("high leverage")
        elif de > 150:
            r += 10
            flags.append("elevated leverage")
        elif de < 40:
            r -= 4

    cr = fund.get("current_ratio")
    if is_num(cr) and cr < 0.9:
        r += 8
        flags.append("tight liquidity")

    fcf = fund.get("free_cashflow")
    if is_num(fcf) and fcf < 0:
        r += 10
        flags.append("negative FCF")

    beta = info.get("beta")
    if is_num(beta):
        if beta > 1.8:
            r += 10
            flags.append("high beta")
        elif beta > 1.4:
            r += 5

    rsi = tech.get("RSI")
    if is_num(rsi) and rsi > 75:
        r += 10
        flags.append("overextended RSI")
    if price and mas.get("MA20") and price > mas["MA20"] * 1.12:
        r += 8
        flags.append("stretched vs MA20")

    dte = extra.get("days_to_earnings")
    if dte is not None and 0 <= dte <= 10:
        r += 14
        flags.append("earnings window")

    spf = extra.get("short_pct_float_display")
    if is_num(spf) and spf >= 15:
        r += 8
        flags.append("elevated short interest")

    if str(regime).startswith("🔴"):
        r += 8
        flags.append("risk-off regime")

    upside = extra.get("upside_pct")
    if is_num(upside) and upside < -15:
        r += 6
        flags.append("above analyst target")

    raw = score_clamp(int(round(r)))
    if raw >= 70:
        verdict = "🔴 High"
    elif raw >= 45:
        verdict = "🟠 Medium"
    else:
        verdict = "🟢 Contained"
    return {"score": raw, "verdict": verdict, "flags": flags}


def data_quality_score(
    price,
    hist,
    spy_hist,
    fund: Dict[str, Any],
    news_items,
    extra: Dict[str, Any],
    peers: List,
    tech: Dict[str, Any],
) -> Dict[str, Any]:
    components = {}
    components["price"] = 1.0 if price else 0.0
    if hist is None or getattr(hist, "empty", True):
        components["history"] = 0.0
    elif len(hist) >= 200:
        components["history"] = 1.0
    elif len(hist) >= 50:
        components["history"] = 0.6
    else:
        components["history"] = 0.3
    components["spy"] = 1.0 if spy_hist is not None and not getattr(spy_hist, "empty", True) else 0.0

    fund_fields = [
        fund.get("revenue_growth"),
        earnings_growth_safe(fund),
        fund.get("roe"),
        fund.get("free_cashflow"),
        fund.get("debt_to_equity"),
        fund.get("operating_margins"),
    ]
    components["fundamental"] = sum(1 for f in fund_fields if f is not None) / max(len(fund_fields), 1)

    val_fields = [fund.get("forward_pe"), fund.get("peg"), fund.get("fcf_yield"), fund.get("ev_ebitda")]
    components["valuation"] = sum(1 for f in val_fields if f is not None) / max(len(val_fields), 1)

    # Technical completeness: RSI alone is partial; ATR+MACD needed for full credit.
    tech_pts = 0.0
    if tech.get("RSI") is not None:
        tech_pts += 0.4
    if tech.get("ATR") is not None:
        tech_pts += 0.3
    if tech.get("MACD_hist") is not None or tech.get("MACD") is not None:
        tech_pts += 0.3
    elif price:
        tech_pts = max(tech_pts, 0.2)
    components["technical"] = min(1.0, tech_pts)

    components["peers"] = 1.0 if peers else 0.0
    components["news"] = 1.0 if news_items else 0.0

    fred_st = str(extra.get("fred_status", ""))
    if fred_st.startswith("ok"):
        macro = 1.0
    elif "partial" in fred_st:
        macro = 0.5
    else:
        macro = 0.0
    components["macro"] = macro

    overall = 0.0
    for k, w in DQ_WEIGHTS.items():
        overall += components.get(k, 0.0) * w
    overall_pct = float(overall * 100)

    sources = {
        "yahoo_price": "PASS" if components["price"] else "FAIL",
        "history": "PASS" if components["history"] >= 0.6 else ("PARTIAL" if components["history"] else "FAIL"),
        "fundamentals": "PASS" if components["fundamental"] >= 0.7 else (
            "PARTIAL" if components["fundamental"] >= 0.3 else "FAIL"
        ),
        "technical": (
            "PASS" if components["technical"] >= 0.9
            else ("PARTIAL" if components["technical"] >= 0.4 else "FAIL")
        ),
        "news": "PASS" if news_items else "MISSING",
        "finnhub": extra.get("finnhub_status", "no_key"),
        "fred": extra.get("fred_status", "no_key"),
        "peers": "PASS" if peers else "MISSING",
    }
    return {
        "overall": overall_pct,
        "components": components,
        "sources": sources,
    }


def research_composite(quality: int, momentum: int, valuation: int, risk: int) -> Dict[str, Any]:
    """
    Balanced research score. Risk is inverted so high risk lowers the composite.
    Not a buy order.
    """
    w = COMPOSITE_WEIGHTS
    risk_attr = score_clamp(100 - int(risk))
    raw = (
        quality * w["quality"]
        + momentum * w["momentum"]
        + valuation * w["valuation"]
        + risk_attr * w["risk_attractiveness"]
    )
    score = score_clamp(int(round(raw)))
    return {
        "score": score,
        "risk_attractiveness": risk_attr,
        "weights": dict(w),
    }


def dual_labels(quality: int, momentum: int, quality_available: bool = True) -> Dict[str, str]:
    if not quality_available:
        business = "QUALITY N/A"
    elif quality >= 85:
        business = "SOLID BUSINESS+"
    elif quality >= 72:
        business = "SOLID BUSINESS"
    elif quality >= 48:
        business = "MIXED BUSINESS"
    else:
        business = "WEAK BUSINESS"
    if momentum >= 80:
        price = "PRICE LEADER+"
    elif momentum >= 65:
        price = "PRICE LEADER"
    elif momentum >= 50:
        price = "PRICE MIXED"
    else:
        price = "PRICE LAGGARD"
    return {"business": business, "price": price}


def investment_profile(quality: int, momentum: int, valuation: int, risk: int) -> str:
    q = "A+" if quality >= 85 else ("A" if quality >= 72 else ("B" if quality >= 55 else "C"))
    mom = "HIGH MOMENTUM" if momentum >= 75 else ("MID MOMENTUM" if momentum >= 55 else "WEAK MOMENTUM")
    val = "CHEAP" if valuation >= 72 else ("FAIR" if valuation >= 52 else "EXPENSIVE")
    rk = "HIGH RISK" if risk >= 70 else ("MED RISK" if risk >= 45 else "CONTAINED RISK")
    return f"{q} QUALITY / {mom} / {val} / {rk}"


def legacy_bucket_scores(quality: int, momentum: int, valuation: int) -> Dict[str, int]:
    """Kept for continuity only. Classification in v3.4 uses research_composite."""
    mi = int(round(momentum * 0.20))
    rs = int(round(momentum * 0.20))
    tech = int(round(momentum * 0.20))
    sm = int(round(momentum * 0.15))
    fund = int(round(quality * 0.15))
    val = int(round(valuation * 0.10))
    total = mi + rs + tech + sm + fund + val
    return {
        "market_interest": score_clamp(mi, 0, 20),
        "relative_strength": score_clamp(rs, 0, 20),
        "technical": score_clamp(tech, 0, 20),
        "smart_money": score_clamp(sm, 0, 15),
        "fundamental": score_clamp(fund, 0, 15),
        "valuation_risk": score_clamp(val, 0, 10),
        "total": score_clamp(total, 0, 100),
    }
