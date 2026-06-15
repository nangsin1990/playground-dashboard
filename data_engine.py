"""
Core Quant Engine
=================
Pure-computation layer (no Streamlit / network calls here) so it can be
unit-tested with synthetic data. `data_io.py` handles the yfinance calls
and feeds OHLCV DataFrames into these functions.

Formulas implement the spec described in the project doc:
  - Market Breadth          : % of universe with Close > SMA50 / SMA200
  - Volume Dry-Up (VDU)      : pause-day volume in the 40-60% of 50d-avg
                                "institutional sweet spot" (below 40% =
                                "abandoned stock", excluded)
  - Pocket Pivot (PPBP)      : up-day, close > 10d SMA, volume > largest
                                down-day volume of the prior 10 sessions
  - Buyable Gap-Up (BGU)     : open >= +1.5% gap vs prior close AND
                                volume >= 250% of 50d-avg volume
  - 52-Week High Breakout    : Close >= 0.95 * rolling 252d high
  - Confluence               : >=2 of the 4 scanners fire within the
                                trailing 5 trading days
  - RS Rating                : percentile rank (1-99) of a blended
                                3/6/9/12-month return, across the whole
                                combined universe
  - Theme Movers / dRS 7D    : equal-weight average return per
                                theme/sector; RS-rating delta over 7
                                trading days for individual names
"""

from __future__ import annotations
import numpy as np
import pandas as pd

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


# ----------------------------------------------------------------------
# 1) Indicators
# ----------------------------------------------------------------------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append SMA50/150/200, 50d avg volume, and 252d rolling high."""
    out = df.copy()
    out["SMA50"] = out["Close"].rolling(50, min_periods=1).mean()
    out["SMA150"] = out["Close"].rolling(150, min_periods=1).mean()
    out["SMA200"] = out["Close"].rolling(200, min_periods=1).mean()
    out["SMA10"] = out["Close"].rolling(10, min_periods=1).mean()
    out["VOL_SMA50"] = out["Volume"].rolling(50, min_periods=1).mean()
    out["HIGH_52W"] = out["Close"].rolling(252, min_periods=1).max()
    return out


# ----------------------------------------------------------------------
# 2) The four scanners -> each returns a boolean Series aligned to df.index
# ----------------------------------------------------------------------
def scan_volume_dry_up(df: pd.DataFrame) -> pd.Series:
    """VDU: today's volume is 40-60% of the 50d average (the 'sweet
    spot'). <40% is flagged separately as an abandoned/illiquid stock and
    does NOT count as a VDU signal."""
    ratio = df["Volume"] / df["VOL_SMA50"].replace(0, np.nan)
    return (ratio >= 0.40) & (ratio <= 0.60)


def scan_pocket_pivot(df: pd.DataFrame, vol_lookback: int = 10) -> pd.Series:
    """PPBP (Kacher/Morales): up day, closes above the 10d SMA, and
    today's volume exceeds the largest down-day volume of the prior
    `vol_lookback` sessions."""
    up_day = df["Close"] > df["Close"].shift(1)
    above_sma10 = df["Close"] > df["SMA10"]
    down_day_vol = df["Volume"].where(df["Close"] < df["Close"].shift(1))
    max_down_vol_prior = down_day_vol.shift(1).rolling(vol_lookback, min_periods=1).max()
    return up_day & above_sma10 & (df["Volume"] > max_down_vol_prior)


def scan_buyable_gap_up(df: pd.DataFrame, gap_pct: float = 1.5, vol_mult: float = 2.5) -> pd.Series:
    """BGU: opens >= gap_pct% above prior close AND volume >= vol_mult x
    the 50d average volume."""
    gap = (df["Open"] - df["Close"].shift(1)) / df["Close"].shift(1) * 100
    return (gap >= gap_pct) & (df["Volume"] >= vol_mult * df["VOL_SMA50"])


def scan_52w_high(df: pd.DataFrame, pct: float = 0.95) -> pd.Series:
    """52W: Close within `pct` of the rolling 252-session high."""
    return df["Close"] >= pct * df["HIGH_52W"]


