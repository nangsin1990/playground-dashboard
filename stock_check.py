"""ตรวจสภาพตอนนี้ — ห่อเครื่องกรองรายตัวให้หน้า Stock ใช้

ไม่โชว์ชื่อชุดต้นทางใน UI
ดึงทีละ ticker เท่านั้น ไม่กวาดทั้งจักรวาล
"""
from __future__ import annotations

import logging
import os
import sys
import time
from functools import lru_cache
from typing import Any, Dict, Optional

log = logging.getLogger("playground.stock_check")

_MWS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mws")
if _MWS_DIR not in sys.path:
    sys.path.insert(0, _MWS_DIR)

HEADLINE = {
    "CANDIDATE": "ผ่านเกณฑ์",
    "WAIT_PULLBACK": "รอพักฐาน",
    "WATCH_BASE": "ดูต่อ กำลังฐาน",
    "WATCH": "ดูต่อ",
    "WATCH_EXPENSIVE": "แพงไป",
    "PRICE_ONLY": "ดูได้แค่ราคา",
    "SKIP": "ข้ามไปก่อน",
    "AVOID_LONG": "ยังไม่แตะ",
    "WATCH_CORRECTION": "ดูต่อ กำลังปรับฐาน",
    "INSUFFICIENT": "ข้อมูลไม่พอ",
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
    "data_quality_pass": "ข้อมูลพอใช้",
    "regime_risk_off": "ตลาดปิดความเสี่ยง",
    "business_skip": "ธุรกิจยังไม่ผ่าน",
    "business_pass": "ธุรกิจรับได้",
    "business_strong": "ธุรกิจแข็ง",
    "timing_uptrend": "ราคาอยู่ในขาขึ้น",
    "timing_base": "กำลังสร้างฐาน",
    "timing_downtrend": "ราคายังเป็นขาลง",
    "timing_stretched": "ราคายืดจากค่าเฉลี่ย",
    "rs_laggard": "อ่อนกว่าตลาด",
    "earnings_window": "ใกล้ประกาศงบ",
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


@lru_cache(maxsize=64)
def _scan_cached(ticker: str, bucket: int) -> Dict[str, Any]:
    from engine import run_scan

    return run_scan(ticker)


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
            "ธุรกิจ": _card(cards.get("quality")),
            "จังหวะ": _card(cards.get("momentum")),
            "มูลค่า": _card(cards.get("valuation")),
            "ความเสี่ยง": _card(cards.get("risk")),
            "ข้อมูล": _card(cards.get("data_quality")),
        },
        "labels": {
            "ธุรกิจ": cards.get("business_label"),
            "ราคา": cards.get("price_label"),
            "เทคนิค": cards.get("momentum_tech"),
            "เทียบตลาด": cards.get("momentum_rs"),
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
            "ตัดขาดทุน": _num(levels.get("trade_stop"), 2),
            "เหตุผล": levels.get("trade_stop_reason"),
            "ทะลุขึ้น": _num(levels.get("breakout"), 2),
            "ถ้าธีมพัง": _num(levels.get("thesis_invalidation"), 2),
        },
        "note": raw.get("disclaimer") or "ใบตรวจสภาพอัตโนมัติ ไม่ใช่คำสั่งซื้อ",
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
            "error": "ยังสรุปไม่ได้",
            "detail": reason,
            "headline": HEADLINE["INSUFFICIENT"],
            "tone": "info",
            "summary": "ข้อมูลราคาไม่พอสำหรับสรุปตอนนี้",
        }

    if rsi is not None and rsi >= 72:
        stance = "WAIT_PULLBACK"
        summary = "RSI อยู่ในโซนร้อน รอพักฐานก่อน"
    elif rsi is not None and rsi <= 30:
        stance = "WATCH"
        summary = "โมเมนตัมอ่อน ดูต่อว่าราคายืนได้ไหม"
    elif pe is not None and pe >= 45:
        stance = "WATCH_EXPENSIVE"
        summary = "ราคาค่อนข้างแพงเมื่อเทียบกับกำไร ดูต่อได้แต่ยังไม่ใช่จังหวะเติม"
    elif roe is not None and roe < 8:
        stance = "SKIP"
        summary = "ผลตอบแทนผู้ถือหุ้นยังอ่อน ข้ามไปก่อน"
    else:
        stance = "WATCH"
        summary = "สรุปย่อจากกราฟและงบในหน้านี้ ยังไม่ใช่ใบเต็ม"

    scores = {
        "ธุรกิจ": None if roe is None else min(90, max(20, int(roe * 1.2))),
        "จังหวะ": None if rsi is None else int(max(10, min(90, rsi))),
        "มูลค่า": None if pe is None else int(max(10, min(90, 80 - (pe - 15)))),
        "ความเสี่ยง": None,
        "ข้อมูล": 60 if tech.get("ok") or fu.get("ok") else 30,
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
            "ธุรกิจ": None if roe is None else f"ROE {roe}%",
            "ราคา": None if pe is None else f"P/E {pe}",
            "เทคนิค": None if rsi is None else f"RSI {rsi}",
        },
        "gates": [],
        "use": ["ใช้กราฟและงบด้านล่างประกอบ", "ใบนี้เป็นสรุปย่อ"],
        "ignore": ["อย่าเพิ่งคะแนนรวมเป็นคำสั่งซื้อ"],
        "flags": [reason] if reason else [],
        "price": {"last": price, "rsi": rsi},
        "levels": {},
        "note": "สรุปย่อจากข้อมูลหน้า Stock เพราะใบเต็มดึงไม่ครบ",
        "detail": reason,
    }


def fetch_stock_check(ticker: str) -> Dict[str, Any]:
    tk = (ticker or "").strip().upper()
    if not tk or len(tk) > 16:
        return {"ok": False, "ticker": tk, "error": "ticker ว่างหรือยาวเกิน", "headline": HEADLINE["INSUFFICIENT"], "tone": "info"}
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

    return _fallback_check(tk, reason or "ใบเต็มดึงไม่ครบ")


def clear_cache() -> None:
    _scan_cached.cache_clear()
