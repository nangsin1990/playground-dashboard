# -*- coding: utf-8 -*-
"""Dashboard payload builder. JSON-stable types only. MWS v3.8.0"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

import logging

try:
    from .config import REVISION, REVISION_TAG, SERIES_BARS
    from .decision import evaluate_decision
    from .levels import pick_levels
    from .scoring import earnings_growth_safe
    from .utils import normalize_debt_to_equity
except ImportError:
    from config import REVISION, REVISION_TAG, SERIES_BARS
    from decision import evaluate_decision
    from levels import pick_levels
    from scoring import earnings_growth_safe
    from utils import normalize_debt_to_equity

log = logging.getLogger("mws.payload")


def json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        if np.isnan(v) or np.isinf(v):
            return None
        return float(v)
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [json_safe(x) for x in v]
    if isinstance(v, pd.Timestamp):
        return str(v.date()) if not pd.isna(v) else None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    return v


def _series(hist: pd.DataFrame, n: int = SERIES_BARS) -> List[Dict[str, Any]]:
    if hist is None or getattr(hist, "empty", True):
        return []
    tail = hist.tail(n)
    out = []
    for idx, row in tail.iterrows():
        try:
            t = pd.Timestamp(idx)
            t_s = str(t.date())
        except Exception:
            t_s = str(idx)[:10]
        out.append(
            {
                "t": t_s,
                "o": json_safe(row.get("Open")),
                "h": json_safe(row.get("High")),
                "l": json_safe(row.get("Low")),
                "c": json_safe(row.get("Close")),
                "v": json_safe(row.get("Volume")),
            }
        )
    return out


def build_scan_payload(scanner) -> Dict[str, Any]:
    if not getattr(scanner, "scorecards", None):
        scanner.score_sections()
    cards = scanner.scorecards
    fund = getattr(scanner, "_fund", {}) or {}
    mas = getattr(scanner, "_mas", {}) or {}
    tech = getattr(scanner, "_tech", {}) or {}
    rs = getattr(scanner, "_rs", {}) or {}
    extra = getattr(scanner, "extra", {}) or {}
    price = getattr(scanner, "_price", None)
    live_quote = scanner.get_live_quote() if hasattr(scanner, "get_live_quote") else None
    as_of = scanner.last_bar_date() if hasattr(scanner, "last_bar_date") else None
    support, resistance = scanner.swing_levels()
    dq = float(extra.get("data_quality_overall") or 0)
    q = int(cards["quality"]["score"])
    m = int(cards["momentum"]["score"])
    v = int(cards["valuation"]["score"])
    rk = int(cards["risk"]["score"])
    comp = cards.get("composite") or {}
    labels = cards.get("labels") or {}
    lv = pick_levels(price, mas, tech.get("ATR"), support, resistance)

    decision = evaluate_decision(
        dq=dq,
        quality=q,
        momentum=m,
        valuation=v,
        risk=rk,
        tech_verdict=cards["momentum"].get("tech_verdict", ""),
        stage=cards["momentum"].get("stage", ""),
        rs_verdict=cards["momentum"].get("rs_verdict", ""),
        avg_rs=cards["momentum"].get("avg_rs"),
        price=price,
        mas=mas,
        rsi=_to_float(tech.get("RSI")),
        regime=_resolve_regime(scanner),
        days_to_earnings=extra.get("days_to_earnings"),
        fcf=_to_float(fund.get("free_cashflow")),
        de_norm=_to_float(fund.get("debt_to_equity_norm") or normalize_debt_to_equity(fund.get("debt_to_equity"), fund.get("total_cash"), fund.get("total_debt"))),
        quality_available=bool(cards["quality"].get("available", True)),
        valuation_available=bool(cards["valuation"].get("available", True)),
    )

    info = scanner.info or {}
    payload = {
        "schema": "mws.scan.v1",
        "revision": REVISION,
        "revision_tag": REVISION_TAG,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticker": scanner.ticker_symbol,
        "meta": {
            "name": info.get("longName") or info.get("shortName") or scanner.ticker_symbol,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "currency": info.get("currency"),
            "exchange": info.get("exchange"),
            "market_cap": json_safe(info.get("marketCap")),
        },
        "decision": decision,
        "scorecards": {
            "quality": q,
            "momentum": m,
            "valuation": v,
            "risk": rk,
            "data_quality": round(dq, 1),
            "composite_research_only": json_safe(comp.get("score")),
            "quality_verdict": cards["quality"].get("verdict"),
            "momentum_rs": cards["momentum"].get("rs_verdict"),
            "momentum_tech": cards["momentum"].get("tech_verdict"),
            "stage": cards["momentum"].get("stage"),
            "valuation_verdict": cards["valuation"].get("verdict"),
            "risk_verdict": cards["risk"].get("verdict"),
            "risk_flags": cards["risk"].get("flags") or [],
            "business_label": labels.get("business"),
            "price_label": labels.get("price"),
            "profile": cards.get("profile"),
        },
        "market": {
            "regime": getattr(scanner, "_regime", None) or _resolve_regime(scanner) or None,
            "regime_note": getattr(scanner, "_regime_note", None),
            "sector_etf": scanner.sector_etf,
            "sector": getattr(scanner, "_sector_detail", {}) or {},
            "rs_vs_spy": rs,
        },
        "price": {
            "as_of": as_of,
            "last": json_safe(price),
            "live_quote": json_safe(live_quote),
            "session_note": "last/MAs/RSI come from last complete bar; live_quote may be newer",
            "ma20": json_safe(mas.get("MA20")),
            "ma50": json_safe(mas.get("MA50")),
            "ma200": json_safe(mas.get("MA200")),
            "rsi14": json_safe(tech.get("RSI")),
            "atr14": json_safe(tech.get("ATR")),
            "macd_hist": json_safe(tech.get("MACD_hist")),
            "volume": json_safe(tech.get("last_vol")),
            "volume_avg20": json_safe(tech.get("vol_avg_20")),
            "swing_low_60": json_safe(support),
            "swing_high_60": json_safe(resistance),
            "high_52w": json_safe(extra.get("high_52w")),
            "low_52w": json_safe(extra.get("low_52w")),
        },
        "levels": {
            "use": "trade_stop",
            "trade_stop": json_safe(lv.get("trade_stop")),
            "trade_stop_reason": lv.get("trade_stop_reason"),
            "breakout": json_safe(lv.get("breakout")),
            "atr_stop": json_safe(lv.get("atr_stop")),
            "swing_stop": json_safe(lv.get("swing_stop")),
            "ma50": json_safe(lv.get("ma50")),
            "thesis_invalidation": json_safe(lv.get("thesis_invalidation")),
            "invalidation": json_safe(lv.get("thesis_invalidation")),
            "target_note": lv.get("target_note"),
            "ignore": lv.get("ignore") or [],
            "note": lv.get("note"),
        },
        "fundamentals": {
            "revenue_growth": json_safe(fund.get("revenue_growth")),
            "eps_growth_used": json_safe(earnings_growth_safe(fund)),
            "roic_proxy": json_safe(cards["quality"].get("roic_proxy")),
            "roe": json_safe(fund.get("roe")),
            "roa": json_safe(fund.get("roa")),
            "gross_margin": json_safe(fund.get("gross_margins")),
            "operating_margin": json_safe(fund.get("operating_margins")),
            "profit_margin": json_safe(fund.get("profit_margins")),
            "fcf": json_safe(fund.get("free_cashflow")),
            "ocf": json_safe(fund.get("operating_cashflow")),
            "fcf_conversion": json_safe(fund.get("fcf_conversion")),
            "fcf_yield": json_safe(fund.get("fcf_yield")),
            "debt_to_equity_raw": json_safe(fund.get("debt_to_equity")),
            "debt_to_equity_pct": json_safe(fund.get("debt_to_equity_norm")),
            "current_ratio": json_safe(fund.get("current_ratio")),
            "cash": json_safe(fund.get("total_cash")),
            "debt": json_safe(fund.get("total_debt")),
            "forward_pe": json_safe(fund.get("forward_pe")),
            "trailing_pe": json_safe(fund.get("trailing_pe")),
            "peg": json_safe(fund.get("peg")),
            "ev_ebitda": json_safe(fund.get("ev_ebitda")),
            "price_to_sales": json_safe(fund.get("price_to_sales")),
        },
        "event": {
            "earnings_date": extra.get("earnings_date"),
            "days_to_earnings": extra.get("days_to_earnings"),
            "short_pct_float": json_safe(extra.get("short_pct_float_display")),
            "short_ratio": json_safe(extra.get("short_ratio")),
            "analyst_mean": None,
            "analyst_upside_pct": None,
            "analyst_n": None,
            "analyst_note": "ตัดออกจากรายงาน ไม่ใช้ตัดสิน",
        },
        "news": {
            "available": bool(scanner.news_available),
            "catalyst_score": int(scanner.catalyst_score),
            "items": [
                {
                    "date": n.get("date"),
                    "sentiment": n.get("sentiment"),
                    "title": n.get("title"),
                    "provider": n.get("provider"),
                }
                for n in (scanner.news_items or [])[:8]
            ],
            "note": "นับคำภาษาอังกฤษ น้ำหนักต่ำ",
        },
        "peers": scanner.peers_data or [],
        "data_quality": {
            "overall": round(dq, 1),
            "components": extra.get("data_quality") or {},
            "sources": extra.get("data_sources") or {},
        },
        "series": _series(scanner.hist),
        "disclaimer": (
            "ใบตรวจสภาพหุ้นอัตโนมัติ ไม่ใช่คำสั่งซื้อ ไม่ใช่คำแนะนำการลงทุน "
            "คะแนนรวมเป็นป้ายวิจัย จุดเข้า-ตัดใช้ราคา"
        ),
    }
    return json_safe(payload)


def _resolve_regime(scanner) -> str:
    """Prefer cached _regime; if missing, compute once and warn.

    Empty regime makes regime_risk_off=False silently — that hid risk-off
    names from the DQ/decision path (BUG-02/03 amplifier).
    """
    cached = getattr(scanner, "_regime", None)
    if cached:
        return str(cached)
    if hasattr(scanner, "market_regime"):
        try:
            regime, note = scanner.market_regime()
            try:
                scanner._regime = regime
                scanner._regime_note = note
            except Exception:
                pass
            if regime:
                log.warning(
                    "scanner._regime was unset for %s; computed via market_regime() -> %s",
                    getattr(scanner, "ticker_symbol", "?"),
                    regime,
                )
                return str(regime)
        except Exception:
            log.warning(
                "scanner._regime unset and market_regime() failed for %s",
                getattr(scanner, "ticker_symbol", "?"),
            )
    else:
        log.warning(
            "scanner._regime unset for %s and no market_regime(); risk-off will not fire",
            getattr(scanner, "ticker_symbol", "?"),
        )
    return ""


def _to_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if np.isnan(x) or np.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def build_radar_payload(radar) -> Dict[str, Any]:
    m = radar.metrics or {}
    return json_safe(
        {
            "schema": "mws.radar.v1",
            "revision": REVISION,
            "revision_tag": REVISION_TAG,
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ticker": radar.ticker,
            "score": m.get("accumulation_score"),
            "percentile": radar.percentile,
            "verdict": radar.verdict() if m else None,
            "metrics": m,
            "universe": {
                t: {"score": mm.get("accumulation_score"), "resilience": mm.get("down_market_resilience")}
                for t, mm in (radar.universe_metrics or {}).items()
            },
            "note": "คะแนน phenotype ไม่ใช่ความน่าจะเป็นว่ามีคนเก็บของ",
        }
    )
