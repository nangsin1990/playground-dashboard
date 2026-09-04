"""ตรวจสภาพตอนนี้ — ห่อเครื่องกรองรายตัวให้หน้า Stock ใช้

ไม่โชว์ชื่อชุดต้นทางใน UI
ดึงทีละ ticker เท่านั้น ไม่กวาดทั้งจักรวาล
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Dict, Optional

log = logging.getLogger("playground.stock_check")

_MWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mws")
if _MWS_DIR not in sys.path:
    sys.path.insert(0, _MWS_DIR)

HEADLINE = {
    "CANDIDATE": "Candidate",
    "WAIT_PULLBACK": "Wait pullback",
    "WATCH_BASE": "Watch base",
    "WATCH": "Watch",
    "WATCH_EXPENSIVE": "Watch · expensive",
    "PRICE_ONLY": "Price only",
    "SKIP": "Skip",
    "AVOID_LONG": "Avoid long",
    "WATCH_CORRECTION": "Watch correction",
    "INSUFFICIENT": "Insufficient data",
}

TONE = {
    "CANDIDATE": "ok",
    "WAIT_PULLBACK": "wait",
    "WATCH_BASE": "wait",
    "WATCH": "wait",
    "WATCH_EXPENSIVE": "wait",
    "PRICE_ONLY": "info",
    "SKIP": "bad",
    "AVOID_LONG": "bad",
    "WATCH_CORRECTION": "wait",
    "INSUFFICIENT": "info",
}

GATE_LABEL = {
    "data_quality_pass": "Data quality",
    "regime_risk_off": "Risk-off",
    "business_skip": "Quality fail",
    "business_pass": "Quality pass",
    "business_strong": "Quality strong",
    "timing_uptrend": "Uptrend",
    "timing_base": "Base",
    "timing_downtrend": "Downtrend",
    "timing_stretched": "Extended",
    "rs_laggard": "RS laggard",
    "earnings_window": "Earnings window",
}

NEGATIVE_WHEN_TRUE = {
    "regime_risk_off",
    "business_skip",
    "timing_downtrend",
    "timing_stretched",
    "rs_laggard",
    "earnings_window",
}


def _num(v: Any, nd: int = 1) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return round(x, nd)
    except (TypeError, ValueError):
        return None


def _card(v: Any):
    if isinstance(v, dict):
        return _num(v.get("score"), 0)
    n = _num(v, 1)
    if n is None:
        return None
    if abs(n - int(n)) < 1e-9:
        return int(n)
    return n


_SCAN_MEM: Dict[tuple, Dict[str, Any]] = {}


def _hollow(raw: Any) -> bool:
    if not isinstance(raw, dict) or raw.get("ok") is False:
        return True
    cards = raw.get("scorecards") or {}
    q = cards.get("quality")
    v = cards.get("valuation")
    qv = str(cards.get("quality_verdict") or "")
    vv = str(cards.get("valuation_verdict") or "")
    return (q in (0, None) and v in (0, None)) or ("Unknown" in qv and "Unknown" in vv)


def _scan_cached(ticker: str, bucket: int) -> Dict[str, Any]:
    from engine import run_scan

    key = (ticker, bucket)
    hit = _SCAN_MEM.get(key)
    if hit is not None:
        return hit
    raw = run_scan(ticker)
    if not _hollow(raw):
        _SCAN_MEM[key] = raw
        extra = [k for k in list(_SCAN_MEM) if k[1] < bucket - 2]
        for k in extra:
            _SCAN_MEM.pop(k, None)
    return raw


def _pack_from_scan(raw: Dict[str, Any], tk: str, took: float) -> Dict[str, Any]:
    dec = raw.get("decision") or {}
    stance = str(dec.get("stance") or "INSUFFICIENT")
    cards = raw.get("scorecards") or {}
    price = raw.get("price") or {}
    levels = raw.get("levels") or {}
    gates_raw = dec.get("gates") or {}
    gates = []
    for key, label in GATE_LABEL.items():
        if key not in gates_raw:
            continue
        flag = bool(gates_raw.get(key))
        bad = (key in NEGATIVE_WHEN_TRUE and flag) or (
            key not in NEGATIVE_WHEN_TRUE and not flag and key in {"data_quality_pass", "business_pass"}
        )
        gates.append({"key": key, "label": label, "on": flag, "warn": bad})
    return {
        "ok": True,
        "ticker": raw.get("ticker") or tk,
        "name": (raw.get("meta") or {}).get("name"),
        "headline": HEADLINE.get(stance, stance),
        "tone": TONE.get(stance, "info"),
        "summary": dec.get("summary") or "",
        "as_of": price.get("as_of"),
        "scores": {
            "Quality": None if "Unknown" in str(cards.get("quality_verdict") or "") else _card(cards.get("quality")),
            "Momentum": _card(cards.get("momentum")),
            "Valuation": None if "Unknown" in str(cards.get("valuation_verdict") or "") else _card(cards.get("valuation")),
            "Risk": _card(cards.get("risk")),
            "Data": _card(cards.get("data_quality")),
        },
        "labels": {
            "Quality": cards.get("business_label"),
            "Price": cards.get("price_label"),
            "Trend": cards.get("momentum_tech"),
            "RS": cards.get("momentum_rs"),
        },
        "gates": gates,
        "use": dec.get("emphasize") or [],
        "ignore": dec.get("ignore") or [],
        "flags": dec.get("flags") or [],
        "price": {
            "last": _num(price.get("last"), 2),
            "live": _num(price.get("live_quote"), 2),
            "ma20": _num(price.get("ma20"), 2),
            "ma50": _num(price.get("ma50"), 2),
            "ma200": _num(price.get("ma200"), 2),
            "rsi": _num(price.get("rsi14"), 1),
        },
        "levels": {
            "stop": _num(levels.get("trade_stop"), 2),
            "stop_reason": levels.get("trade_stop_reason"),
            "breakout": _num(levels.get("breakout"), 2),
            "invalidation": _num(levels.get("thesis_invalidation"), 2),
        },
        "note": raw.get("disclaimer") or "Auto filter desk — not a buy order",
        "elapsed_sec": took,
    }


def _fallback_check(tk: str, reason: str) -> Dict[str, Any]:
    """สรุปย่อจากโมดูลหน้า Stock ถ้าใบเต็มดึงไม่ครบ"""
    tech: Dict[str, Any] = {}
    fu: Dict[str, Any] = {}
    try:
        import technical_analysis as pta

        tech = pta.fetch_technicals(ticker=tk) or {}
    except Exception as exc:
        log.warning("fallback technicals failed %s: %s", tk, exc)
    try:
        import fundamentals as pfu

        fu = pfu.fetch_fundamentals(ticker=tk) or {}
    except Exception as exc:
        log.warning("fallback fundamentals failed %s: %s", tk, exc)

    rsi = _num(tech.get("rsi"), 1)
    price = _num(tech.get("price"), 2)
    if price is None:
        price = _num(fu.get("last_close"), 2)
    pe = _num(fu.get("pe") or fu.get("forward_pe"), 1)
    roe = _num(fu.get("roe"), 1)

    if price is None and rsi is None:
        return {
            "ok": False,
            "ticker": tk,
            "error": "Cannot complete check",
            "detail": reason,
            "headline": HEADLINE["INSUFFICIENT"],
            "tone": "info",
            "summary": "Price/RSI not enough to score.",
        }

    if rsi is not None and rsi >= 72:
        stance = "WAIT_PULLBACK"
        summary = "RSI stretched — wait pullback."
    elif rsi is not None and rsi <= 30:
        stance = "WATCH"
        summary = "Momentum soft — watch if price holds."
    elif pe is not None and pe >= 45:
        stance = "WATCH_EXPENSIVE"
        summary = "Valuation rich vs earnings — watch, not add."
    elif roe is not None and roe < 8:
        stance = "SKIP"
        summary = "ROE weak — skip."
    else:
        stance = "WATCH"
        summary = "Lite check from this page — not the full scorecard."

    scores = {
        "Quality": None if roe is None else min(90, max(20, int(roe * 1.2))),
        "Momentum": None if rsi is None else int(max(10, min(90, rsi))),
        "Valuation": None if pe is None else int(max(10, min(90, 80 - (pe - 15)))),
        "Risk": None,
        "Data": 60 if tech.get("ok") or fu.get("ok") else 30,
    }
    return {
        "ok": True,
        "ticker": tk,
        "name": fu.get("name"),
        "headline": HEADLINE.get(stance, stance),
        "tone": TONE.get(stance, "info"),
        "summary": summary,
        "scores": scores,
        "labels": {
            "Quality": None if roe is None else f"ROE {roe}%",
            "Price": None if pe is None else f"P/E {pe}",
            "Trend": None if rsi is None else f"RSI {rsi}",
        },
        "gates": [],
        "use": ["Use the tabs below for chart and fundamentals", "This is a lite check"],
        "ignore": ["Do not treat the score as a buy order"],
        "flags": [reason] if reason else [],
        "price": {"last": price, "rsi": rsi},
        "levels": {},
        "note": "Lite check — full scorecard incomplete",
        "detail": reason,
    }


def fetch_stock_check(ticker: str) -> Dict[str, Any]:
    tk = (ticker or "").strip().upper()
    if not tk or len(tk) > 16:
        return {"ok": False, "ticker": tk, "error": "ticker missing or too long", "headline": HEADLINE["INSUFFICIENT"], "tone": "info"}
    bucket = int(time.time() // 300)
    t0 = time.time()
    raw = None
    reason = ""
    try:
        raw = _scan_cached(tk, bucket)
    except Exception as exc:
        log.exception("stock check engine failed ticker=%s", tk)
        reason = str(exc)[:180]
        raw = None

    if isinstance(raw, dict) and raw.get("ok") is not False and (raw.get("decision") or raw.get("scorecards")):
        try:
            return _pack_from_scan(raw, tk, round(time.time() - t0, 2))
        except Exception as exc:
            log.exception("pack scan failed ticker=%s", tk)
            reason = reason or str(exc)[:180]

    if isinstance(raw, dict) and raw.get("ok") is False:
        reason = reason or str(raw.get("error") or "fetch_failed")

    return _fallback_check(tk, reason or "full scorecard incomplete")


def clear_cache() -> None:
    _SCAN_MEM.clear()
