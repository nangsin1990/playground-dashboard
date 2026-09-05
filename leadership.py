# FILE: leadership.py
# จัดจักรวาลตาม CAN SLIM (L) + Minervini Trend Template + Weinstein Stage
# บอร์ดนี้เป็นรายชื่อไปศึกษา ไม่ใช่คำสั่งซื้อขาย
# U/D volume และ Accum เป็นพร็อกซีวอลุ่ม ไม่ใช่โฮลดิ้งสถาบัน

from __future__ import annotations
from collections import defaultdict
from datetime import datetime
import logging
import time

import pandas as pd
import numpy as np

import pipeline
from cache_utils import ttl_cache
from constants import (
    LB_TREND_LOOKBACK, LB_ACCUM_LOOKBACK, LB_TIGHTNESS_WEEKS,
    LB_UD_RATIO_LOOKBACK, LB_VOL_WINDOW, LB_BREAKOUT_PROX, LB_ACCUM_MIN,
    LB_UD_MIN, LB_VOL_MIN, LB_TOP_N, CACHE_TTL_DATA,
    LB_RS_LEADER_MIN, LB_RS_ELITE, LB_RS_LAGGARD_MAX, LB_RS_FADE_MAX,
    LB_OFF_LOW_MIN, LB_OFF_HIGH_MAX, LB_TEMPLATE_MIN,
)
import data_engine as eng

log = logging.getLogger("playground.leadership")


def _get_leadership_data(mode: str, pack: dict | None = None) -> dict:
    pack = pack if pack is not None else pipeline.load_market_pack(mode)
    dash_data = pack.get("dash") or {}
    combined = pack.get("combined") or {}
    if not combined or not dash_data.get("ok"):
        return {"ok": False, "error": dash_data.get("error") or "No data from pipeline"}
    return {
        "ok": True,
        "pack": pack,
        "combined": combined,
        "ticker_meta": pack.get("ticker_meta") or {},
        "rs_now": dash_data.get("rs_now"),
        "rs_7": dash_data.get("rs_7"),
        "ticker_signal": dash_data.get("ticker_signal"),
        "total_universe": len(combined),
    }


def _gt(a, b) -> bool:
    try:
        if a is None or b is None or pd.isna(a) or pd.isna(b):
            return False
        return float(a) > float(b)
    except (TypeError, ValueError):
        return False


def _f(v, default=None):
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _classify_role(s: dict) -> str:
    """
    leader = ผ่านเกณฑ์ CAN SLIM L ขั้นต่ำ + โครง Stage 2 แบบย่อ
    laggard = RS อ่อนและโครงพัง (Stage 4 / เทรนด์แตก)
    watch = โซนเทา ไม่ยัดเข้าฝั่งใด
    คนละกองเสมอ
    """
    rs = s.get("rs")
    ls = int(s.get("ls") or 0)
    ws = int(s.get("ws") or 0)
    stage = int(s.get("stage") or 0)
    trend = int(s.get("trend_score") or 0)
    prox = float(s.get("prox_52w") or 0)
    off_low = float(s.get("off_low_pct") or 0)
    template = int(s.get("template_score") or 0)

    structure_ok = (
        trend >= 3
        and prox <= LB_OFF_HIGH_MAX
        and off_low >= LB_OFF_LOW_MIN
        and stage != 4
    )
    structure_broken = stage == 4 or trend <= 1

    if rs is None:
        if structure_broken and ws >= 55 and ls < 45:
            return "laggard"
        if structure_ok and template >= LB_TEMPLATE_MIN and ls >= 55 and ws < 40:
            return "leader"
        return "watch"

    leader_ok = (
        rs >= LB_RS_LEADER_MIN
        and structure_ok
        and template >= 6
        and ws < 45
        and ls >= 55
    )
    laggard_ok = (
        rs <= LB_RS_LAGGARD_MAX
        and structure_broken
        and ls < 50
        and ws >= 50
    )
    if stage == 4 and rs < LB_RS_LEADER_MIN and ls < 50 and ws >= 50:
        laggard_ok = True

    if leader_ok and laggard_ok:
        return "leader" if rs >= LB_RS_LEADER_MIN and structure_ok else "laggard"
    if leader_ok:
        return "leader"
    if laggard_ok:
        return "laggard"
    return "watch"


