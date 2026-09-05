# -*- coding: utf-8 -*-
"""Decision gates for dashboard and reports. Not a buy engine. MWS v3.8.0"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from .config import (
        DQ_GATE,
        EARNINGS_WINDOW_DAYS,
        MA20_STRETCH,
        MA20_STRETCH_HARD,
        MA20_STRETCH_ZONE,
        MOMENTUM_STRETCH_RSI,
        MOMENTUM_STRETCH_RSI_HARD,
        QUALITY_PASS,
        QUALITY_SKIP,
        QUALITY_STRONG,
        RSI_STRETCH_ZONE,
        VALUATION_EXPENSIVE_ZONE,
        VALUATION_EXTREME,
    )
except ImportError:  # script / sys.path=mws
    from config import (
        DQ_GATE,
        EARNINGS_WINDOW_DAYS,
        MA20_STRETCH,
        MA20_STRETCH_HARD,
        MA20_STRETCH_ZONE,
        MOMENTUM_STRETCH_RSI,
        MOMENTUM_STRETCH_RSI_HARD,
        QUALITY_PASS,
        QUALITY_SKIP,
        QUALITY_STRONG,
        RSI_STRETCH_ZONE,
        VALUATION_EXPENSIVE_ZONE,
        VALUATION_EXTREME,
    )


def _num(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def evaluate_decision(
    *,
    dq: float,
    quality: int,
    momentum: int,
    valuation: int,
    risk: int,
    tech_verdict: str,
    stage: str,
    rs_verdict: str,
    avg_rs: Optional[float],
    price: Optional[float],
    mas: Dict[str, Any],
    rsi: Optional[float],
    regime: str,
    days_to_earnings: Optional[int],
    fcf: Optional[float],
    de_norm: Optional[float],
    quality_available: bool = True,
    valuation_available: bool = True,
) -> Dict[str, Any]:
    flags: List[str] = []
    emphasize: List[str] = []
    ignore: List[str] = [
        "Single composite score is not a buy order",
        "Institutional % is not smart-money flow",
        "Headline keyword news is a weak signal",
        "Analyst target is not an entry",
        "Macro prints do not move this ticker's stop",
        "ROIC here is a proxy, not true ROIC",
    ]

    dq_pass = dq >= DQ_GATE
    price_ok = price is not None and rsi is not None
    regime_risk_off = str(regime).startswith("🔴")
    business_skip = quality_available and quality < QUALITY_SKIP
    business_pass = quality_available and quality >= QUALITY_PASS
    business_strong = quality_available and quality >= QUALITY_STRONG
    downtrend = "Downtrend" in (tech_verdict or "") or "Stage 4" in (stage or "")
    uptrend = "Uptrend" in (tech_verdict or "")
    correction = "Correction" in (tech_verdict or "") or "Correction" in (stage or "")
    base = ("Base" in (tech_verdict or "") or "Recovery" in (tech_verdict or "")) and not correction
    rs_laggard = "Laggard" in (rs_verdict or "")
    valuation_extreme = valuation_available and valuation < VALUATION_EXTREME
    valuation_zone = valuation_available and (not valuation_extreme) and valuation < VALUATION_EXPENSIVE_ZONE

    stretched_hard = False
    stretched = False
    stretch_zone = False
    if rsi is not None and rsi >= MOMENTUM_STRETCH_RSI_HARD:
        stretched_hard = True
        stretched = True
        flags.append("RSI hard-stretched")
    elif rsi is not None and rsi >= MOMENTUM_STRETCH_RSI:
        stretched = True
        flags.append("RSI above entry cap")
    elif rsi is not None and rsi >= RSI_STRETCH_ZONE:
        stretch_zone = True
        flags.append("RSI entering stretch zone")
    ma20 = _num(mas.get("MA20"))
    if price and ma20:
        ratio = price / ma20
        if ratio >= MA20_STRETCH_HARD:
            stretched_hard = True
            stretched = True
            flags.append("price hard-stretched vs MA20")
        elif ratio >= MA20_STRETCH:
            stretched = True
            flags.append("price extended vs MA20")
        elif ratio >= MA20_STRETCH_ZONE:
            stretch_zone = True
            flags.append("price entering extension zone")

    earnings_window = (
        days_to_earnings is not None and 0 <= int(days_to_earnings) <= EARNINGS_WINDOW_DAYS
    )
    if earnings_window:
        flags.append("earnings window")
    if regime_risk_off:
        flags.append("risk-off regime")
    if fcf is not None and fcf < 0:
        flags.append("negative FCF")
    if de_norm is not None and de_norm > 150:
        flags.append("elevated leverage")
    if valuation_extreme:
        flags.append("valuation extreme")
    elif valuation_zone:
        flags.append("valuation expensive zone")

    if not quality_available:
        flags.append("fundamentals missing")
    if not valuation_available:
        flags.append("valuation missing")

    if not dq_pass:
        stance = "INSUFFICIENT"
        summary = "Data quality below gate — do not treat as a candidate."
        flags.append("data quality fail")
    elif not price_ok:
        stance = "INSUFFICIENT"
        summary = "Price/RSI missing — cannot score timing."
    elif not quality_available:
        stance = "PRICE_ONLY"
        summary = "Fundamentals incomplete — price action only, no quality score."
    elif downtrend:
        stance = "AVOID_LONG"
        summary = "Price structure is downtrend — no long setup on this system."
    elif business_skip and momentum >= 75:
        stance = "PRICE_ONLY"
        summary = "Price leads but quality is weak — timing watch only, not a hold."
    elif not business_pass:
        stance = "SKIP"
        summary = "Quality fails the filter."
    elif stretched or stretch_zone:
        stance = "WAIT_PULLBACK"
        summary = "Quality is fine but price is extended — wait pullback."
    elif uptrend and business_pass and not rs_laggard and not regime_risk_off and (valuation_extreme or valuation_zone):
        stance = "WATCH_EXPENSIVE"
        summary = "Quality + uptrend pass, valuation is rich — watch, not candidate."
    elif uptrend and business_pass and not rs_laggard and not regime_risk_off:
        stance = "CANDIDATE"
        summary = "Quality, timing, and valuation clear the filter. Research candidate, not a buy call."
    elif correction and business_pass:
        stance = "WATCH_CORRECTION"
        summary = "Quality is fine but price is in correction — wait hold above MA200."
    elif base and business_pass:
        stance = "WATCH_BASE"
        summary = "Quality is fine but price is still in a base."
    else:
        stance = "WATCH"
        summary = "Quality and timing are not both through yet."

    if dq_pass:
        emphasize.append("Data quality pass")
    emphasize.append("Split quality vs timing vs valuation")
    emphasize.append("One stop only: trade_stop")
    emphasize.append("Analyst target is hidden and unused")
    if earnings_window:
        emphasize.append("Earnings window — do not size up")
    if valuation_extreme:
        emphasize.append("Valuation extreme — do not read this as an entry")

    return {
        "stance": stance,
        "summary": summary,
        "is_buy_signal": False,
        "gates": {
            "data_quality_pass": dq_pass,
            "regime_risk_off": regime_risk_off,
            "business_skip": business_skip,
            "business_pass": business_pass,
            "business_strong": business_strong,
            "timing_uptrend": uptrend,
            "timing_base": base,
            "timing_correction": correction,
            "timing_downtrend": downtrend,
            "timing_stretched": stretched,
            "timing_stretched_hard": stretched_hard,
            "timing_stretch_zone": stretch_zone,
            "rs_laggard": rs_laggard,
            "valuation_extreme": valuation_extreme,
            "valuation_zone": valuation_zone,
            "earnings_window": earnings_window,
        },
        "gate_labels": {
            "data_quality": "Pass" if dq_pass else "Fail",
            "business": "Strong" if business_strong else ("Pass" if business_pass else "Fail"),
            "uptrend": "Yes" if uptrend else "No",
            "stretched": "Yes" if stretched else ("Approaching" if stretch_zone else "No"),
            "expensive": "Yes" if valuation_extreme else ("Approaching" if valuation_zone else "No"),
            "earnings_window": "Yes" if earnings_window else "No",
        },
        "flags": flags,
        "emphasize": emphasize,
        "ignore": ignore,
        "thresholds": {
            "dq_gate": DQ_GATE,
            "quality_skip": QUALITY_SKIP,
            "quality_pass": QUALITY_PASS,
            "quality_strong": QUALITY_STRONG,
            "rsi_stretch": MOMENTUM_STRETCH_RSI,
            "rsi_stretch_hard": MOMENTUM_STRETCH_RSI_HARD,
            "ma20_stretch": MA20_STRETCH,
            "valuation_extreme": VALUATION_EXTREME,
        },
    }


ENTRY_STANCES = frozenset({"CANDIDATE"})


def stance_allows_entry(stance: str) -> bool:
    """Backtest and dashboard may open only on CANDIDATE. Everything else is research."""
    return stance in ENTRY_STANCES
