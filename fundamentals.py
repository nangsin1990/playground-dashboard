"""fundamentals.py — P/E, ROE, EPS, Market Cap จาก yfinance (ฟรี)

ดึงทีละตัวแล้ว cache 24 ชม. ไม่ยิง FMP/Twelve Data
ค่าอาจว่างถ้า Yahoo ไม่มีข้อมูลตัวนั้น
"""
from __future__ import annotations

import logging
import math
from datetime import datetime

import yfinance as yf

from cache_utils import ttl_cache
from constants import CACHE_TTL_FUND

log = logging.getLogger("playground.fundamentals")


def _num(v):
    try:
        if v is None:
            return None
        x = float(v)
        if not math.isfinite(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _roe_pct(v):
    x = _num(v)
    if x is None:
        return None
    if abs(x) <= 2:
        x *= 100
    return round(x, 1)


def _fmt_mcap(v):
    x = _num(v)
    if x is None:
        return None
    abs_x = abs(x)
    if abs_x >= 1e12:
        return round(x / 1e12, 2)
    if abs_x >= 1e9:
        return round(x / 1e9, 2)
    if abs_x >= 1e6:
        return round(x / 1e6, 2)
    return round(x, 0)


def _mcap_unit(v):
    x = _num(v)
    if x is None:
        return None
    abs_x = abs(x)
    if abs_x >= 1e12:
        return "T"
    if abs_x >= 1e9:
        return "B"
    if abs_x >= 1e6:
        return "M"
    return ""


@ttl_cache(CACHE_TTL_FUND)
def fetch_fundamentals(ticker: str) -> dict:
    raw = (ticker or "").strip()
    if not raw:
        return {"ok": False, "ticker": raw, "error": "no ticker"}
    try:
        t = yf.Ticker(raw)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        fast = {}
        try:
            fast = dict(t.fast_info or {})
        except Exception:
            fast = {}

        pe = _num(info.get("trailingPE")) or _num(info.get("forwardPE"))
        roe = _roe_pct(info.get("returnOnEquity"))
        eps = _num(info.get("trailingEps")) or _num(info.get("forwardEps"))
        mcap = _num(info.get("marketCap")) or _num(fast.get("market_cap")) or _num(fast.get("marketCap"))
        fwd_pe = _num(info.get("forwardPE"))
        dy = _num(info.get("dividendYield"))
        if dy is not None and dy <= 1:
            dy = round(dy * 100, 2)
        elif dy is not None:
            dy = round(dy, 2)

        return {
            "ok": True,
            "ticker": raw,
            "pe": None if pe is None else round(pe, 1),
            "forward_pe": None if fwd_pe is None else round(fwd_pe, 1),
            "roe": roe,
            "eps": None if eps is None else round(eps, 2),
            "market_cap": mcap,
            "mcap": _fmt_mcap(mcap),
            "mcap_unit": _mcap_unit(mcap),
            "div_yield": dy,
            "currency": info.get("currency") or fast.get("currency"),
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        }
    except Exception as e:
        log.debug("fundamentals failed %s: %s", raw, e)
        return {"ok": False, "ticker": raw, "error": str(e)}


def attach_fundamentals(rows: list[dict], limit_fetch: int = 80) -> list[dict]:
    """แปะพื้นฐานลงแถวที่มีอยู่ ดึงใหม่ไม่เกิน limit_fetch ต่อคำขอ."""
    if not rows:
        return rows
    seen = 0
    for r in rows:
        if seen >= limit_fetch:
            break
        t = r.get("full_ticker") or r.get("ticker") or r.get("symbol")
        if not t:
            continue
        pack = fetch_fundamentals(str(t))
        seen += 1
        if not pack.get("ok"):
            continue
        r["pe"] = pack.get("pe")
        r["forward_pe"] = pack.get("forward_pe")
        r["roe"] = pack.get("roe")
        r["eps"] = pack.get("eps")
        r["market_cap"] = pack.get("market_cap")
        r["mcap"] = pack.get("mcap")
        r["mcap_unit"] = pack.get("mcap_unit")
        r["div_yield"] = pack.get("div_yield")
    return rows