def _calc_trend_template(df: pd.DataFrame) -> dict:
    empty = {
        "trend_c1": False, "trend_c2": False, "trend_c3": False, "trend_c4": False,
        "trend_score": 0, "sma50": None, "sma150": None, "sma200": None,
        "sma200_rising": False, "ma_aligned": False,
    }
    if df is None or len(df) < 150:
        return empty
    last = df.iloc[-1]
    px = last.get("Close")
    sma50 = last.get("SMA50")
    sma150 = last.get("SMA150")
    sma200 = last.get("SMA200")
    if sma50 is None or pd.isna(sma50):
        sma50 = df["Close"].rolling(50, min_periods=30).mean().iloc[-1]
    if sma150 is None or pd.isna(sma150):
        sma150 = df["Close"].rolling(150, min_periods=50).mean().iloc[-1]
    if sma200 is None or pd.isna(sma200):
        if len(df) >= 200:
            sma200 = df["Close"].rolling(200, min_periods=100).mean().iloc[-1]
    c1 = _gt(px, sma50)
    c2 = _gt(px, sma150)
    c3 = _gt(px, sma200)
    sma200_s = df["SMA200"] if "SMA200" in df.columns else df["Close"].rolling(200, min_periods=100).mean()
    tail = sma200_s.tail(LB_TREND_LOOKBACK).dropna()
    c4 = bool(len(tail) > 1 and _gt(tail.iloc[-1], tail.iloc[0]))
    ma_aligned = _gt(sma50, sma150) and _gt(sma150, sma200)
    return {
        "trend_c1": bool(c1), "trend_c2": bool(c2), "trend_c3": bool(c3), "trend_c4": bool(c4),
        "trend_score": int(c1) + int(c2) + int(c3) + int(c4),
        "sma50": _f(sma50), "sma150": _f(sma150), "sma200": _f(sma200),
        "sma200_rising": bool(c4), "ma_aligned": bool(ma_aligned),
    }


def _weinstein_stage(px, sma150, sma200, sma200_rising) -> int:
    above_200 = _gt(px, sma200)
    above_150 = _gt(px, sma150)
    if above_200 and sma200_rising and above_150:
        return 2
    if (not above_200) and (not sma200_rising):
        return 4
    if above_200 and not sma200_rising:
        return 3
    return 1


def _calc_accumulation(df: pd.DataFrame) -> dict:
    if len(df) < LB_UD_RATIO_LOOKBACK:
        return {"ud_ratio": 1.0, "accum_score": 0.0}
    tail = df.tail(LB_UD_RATIO_LOOKBACK)
    change = tail["Close"].diff()
    up_vol = tail["Volume"][change > 0].sum()
    down_vol = tail["Volume"][change <= 0].sum()
    ud_ratio = up_vol / down_vol if down_vol > 0 else 5.0
    ad = ((df["Close"] - df["Low"]) - (df["High"] - df["Close"])) / (df["High"] - df["Low"]).replace(0, np.nan) * df["Volume"]
    ad = ad.fillna(0)
    ad_smooth = ad.ewm(span=LB_ACCUM_LOOKBACK, adjust=False).mean()
    vol_smooth = df["Volume"].ewm(span=LB_ACCUM_LOOKBACK, adjust=False).mean()
    accum_score = ad_smooth.iloc[-1] / vol_smooth.iloc[-1] if vol_smooth.iloc[-1] > 0 else 0.0
    return {"ud_ratio": round(float(ud_ratio), 2), "accum_score": round(float(accum_score), 3)}


