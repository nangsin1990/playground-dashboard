# FILE: leadership_core.py
# เกณฑ์กลางชุดเดียว: CAN SLIM L + Minervini 8 ข้อ + Weinstein Stage
# ห้าม import pipeline เพื่อกันวน

from __future__ import annotations

import numpy as np
import pandas as pd

from constants import (
    LB_TREND_LOOKBACK, LB_ACCUM_LOOKBACK, LB_TIGHTNESS_WEEKS,
    LB_UD_RATIO_LOOKBACK, LB_VOL_WINDOW, LB_ACCUM_MIN, LB_UD_MIN,
    LB_RS_LEADER_MIN, LB_RS_LAGGARD_MAX, LB_OFF_LOW_MIN, LB_OFF_HIGH_MAX,
    LB_TEMPLATE_MIN,
)
import data_engine as eng


def gt(a, b) -> bool:
    try:
        if a is None or b is None or pd.isna(a) or pd.isna(b):
            return False
        return float(a) > float(b)
    except (TypeError, ValueError):
        return False


def to_f(v, default=None):
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def classify_role(s: dict) -> str:
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


def calc_trend_template(df: pd.DataFrame) -> dict:
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
    c1 = gt(px, sma50)
    c2 = gt(px, sma150)
    c3 = gt(px, sma200)
    sma200_s = df["SMA200"] if "SMA200" in df.columns else df["Close"].rolling(200, min_periods=100).mean()
    tail = sma200_s.tail(LB_TREND_LOOKBACK).dropna()
    c4 = bool(len(tail) > 1 and gt(tail.iloc[-1], tail.iloc[0]))
    ma_aligned = gt(sma50, sma150) and gt(sma150, sma200)
    return {
        "trend_c1": bool(c1), "trend_c2": bool(c2), "trend_c3": bool(c3), "trend_c4": bool(c4),
        "trend_score": int(c1) + int(c2) + int(c3) + int(c4),
        "sma50": to_f(sma50), "sma150": to_f(sma150), "sma200": to_f(sma200),
        "sma200_rising": bool(c4), "ma_aligned": bool(ma_aligned),
    }


def weinstein_stage(px, sma150, sma200, sma200_rising) -> int:
    above_200 = gt(px, sma200)
    above_150 = gt(px, sma150)
    if above_200 and sma200_rising and above_150:
        return 2
    if (not above_200) and (not sma200_rising):
        return 4
    if above_200 and not sma200_rising:
        return 3
    return 1


def calc_accumulation(df: pd.DataFrame) -> dict:
    if df is None or len(df) < LB_UD_RATIO_LOOKBACK:
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


def calc_volatility(df: pd.DataFrame) -> dict:
    lookback = LB_TIGHTNESS_WEEKS * 5
    if df is None or len(df) < lookback:
        return {"base_tight": 100.0, "vol_ratio": 1.0}
    tail = df["Close"].tail(lookback)
    base_tight = (tail.max() - tail.min()) / tail.min() * 100 if tail.min() > 0 else 100.0
    vol_tail = df["Volume"].tail(LB_VOL_WINDOW)
    vol_ratio = vol_tail.iloc[-1] / vol_tail.iloc[:-1].mean() if len(vol_tail) > 1 and vol_tail.iloc[:-1].mean() > 0 else 1.0
    return {"base_tight": round(float(base_tight), 2), "vol_ratio": round(float(vol_ratio), 1)}


def template_pack(trend: dict, prox_below: float, off_low_pct: float, rs_val) -> dict:
    tt1 = bool(trend.get("trend_c2"))
    tt2 = bool(trend.get("trend_c3"))
    tt3 = gt(trend.get("sma150"), trend.get("sma200"))
    tt4 = bool(trend.get("sma200_rising"))
    tt5 = bool(trend.get("ma_aligned"))
    tt6 = bool(trend.get("trend_c1"))
    tt7 = off_low_pct >= LB_OFF_LOW_MIN
    tt8 = (rs_val or 0) >= LB_RS_LEADER_MIN
    flags = [tt1, tt2, tt3, tt4, tt5, tt6, tt7, tt8]
    return {
        "tt1": tt1, "tt2": tt2, "tt3": tt3, "tt4": tt4,
        "tt5": tt5, "tt6": tt6, "tt7": tt7, "tt8": tt8,
        "template_score": int(sum(flags)),
        "template_pass": int(sum(flags)) >= LB_TEMPLATE_MIN and bool(tt8),
        "within_25_high": prox_below <= LB_OFF_HIGH_MAX,
    }


def range_from_df(df: pd.DataFrame) -> dict:
    last = df.iloc[-1]
    px = to_f(last.get("Close"), 0.0) or 0.0
    high_52w = last.get("HIGH_52W", df["High"].tail(252).max() if len(df) >= 60 else df["High"].max())
    high_52w = to_f(high_52w, 0.0) or 0.0
    low_52w = to_f(df["Low"].tail(252).min() if len(df) >= 60 else df["Low"].min(), 0.0) or 0.0
    off_52w = (px / high_52w - 1) * 100 if high_52w > 0 else 0.0
    prox_below = 0.0 if off_52w >= 0 else abs(off_52w)
    off_low_pct = (px / low_52w - 1) * 100 if low_52w > 0 else 0.0
    return {
        "px": px,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "off_52w": round(float(off_52w), 1),
        "prox_52w": round(float(prox_below), 1),
        "off_low_pct": round(float(off_low_pct), 1),
        "drawdown_pct": -abs(eng.current_drawdown_from_peak(df["Close"])),
    }


def snapshot_from_df(df: pd.DataFrame, rs_val=None) -> dict:
    """โครงราคาชุดเดียว สำหรับ leadership board และหน้า index."""
    if df is None or len(df) < 50:
        return {}
    trend = calc_trend_template(df)
    rng = range_from_df(df)
    tmpl = template_pack(trend, rng["prox_52w"], rng["off_low_pct"], rs_val)
    stage = weinstein_stage(rng["px"], trend.get("sma150"), trend.get("sma200"), trend.get("sma200_rising"))
    return {
        **trend,
        **rng,
        **tmpl,
        **calc_accumulation(df),
        **calc_volatility(df),
        "stage": stage,
        "above50": gt(rng["px"], trend.get("sma50")),
        "above200": gt(rng["px"], trend.get("sma200")),
    }


def score_ls(s: dict) -> int:
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


def score_ws(s: dict) -> int:
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


def is_leader_pause(s: dict) -> bool:
    """ตัวนำที่กำลังพักฐาน — ไม่ใช่ของถูกเพราะถูกเท."""
    rs = s.get("rs")
    if rs is None or rs < LB_RS_LEADER_MIN:
        return False
    if (s.get("drs7") or 0) >= 0:
        return False
    if int(s.get("stage") or 0) == 4:
        return False
    if not s.get("above200"):
        return False
    prox = float(s.get("prox_52w") or 0)
    if prox < 8 or prox > LB_OFF_HIGH_MAX:
        return False
    if float(s.get("off_low_pct") or 0) < LB_OFF_LOW_MIN:
        return False
    return True


def is_confluence_study(s: dict) -> bool:
    """แพทเทิร์นรวมบนหน้า index รับเฉพาะตัวที่ยังไม่ใช่ผู้แพ้และ RS ถึงเกณฑ์ผู้นำ."""
    rs = s.get("rs")
    if rs is None or rs < LB_RS_LEADER_MIN:
        return False
    if int(s.get("stage") or 0) == 4:
        return False
    if (s.get("trend_score") or 0) < 2:
        return False
    return True