SCANNERS = {
    "VDU": scan_volume_dry_up,
    "PPBP": scan_pocket_pivot,
    "BGU": scan_buyable_gap_up,
    "52W": scan_52w_high,
}


def run_scanners(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Run all 4 scanners on an indicator-enriched DataFrame."""
    return {name: fn(df) for name, fn in SCANNERS.items()}


# ----------------------------------------------------------------------
# 3) Confluence: rolling 5-day, >=2 of 4 scanners firing
# ----------------------------------------------------------------------
def confluence_flags(signals: dict[str, pd.Series], rolling_days: int = 5, min_signals: int = 2):
    """For each scanner, did it fire within the trailing `rolling_days`?
    Returns (rolled_dict, confluence_bool_series, count_series)."""
    rolled = {
        name: s.rolling(rolling_days, min_periods=1).max().fillna(0).astype(bool)
        for name, s in signals.items()
    }
    count = sum(r.astype(int) for r in rolled.values())
    confluence = count >= min_signals
    return rolled, confluence, count


# ----------------------------------------------------------------------
# 4) Market Breadth
# ----------------------------------------------------------------------
def breadth_pct(df: pd.DataFrame) -> tuple[float, float]:
    """% above SMA50, % above SMA200 -- for a single name's *latest* row,
    returns (1.0/0.0, 1.0/0.0). Aggregate across the universe to get the
    market-level percentage."""
    last = df.iloc[-1]
    above50 = float(last["Close"] > last["SMA50"])
    above200 = float(last["Close"] > last["SMA200"])
    return above50, above200


def market_breadth_history(close_above_df: pd.DataFrame, days: int = 20) -> pd.DataFrame:
    """close_above_df: DataFrame indexed by date, columns = tickers,
    values = bool (Close > SMA). Returns the daily cross-sectional % True
    for the last `days` rows."""
    pct = close_above_df.mean(axis=1) * 100
    return pct.tail(days)


# ----------------------------------------------------------------------
# 5) RS Rating (1-99 percentile of blended return)
# ----------------------------------------------------------------------
def blended_return(close: pd.Series) -> float:
    """0.4*ret_63d + 0.2*ret_126d + 0.2*ret_189d + 0.2*ret_252d
    (an IBD-style blended relative-strength input)."""
    def ret(n):
        if len(close) <= n:
            n = len(close) - 1
        if n <= 0 or close.iloc[-1 - n] == 0:
            return 0.0
        return close.iloc[-1] / close.iloc[-1 - n] - 1.0

    return 0.4 * ret(63) + 0.2 * ret(126) + 0.2 * ret(189) + 0.2 * ret(252)


def rs_rating_table(blended_returns: pd.Series) -> pd.Series:
    """Percentile-rank a Series of blended returns into a 1-99 RS Rating."""
    pct = blended_returns.rank(pct=True, method="average")
    return (pct * 98 + 1).round().astype(int).clip(1, 99)


# ----------------------------------------------------------------------
# 6) Theme / Sector rotation
# ----------------------------------------------------------------------
def theme_returns(returns_1d: pd.Series, returns_1m: pd.Series, returns_3m: pd.Series,
                   theme_map: dict[str, str]) -> pd.DataFrame:
    """Equal-weight average 1D/1M/3M return per theme, sorted by 1M+3M."""
    df = pd.DataFrame({"1D": returns_1d, "1M": returns_1m, "3M": returns_3m})
    df["theme"] = df.index.map(theme_map)
    g = df.groupby("theme")[["1D", "1M", "3M"]].mean()
    g["score"] = g["1M"] + g["3M"]
    return g.sort_values("score", ascending=False)


def rs_movers_7d(rs_today: pd.Series, rs_7d_ago: pd.Series, top_n: int = 5) -> pd.DataFrame:
    """Top-N names by 7-session RS Rating delta (dRS 7D)."""
    delta = (rs_today - rs_7d_ago).rename("dRS_7D")
    out = pd.DataFrame({"RS": rs_today, "dRS_7D": delta})
    return out.sort_values("dRS_7D", ascending=False).head(top_n)