def _calc_volatility(df: pd.DataFrame) -> dict:
    lookback = LB_TIGHTNESS_WEEKS * 5
    if len(df) < lookback:
        return {"base_tight": 100.0, "vol_ratio": 1.0}
    tail = df["Close"].tail(lookback)
    base_tight = (tail.max() - tail.min()) / tail.min() * 100 if tail.min() > 0 else 100.0
    vol_tail = df["Volume"].tail(LB_VOL_WINDOW)
    vol_ratio = vol_tail.iloc[-1] / vol_tail.iloc[:-1].mean() if len(vol_tail) > 1 and vol_tail.iloc[:-1].mean() > 0 else 1.0
    return {"base_tight": round(float(base_tight), 2), "vol_ratio": round(float(vol_ratio), 1)}


def _template_pack(trend: dict, prox_below: float, off_low_pct: float, rs_val) -> dict:
    """Minervini 8 ข้อ — ข้อ 8 คือ RS ≥ 70"""
    tt1 = bool(trend.get("trend_c2"))          # Px > SMA150
    tt2 = bool(trend.get("trend_c3"))          # Px > SMA200
    tt3 = bool(trend.get("ma_aligned")) and _gt(trend.get("sma150"), trend.get("sma200"))
    tt4 = bool(trend.get("sma200_rising"))
    tt5 = bool(trend.get("ma_aligned"))        # SMA50 > SMA150 > SMA200
    tt6 = bool(trend.get("trend_c1"))          # Px > SMA50
    tt7 = off_low_pct >= LB_OFF_LOW_MIN
    tt8 = (rs_val or 0) >= LB_RS_LEADER_MIN
    # tt3 แยก 150>200 ถ้า ma_aligned ใช้ 50>150>200 อยู่แล้ว tt3 ซ้ำ — คง 150>200 ชัดๆ
    tt3 = _gt(trend.get("sma150"), trend.get("sma200"))
    flags = [tt1, tt2, tt3, tt4, tt5, tt6, tt7, tt8]
    return {
        "tt1": tt1, "tt2": tt2, "tt3": tt3, "tt4": tt4,
        "tt5": tt5, "tt6": tt6, "tt7": tt7, "tt8": tt8,
        "template_score": int(sum(flags)),
        "template_pass": int(sum(flags)) >= LB_TEMPLATE_MIN and bool(tt8),
        "within_25_high": prox_below <= LB_OFF_HIGH_MAX,
    }


def _score_ls(s: dict) -> int:
    rs_val = s.get("rs") or 0
    prox = float(s.get("prox_52w") or 0)
    template = int(s.get("template_score") or 0)
    r1d = s.get("r1d") or 0
    vol_ratio = float(s.get("vol_ratio") or 1)
    base_tight = float(s.get("base_tight") or 100)
    theme_boost = 100 if s.get("is_theme_leader") else (50 if (s.get("theme_rank") or 99) <= 3 else 0)
    ls_rs = rs_val * 0.30
    ls_tt = (template / 8) * 100 * 0.25
    ls_prox = max(0, 100 - prox * 4) * 0.15
    ls_theme = theme_boost * 0.10
    ls_vol = min(100, max(0, (vol_ratio - 1) * 50)) * 0.10 if r1d > 0 else 0
    ls_tight = max(0, 100 - base_tight * 2) * 0.10
    return int(ls_rs + ls_tt + ls_prox + ls_theme + ls_vol + ls_tight)


def _score_ws(s: dict) -> int:
    rs_val = s.get("rs")
    weak_rs = 49 if rs_val is None else max(0, 99 - rs_val)
    fade = max(0, -(s.get("drs7") or 0)) * 6
    dd = abs(min(0.0, float(s.get("drawdown_pct") or 0)))
    accum = float(s.get("accum_score") or 0)
    ud = float(s.get("ud_ratio") or 1)
    if accum <= -0.1 or ud <= 0.8:
        dist = 80
    elif accum >= LB_ACCUM_MIN and ud >= LB_UD_MIN:
        dist = 0
    else:
        dist = 15
    broken = (4 - int(s.get("trend_score") or 0)) / 4 * 100
    if int(s.get("stage") or 0) == 4:
        broken = max(broken, 80)
    return int(min(99, weak_rs * 0.30 + min(100, fade) * 0.20 + min(100, dd * 2) * 0.20 + dist * 0.15 + broken * 0.15))


