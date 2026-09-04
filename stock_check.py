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

# เกตที่เป็นสัญญาณลบเมื่อเป็น True
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


@lru_cache(maxsize=64)
def _scan_cached(ticker: str, bucket: int) -> Dict[str, Any]:
    from engine import run_scan

    return run_scan(ticker)


def fetch_stock_check(ticker: str) -> Dict[str, Any]:
    tk = (ticker or "").strip().upper()
    if not tk or len(tk) > 16:
        return {"ok": False, "error": "ticker ว่างหรือยาวเกิน"}
    # ถัง 5 นาที กันยิง Yahoo ซ้ำตอนกดรีเฟรชหน้า
    bucket = int(time.time() // 300)
    t0 = time.time()
    try:
        raw = _scan_cached(tk, bucket)
    except Exception as exc:
        log.exception("stock check failed ticker=%s", tk)
        return {"ok": False, "ticker": tk, "error": "ตรวจสภาพไม่สำเร็จ", "detail": str(exc)[:180]}

    if not isinstance(raw, dict):
        return {"ok": False, "ticker": tk, "error": "ผลตรวจใช้ไม่ได้"}
    if raw.get("ok") is False:
        return {
            "ok": False,
            "ticker": tk,
            "error": "ดึงข้อมูลไม่ครบ ตรวจสภาพไม่ได้",
            "headline": HEADLINE["INSUFFICIENT"],
            "tone": "info",
        }

    dec = raw.get("decision") or {}
    stance = str(dec.get("stance") or "INSUFFICIENT")
    cards = raw.get("scorecards") or {}
    price = raw.get("price") or {}

    def _card(v):
        if isinstance(v, dict):
            return _num(v.get("score"), 0)
        n = _num(v, 1)
        if n is None:
            return v
        if abs(n - int(n)) < 1e-9:
            return int(n)
        return n
    levels = raw.get("levels") or {}
    gates_raw = dec.get("gates") or {}
    gates = []
    for key, label in GATE_LABEL.items():
        if key not in gates_raw:
            continue
        flag = bool(gates_raw.get(key))
        bad = (key in NEGATIVE_WHEN_TRUE and flag) or (key not in NEGATIVE_WHEN_TRUE and not flag and key in {"data_quality_pass", "business_pass"})
        gates.append({"key": key, "label": label, "on": flag, "warn": bad})

    took = round(time.time() - t0, 2)
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


def clear_cache() -> None:
    _scan_cached.cache_clear()
