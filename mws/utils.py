# -*- coding: utf-8 -*-
"""Shared helpers — timezone, formatting, numeric guards. MWS v3.8.0"""

from __future__ import annotations

from typing import Any, Optional, Tuple

import numpy as np
import pandas as pd


def strip_tz(index_or_ts) -> Any:
    """Return tz-naive DatetimeIndex / Timestamp. Safe for both naive and aware input."""
    if index_or_ts is None:
        return index_or_ts
    if isinstance(index_or_ts, pd.DatetimeIndex):
        idx = pd.to_datetime(index_or_ts)
        if idx.tz is not None:
            return idx.tz_convert("UTC").tz_localize(None)
        return idx
    if isinstance(index_or_ts, pd.Timestamp):
        ts = pd.Timestamp(index_or_ts)
        if ts.tzinfo is not None:
            return ts.tz_convert("UTC").tz_localize(None)
        return ts
    try:
        idx = pd.to_datetime(index_or_ts)
        if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
            return idx.tz_convert("UTC").tz_localize(None)
        if isinstance(idx, pd.Timestamp) and idx.tzinfo is not None:
            return idx.tz_convert("UTC").tz_localize(None)
        return idx
    except Exception:
        return index_or_ts


def naive_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    out.index = strip_tz(out.index)
    return out


def align_closes(a: pd.Series, b: pd.Series) -> Tuple[pd.Series, pd.Series]:
    a = a.copy()
    b = b.copy()
    a.index = strip_tz(a.index)
    b.index = strip_tz(b.index)
    joined = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    return joined["a"], joined["b"]


def is_num(v) -> bool:
    return v is not None and not (isinstance(v, float) and np.isnan(v))


def safe_div(num, den, default=np.nan):
    try:
        if den is None or den == 0 or (isinstance(den, float) and np.isnan(den)):
            return default
        return num / den
    except Exception:
        return default


def normalize_debt_to_equity(de, cash=None, debt=None) -> Optional[float]:
    """
    Yahoo debtToEquity is usually already percent (e.g. 147.3).
    Some feeds send a ratio (1.47). Values in (0, 15) look like a ratio
    unless the company is net cash — then 6.3 almost certainly means 6.3%.
    """
    if not is_num(de):
        return None
    val = float(de)
    if val < 0:
        return val
    net_cash = is_num(cash) and is_num(debt) and float(cash) >= float(debt)
    if 0 < val < 15:
        if net_cash:
            return val
        return val * 100.0
    if net_cash and val > 200:
        return None
    return val


def fmt(v, decimals=2, suffix=""):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    if isinstance(v, (int, np.integer)):
        return f"{v:,}{suffix}"
    return f"{v:,.{decimals}f}{suffix}"


def fmt_pct(v, decimals=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    return f"{v:+.{decimals}f}%"


def safe_get(d: dict, key: str, default=None):
    val = d.get(key, default)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return val