def _apply_theme_ranks(rows: list[dict]) -> None:
    groups: dict[str, list] = defaultdict(list)
    for s in rows:
        groups[str(s.get("theme") or "_none")].append(s)
    for _theme, members in groups.items():
        ranked = sorted(members, key=lambda x: (x.get("rs") is not None, x.get("rs") or 0), reverse=True)
        n = len(ranked)
        cutoff = max(1, n // 4)
        for i, row in enumerate(ranked):
            row["theme_rank"] = i + 1
            row["theme_n"] = n
            row["is_theme_leader"] = bool(
                n >= 3 and i < cutoff and (row.get("rs") or 0) >= LB_RS_LEADER_MIN
            )


@ttl_cache(CACHE_TTL_DATA)
def build_leadership_board(mode: str) -> dict:
    t0 = time.time()
    pack = pipeline.load_market_pack(mode)
    data = _get_leadership_data(mode=mode, pack=pack)
    if not data.get("ok"):
        return data

    combined = data["combined"]
    ticker_meta = data["ticker_meta"]
    rs_now = data.get("rs_now")
    rs_7 = data.get("rs_7")
    ticker_signal = data.get("ticker_signal")

    if rs_now is None:
        rs_now = pd.Series(dtype=float)
    if rs_7 is None:
        rs_7 = pd.Series(dtype=float)
    if ticker_signal is None:
        ticker_signal = {}

    all_stocks = []
    for ticker, df in combined.items():
        if df is None or len(df) < 50:
            continue
        meta = ticker_meta.get(ticker, {})
        last = df.iloc[-1]
        px = _f(last.get("Close"), 0.0) or 0.0

        trend_data = _calc_trend_template(df)
        accum_data = _calc_accumulation(df)
        vol_data = _calc_volatility(df)

        high_52w = last.get("HIGH_52W", df["High"].tail(252).max() if len(df) >= 252 else df["High"].max())
        high_52w = _f(high_52w, 0.0) or 0.0
        low_52w = _f(df["Low"].tail(252).min() if len(df) >= 60 else df["Low"].min(), 0.0) or 0.0
        off_52w = (px / high_52w - 1) * 100 if high_52w > 0 else 0.0
        prox_below = 0.0 if off_52w >= 0 else abs(off_52w)
        off_low_pct = (px / low_52w - 1) * 100 if low_52w > 0 else 0.0

        drawdown_pct = -abs(eng.current_drawdown_from_peak(df["Close"]))

        raw_rs = rs_now.get(ticker) if hasattr(rs_now, "get") else None
        try:
            rs_val = int(raw_rs) if raw_rs is not None and pd.notna(raw_rs) else None
        except (TypeError, ValueError):
            rs_val = None
        raw_rs7 = rs_7.get(ticker) if hasattr(rs_7, "get") else None
        try:
            rs7 = int(raw_rs7) if raw_rs7 is not None and pd.notna(raw_rs7) else None
        except (TypeError, ValueError):
            rs7 = None
        drs7_val = (rs_val - rs7) if rs_val is not None and rs7 is not None else 0

        stage = _weinstein_stage(px, trend_data.get("sma150"), trend_data.get("sma200"), trend_data.get("sma200_rising"))
        tmpl = _template_pack(trend_data, prox_below, off_low_pct, rs_val)
        signals = ticker_signal.get(ticker, {})

        all_stocks.append({
            "ticker": ticker, "symbol": str(ticker).split(".")[0], "name": meta.get("name", ""),
            "theme": meta.get("theme", ""), "market": meta.get("market", ""),
            "rs": rs_val, "drs7": drs7_val, **trend_data, **accum_data, **vol_data, **tmpl,
            "stage": stage,
            "off_52w": round(float(off_52w), 1),
            "prox_52w": round(float(prox_below), 1),
            "off_low_pct": round(float(off_low_pct), 1),
            "drawdown_pct": round(drawdown_pct, 1),
            "price": round(float(px), 4 if px < 1 else 2),
            "r1d": eng.pct_change(df["Close"], 1),
            "r1m": eng.pct_change(df["Close"], 21),
            "r3m": eng.pct_change(df["Close"], 63),
            "is_vdu": signals.get("rolled", {}).get("VDU", False),
            "is_pocket": signals.get("rolled", {}).get("PPBP", False),
            "is_bgu": signals.get("rolled", {}).get("BGU", False),
            "is_near_52w": signals.get("rolled", {}).get("52W", False),
            "is_vcp": signals.get("rolled", {}).get("VCP", False) or bool((signals.get("vcp") or {}).get("is_vcp")),
            **{k: v for k, v in (signals.get("vcp") or {}).items() if k.startswith("vcp_")},
        })

    _apply_theme_ranks(all_stocks)
    for s in all_stocks:
        s["ls"] = _score_ls(s)
        s["ws"] = _score_ws(s)
        s["role"] = _classify_role(s)

    leaders = [s for s in all_stocks if s.get("role") == "leader"]
    laggards_n = sum(1 for s in all_stocks if s.get("role") == "laggard")
    watch_n = sum(1 for s in all_stocks if s.get("role") == "watch")

    overall = sorted(leaders, key=lambda x: (x.get("template_score") or 0, x["ls"], x.get("rs") or 0), reverse=True)[:LB_TOP_N]
    top_rs = sorted([s for s in leaders if (s.get("rs") or 0) >= LB_RS_ELITE], key=lambda x: x.get("rs") or 0, reverse=True)[:LB_TOP_N]
    theme_leaders = sorted(
        [s for s in leaders if s.get("is_theme_leader")],
        key=lambda x: (x.get("rs") or 0, x.get("ls") or 0),
        reverse=True,
    )[:LB_TOP_N]
    top_momentum = sorted([s for s in leaders if s["drs7"] > 0], key=lambda x: x["drs7"], reverse=True)[:LB_TOP_N]
    near_breakout = sorted(
        [s for s in leaders if s["prox_52w"] <= LB_BREAKOUT_PROX and s["trend_score"] >= 3],
        key=lambda x: x["prox_52w"],
    )[:LB_TOP_N]
    volume_proxy = sorted(
        [s for s in leaders if s["accum_score"] >= LB_ACCUM_MIN and s["ud_ratio"] >= LB_UD_MIN],
        key=lambda x: (x["accum_score"], x["ud_ratio"]),
        reverse=True,
    )[:LB_TOP_N]
    volume_surge = sorted(
        [s for s in leaders if s["vol_ratio"] >= LB_VOL_MIN and (s.get("r1d") or 0) > 0],
        key=lambda x: x["vol_ratio"],
        reverse=True,
    )[:LB_TOP_N]
    trend_template = sorted(
        [s for s in leaders if s.get("template_pass") or ((s.get("template_score") or 0) >= LB_TEMPLATE_MIN and (s.get("rs") or 0) >= LB_RS_LEADER_MIN)],
        key=lambda x: (x.get("template_score") or 0, x.get("rs") or 0),
        reverse=True,
    )[:LB_TOP_N]

    next_macro, earn_map = _calendar_overlay()
    for bucket in (overall, top_rs, theme_leaders, top_momentum, near_breakout, volume_proxy, volume_surge, trend_template, all_stocks):
        for row in bucket:
            tk = str(row.get("ticker") or "").split(".")[0].upper()
            if tk in earn_map:
                row["next_earnings"] = earn_map[tk]
            if next_macro:
                row["next_macro"] = next_macro

    breadth_pct = round(100 * len(leaders) / len(all_stocks), 1) if all_stocks else 0.0
    out = {
        "ok": True, "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "total": len(all_stocks),
        "n_leaders": len(leaders),
        "n_laggards": laggards_n,
        "n_watch": watch_n,
        "breadth_pct": breadth_pct,
        "n_stage2": sum(1 for s in all_stocks if s.get("stage") == 2),
        "n_stage4": sum(1 for s in all_stocks if s.get("stage") == 4),
        "n_rs90": sum(1 for s in all_stocks if (s.get("rs") or 0) >= 90),
        "overall": overall, "top_rs": top_rs, "theme_leaders": theme_leaders,
        "top_momentum": top_momentum,
        "near_breakout": near_breakout,
        "institutional": volume_proxy,
        "volume_proxy": volume_proxy,
        "volume_surge": volume_surge, "trend_template": trend_template,
        "next_macro": next_macro,
        "universe": all_stocks,
        "markets": sorted({s.get("market") for s in all_stocks if s.get("market")}),
        "method": "canslim-L / minervini-8 / weinstein-stage",
    }
    log.info("leadership board mode=%s rows=%d leaders=%d in %.2fs", mode, len(all_stocks), len(leaders), time.time() - t0)
    return out


@ttl_cache(CACHE_TTL_DATA)
def build_laggards_board(mode: str) -> dict:
    board = build_leadership_board(mode)
    if not board.get("ok"):
        return board
    stocks = list(board.get("universe") or [])
    laggards = [s for s in stocks if s.get("role") == "laggard"]
    n = LB_TOP_N
    weak_rs = sorted(
        [s for s in laggards if s.get("rs") is not None and s.get("rs") <= 30],
        key=lambda x: x.get("rs") if x.get("rs") is not None else 99,
    )[:n]
    rs_fade = sorted(
        [s for s in laggards if (s.get("drs7") or 0) < 0 and (s.get("rs") is None or s.get("rs") <= LB_RS_FADE_MAX)],
        key=lambda x: x.get("drs7") or 0,
    )[:n]
    distribution = sorted(
        [s for s in laggards if (s.get("accum_score") or 0) <= -0.1 or (s.get("ud_ratio") or 1) <= 0.8],
        key=lambda x: (x.get("accum_score") or 0, x.get("ud_ratio") or 1),
    )[:n]
    off_highs = sorted(
        [s for s in laggards if (s.get("prox_52w") or 0) >= 20 or (s.get("drawdown_pct") or 0) <= -20],
        key=lambda x: x.get("prox_52w") or 0,
        reverse=True,
    )[:n]
    broken_trend = sorted(
        [s for s in laggards if (s.get("trend_score") or 0) <= 1 or (s.get("stage") or 0) == 4],
        key=lambda x: (x.get("trend_score") or 0, x.get("rs") or 0),
    )[:n]
    stage4 = sorted(
        [s for s in laggards if (s.get("stage") or 0) == 4],
        key=lambda x: (x.get("ws") or 0, -(x.get("rs") or 0)),
        reverse=True,
    )[:n]
    worst = sorted(laggards, key=lambda x: x.get("ws") or 0, reverse=True)[:n]
    return {
        "ok": True,
        "updated": board.get("updated"),
        "total": len(stocks),
        "n_leaders": board.get("n_leaders") or 0,
        "n_laggards": len(laggards),
        "n_watch": board.get("n_watch") or 0,
        "breadth_pct": board.get("breadth_pct") or 0,
        "n_stage2": board.get("n_stage2") or 0,
        "n_stage4": board.get("n_stage4") or 0,
        "universe": stocks,
        "markets": board.get("markets") or [],
        "worst": worst,
        "weak_rs": weak_rs,
        "rs_fade": rs_fade,
        "distribution": distribution,
        "off_highs": off_highs,
        "broken_trend": broken_trend,
        "stage4": stage4,
        "method": board.get("method"),
    }


def _calendar_overlay():
    try:
        import economic_calendar as ec
        data = ec.fetch_economic_calendar() or {}
        events = data.get("events") or []
        next_macro = None
        earn_map: dict[str, str] = {}
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if ev.get("category") == "EARNINGS":
                date_s = ev.get("date")
                for t in ev.get("tickers") or []:
                    key = str(t).split(".")[0].upper()
                    if key and key not in earn_map and date_s:
                        earn_map[key] = date_s
            elif ev.get("importance") == "HIGH" and not ev.get("is_past"):
                try:
                    days = int(ev.get("days_away"))
                except (TypeError, ValueError):
                    continue
                if 0 <= days <= 14:
                    if next_macro is None or days < int(next_macro.get("days_away") or 99):
                        next_macro = ev
        return next_macro, earn_map
    except Exception:
        log.exception("calendar overlay failed")
        return None, {}
