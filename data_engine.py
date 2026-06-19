"""
data_engine.py — Pure Quant Engine (no network)
v2: replaced all magic numbers with constants imports
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from constants import (
    SMA_SHORT, SMA_MID, SMA_TREND, SMA_LONG, VOL_SMA, HIGH_52W,
    VDU_VOL_LOW, VDU_VOL_HIGH,
    BGU_GAP_PCT, BGU_VOL_MULT,
    W52_PROXIMITY,
    PPBP_VOL_LOOKBACK,
    CONFLUENCE_DAYS, CONFLUENCE_MIN,
    RS_BLEND_3M_WT, RS_BLEND_6M_WT, RS_BLEND_9M_WT, RS_BLEND_12M_WT,
    TRADING_DAYS_MONTH, TRADING_DAYS_QUARTER,
    TRADING_DAYS_HALFYR, TRADING_DAYS_3QTR, TRADING_DAYS_YEAR,
    BREADTH_HISTORY_DAYS,
)

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


# ── 1) Indicators ──────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append SMA10/50/150/200, 50d avg volume, 252d rolling high."""
    out = df.copy()
    out["SMA10"]    = out["Close"].rolling(SMA_SHORT,  min_periods=1).mean()
    out["SMA50"]    = out["Close"].rolling(SMA_MID,    min_periods=1).mean()
    out["SMA150"]   = out["Close"].rolling(SMA_TREND,  min_periods=1).mean()
    out["SMA200"]   = out["Close"].rolling(SMA_LONG,   min_periods=1).mean()
    out["VOL_SMA50"]= out["Volume"].rolling(VOL_SMA,   min_periods=1).mean()
    out["HIGH_52W"] = out["Close"].rolling(HIGH_52W,   min_periods=1).max()
    return out


# ── 2) Scanners ────────────────────────────────────────────────────────────────
def scan_volume_dry_up(df: pd.DataFrame) -> pd.Series:
    """VDU: volume 40-60% of 50d avg (institutional sweet spot)."""
    ratio = df["Volume"] / df["VOL_SMA50"].replace(0, np.nan)
    return (ratio >= VDU_VOL_LOW) & (ratio <= VDU_VOL_HIGH)


def scan_pocket_pivot(df: pd.DataFrame, vol_lookback: int = PPBP_VOL_LOOKBACK) -> pd.Series:
    """PPBP: up day, above SMA10, volume > max down-day vol of prior N sessions."""
    up_day        = df["Close"] > df["Close"].shift(1)
    above_sma10   = df["Close"] > df["SMA10"]
    down_day_vol  = df["Volume"].where(df["Close"] < df["Close"].shift(1))
    max_down_vol  = down_day_vol.shift(1).rolling(vol_lookback, min_periods=1).max()
    return up_day & above_sma10 & (df["Volume"] > max_down_vol)


def scan_buyable_gap_up(df: pd.DataFrame,
                        gap_pct: float = BGU_GAP_PCT,
                        vol_mult: float = BGU_VOL_MULT) -> pd.Series:
    """BGU: open >= gap_pct% above prior close AND volume >= vol_mult x 50d avg."""
    gap = (df["Open"] - df["Close"].shift(1)) / df["Close"].shift(1) * 100
    return (gap >= gap_pct) & (df["Volume"] >= vol_mult * df["VOL_SMA50"])


def scan_52w_high(df: pd.DataFrame, pct: float = W52_PROXIMITY) -> pd.Series:
    """52W: Close within pct of the rolling 252-session high."""
    return df["Close"] >= pct * df["HIGH_52W"]


SCANNERS = {
    "VDU":  scan_volume_dry_up,
    "PPBP": scan_pocket_pivot,
    "BGU":  scan_buyable_gap_up,
    "52W":  scan_52w_high,
}


def run_scanners(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {name: fn(df) for name, fn in SCANNERS.items()}


# ── 3) Confluence ──────────────────────────────────────────────────────────────
def confluence_flags(signals: dict[str, pd.Series],
                     rolling_days: int = CONFLUENCE_DAYS,
                     min_signals: int  = CONFLUENCE_MIN):
    rolled = {
        name: s.rolling(rolling_days, min_periods=1).max().fillna(0).astype(bool)
        for name, s in signals.items()
    }
    count      = sum(r.astype(int) for r in rolled.values())
    confluence = count >= min_signals
    return rolled, confluence, count


# ── 4) Market Breadth ──────────────────────────────────────────────────────────
def breadth_pct(df: pd.DataFrame) -> tuple[float, float]:
    last = df.iloc[-1]
    return float(last["Close"] > last["SMA50"]), float(last["Close"] > last["SMA200"])


def market_breadth_history(close_above_df: pd.DataFrame,
                            days: int = BREADTH_HISTORY_DAYS) -> pd.DataFrame:
    pct = close_above_df.mean(axis=1) * 100
    return pct.tail(days)


# ── 5) RS Rating ───────────────────────────────────────────────────────────────
def blended_return(close: pd.Series) -> float:
    """
    IBD-style blended return:
      40% × 3M + 20% × 6M + 20% × 9M + 20% × 12M
    """
    def ret(n: int) -> float:
        n = min(n, len(close) - 1)
        if n <= 0:
            return 0.0
        base = close.iloc[-1 - n]
        return float(close.iloc[-1] / base - 1.0) if base != 0 else 0.0

    return (RS_BLEND_3M_WT  * ret(TRADING_DAYS_QUARTER) +
            RS_BLEND_6M_WT  * ret(TRADING_DAYS_HALFYR)  +
            RS_BLEND_9M_WT  * ret(TRADING_DAYS_3QTR)    +
            RS_BLEND_12M_WT * ret(TRADING_DAYS_YEAR))


def rs_rating_table(blended_returns: pd.Series) -> pd.Series:
    pct = blended_returns.rank(pct=True, method="average")
    return (pct * 98 + 1).round().astype(int).clip(1, 99)


# ── 6) Theme / Sector rotation ────────────────────────────────────────────────
def theme_returns(returns_1d: pd.Series, returns_1m: pd.Series,
                  returns_3m: pd.Series, theme_map: dict[str, str]) -> pd.DataFrame:
    df = pd.DataFrame({"1D": returns_1d, "1M": returns_1m, "3M": returns_3m})
    df["theme"] = df.index.map(theme_map)
    g = df.groupby("theme")[["1D", "1M", "3M"]].mean()
    g["score"] = g["1M"] + g["3M"]
    return g.sort_values("score", ascending=False)


def rs_movers_7d(rs_today: pd.Series, rs_7d_ago: pd.Series, top_n: int = 5) -> pd.DataFrame:
    delta = (rs_today - rs_7d_ago).rename("dRS_7D")
    out   = pd.DataFrame({"RS": rs_today, "dRS_7D": delta})
    return out.sort_values("dRS_7D", ascending=False).head(top_n)
