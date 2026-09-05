"""stock_profile.py — เป้านักวิเคราะห์ ผู้ถือหุ้น ข่าว จาก yfinance ฟรี

ดึงทีละตัวตอนเปิดแท็บหน้าหุ้น ไม่วอร์มทั้งลิสต์
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from cache_utils import ttl_cache
from constants import CACHE_TTL_CALENDAR, CACHE_TTL_FUND

log = logging.getLogger("playground.stock_profile")


def _num(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        if hasattr(v, "item"):
            v = v.item()
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _txt(v, limit: int = 240) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    if len(s) > limit:
        return s[: limit - 1] + "…"
    return s


def _ts(v) -> str | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)) and v > 1e9:
            return datetime.fromtimestamp(int(v), tz=timezone.utc).strftime("%Y-%m-%d")
        ts = pd.Timestamp(v)
        if pd.isna(ts):
            return None
        return str(ts)[:10]
    except Exception:
        s = str(v).strip()
        return s[:10] if s else None


def _frame(obj) -> pd.DataFrame | None:
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        return None if obj.empty else obj
    try:
        df = pd.DataFrame(obj)
        return None if df.empty else df
    except Exception:
        return None


def _safe_attr(t, name: str):
    try:
        return getattr(t, name)
    except Exception as e:
        log.debug("%s failed: %s", name, e)
        return None


@ttl_cache(CACHE_TTL_FUND)
def fetch_analyst(ticker: str) -> dict:
    raw = (ticker or "").strip()
    if not raw:
        return {"ok": False, "ticker": raw, "error": "no ticker"}
    try:
        t = yf.Ticker(raw)
        targets: dict[str, Any] = {}
        src = _safe_attr(t, "analyst_price_targets")
        if isinstance(src, dict):
            targets = {
                "current": _num(src.get("current")),
                "low": _num(src.get("low")),
                "mean": _num(src.get("mean")),
                "median": _num(src.get("median")),
                "high": _num(src.get("high")),
            }
        elif src is not None:
            bag = getattr(src, "to_dict", lambda: {})()
            if isinstance(bag, dict):
                flat = bag.get("Price Target") if "Price Target" in bag else bag
                if isinstance(flat, dict):
                    targets = {str(k).lower(): _num(v) for k, v in flat.items()}

        recs = []
        rec_df = _frame(_safe_attr(t, "recommendations"))
        if rec_df is None:
            rec_df = _frame(_safe_attr(t, "recommendations_summary"))
        if rec_df is not None:
            tail = rec_df.tail(8).reset_index()
            for _, row in tail.iterrows():
                recs.append({
                    "date": _ts(row.get("period") or row.get("Date") or row.get("index")),
                    "firm": _txt(row.get("Firm") or row.get("firm"), 40),
                    "grade": _txt(row.get("To Grade") or row.get("toGrade") or row.get("strongBuy") or row.get("period"), 32),
                    "action": _txt(row.get("Action") or row.get("action"), 24),
                })
            recs = list(reversed(recs))

        estimates = []
        est = _frame(_safe_attr(t, "earnings_estimate"))
        if est is not None:
            use = est.reset_index()
            for _, row in use.head(6).iterrows():
                estimates.append({
                    "period": _txt(row.get("period") or row.get("index") or row.iloc[0], 20),
                    "avg": _num(row.get("avg") or row.get("Avg. Estimate") or row.get("earningsAvg")),
                    "low": _num(row.get("low") or row.get("Low. Estimate")),
                    "high": _num(row.get("high") or row.get("High. Estimate")),
                    "num": _num(row.get("numberOfAnalysts") or row.get("No. of Analysts")),
                })

        rec_key = None
        try:
            info = t.get_info() or {}
        except Exception:
            try:
                info = t.info or {}
            except Exception:
                info = {}
        rec_key = _txt(info.get("recommendationKey") or info.get("recommendationMean"), 24)
        rec_mean = _num(info.get("recommendationMean"))
        target_mean_info = _num(info.get("targetMeanPrice"))
        if targets.get("mean") is None and target_mean_info is not None:
            targets["mean"] = target_mean_info
        if targets.get("low") is None:
            targets["low"] = _num(info.get("targetLowPrice"))
        if targets.get("high") is None:
            targets["high"] = _num(info.get("targetHighPrice"))
        if targets.get("current") is None:
            targets["current"] = _num(info.get("currentPrice") or info.get("regularMarketPrice"))

        ok = any(targets.get(k) is not None for k in ("mean", "low", "high", "current")) or bool(recs) or bool(estimates)
        return {
            "ok": ok,
            "ticker": raw,
            "targets": targets,
            "recommendation": rec_key,
            "recommendation_mean": rec_mean,
            "recommendations": recs,
            "estimates": estimates,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "note": None if ok else "Yahoo ไม่มีเป้านักวิเคราะห์ตัวนี้",
        }
    except Exception as e:
        log.debug("analyst failed %s: %s", raw, e)
        return {"ok": False, "ticker": raw, "error": str(e)[:180]}


@ttl_cache(CACHE_TTL_FUND)
def fetch_holders(ticker: str) -> dict:
    raw = (ticker or "").strip()
    if not raw:
        return {"ok": False, "ticker": raw, "error": "no ticker"}
    try:
        t = yf.Ticker(raw)
        major = []
        maj = _safe_attr(t, "major_holders")
        maj_df = _frame(maj)
        if maj_df is not None:
            use = maj_df.reset_index()
            cols = list(use.columns)
            for _, row in use.head(8).iterrows():
                a = _txt(row.iloc[0] if len(cols) else None, 48)
                b = _txt(row.iloc[1] if len(cols) > 1 else None, 64)
                if a or b:
                    major.append({"label": b or a, "value": a if b else None})

        inst = []
        inst_df = _frame(_safe_attr(t, "institutional_holders"))
        if inst_df is not None:
            for _, row in inst_df.head(10).iterrows():
                inst.append({
                    "holder": _txt(row.get("Holder") or row.get("holder"), 48),
                    "shares": _num(row.get("Shares") or row.get("shares")),
                    "pct": _num(row.get("% Out") or row.get("pctHeld") or row.get("pct")),
                    "date": _ts(row.get("Date Reported") or row.get("dateReported")),
                    "value": _num(row.get("Value") or row.get("value")),
                })

        insider = []
        inn_df = _frame(_safe_attr(t, "insider_transactions"))
        if inn_df is None:
            inn_df = _frame(_safe_attr(t, "insider_purchases"))
        if inn_df is not None:
            for _, row in inn_df.head(10).iterrows():
                insider.append({
                    "insider": _txt(row.get("Insider") or row.get("insider") or row.get("Start Date"), 40),
                    "text": _txt(row.get("Text") or row.get("text") or row.get("Transaction"), 80),
                    "shares": _num(row.get("Shares") or row.get("shares") or row.get("Shares")),
                    "value": _num(row.get("Value") or row.get("value")),
                    "date": _ts(row.get("Start Date") or row.get("date") or row.get("Filing Date")),
                })

        ok = bool(major or inst or insider)
        return {
            "ok": ok,
            "ticker": raw,
            "major": major,
            "institutional": inst,
            "insider": insider,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "note": None if ok else "Yahoo ไม่มีข้อมูลผู้ถือหุ้นตัวนี้",
        }
    except Exception as e:
        log.debug("holders failed %s: %s", raw, e)
        return {"ok": False, "ticker": raw, "error": str(e)[:180]}


@ttl_cache(CACHE_TTL_CALENDAR)
def fetch_news(ticker: str) -> dict:
    raw = (ticker or "").strip()
    if not raw:
        return {"ok": False, "ticker": raw, "error": "no ticker"}
    try:
        t = yf.Ticker(raw)
        raw_news = _safe_attr(t, "news") or []
        items = []
        for item in list(raw_news)[:8]:
            if not isinstance(item, dict):
                continue
            content = item.get("content") if isinstance(item.get("content"), dict) else item
            title = _txt(content.get("title") or item.get("title"), 140)
            if not title:
                continue
            link = None
            click = content.get("clickThroughUrl") or content.get("canonicalUrl") or item.get("link")
            if isinstance(click, dict):
                link = click.get("url")
            elif isinstance(click, str):
                link = click
            pub = content.get("provider") or item.get("publisher") or {}
            if isinstance(pub, dict):
                pub = pub.get("displayName") or pub.get("name")
            ts = content.get("pubDate") or item.get("providerPublishTime") or content.get("displayTime")
            items.append({
                "title": title,
                "publisher": _txt(pub, 40),
                "link": _txt(link, 300),
                "date": _ts(ts),
            })
        return {
            "ok": bool(items),
            "ticker": raw,
            "items": items,
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "note": None if items else "Yahoo ไม่มีข่าวตัวนี้",
        }
    except Exception as e:
        log.debug("news failed %s: %s", raw, e)
        return {"ok": False, "ticker": raw, "error": str(e)[:180]}
