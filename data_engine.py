# FILE: data_engine.py
#"""
#data_engine.py — Pure Quant Engine (no network)
#v3: per-market RS rating, drawdown tracker, correlation prep
#"""

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
    CORR_PERIOD_DAYS,
)

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]

# ✨ REFACTOR: เพิ่ม Section 0 สำหรับฟังก์ชัน Utility กลาง
# ── 0) Core Utilities ──────────────────────────────────────────────────────────
def pct_change(series: pd.Series, n: int) -> float | None:
    """
    Calculates the percentage change over n periods, with safety checks.
    This function is now centralized here to be used by other modules like pipeline.py.
    """
    try:
        if len(series) <= n or series.iloc[-1 - n] == 0:
            return None
        val = (series.iloc[-1] / series.iloc[-1 - n] - 1) * 100
        # Handle numpy's special float values before returning.
        return None if (np.isnan(val) or np.isinf(val)) else round(float(val), 2)
    except (IndexError, TypeError):
        return None


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
    ratio = df["Volume"] / df["VOL_SMA50"].replace(0, np.nan)
    return (ratio >= VDU_VOL_LOW) & (ratio <= VDU_VOL_HIGH)


def scan_pocket_pivot(df: pd.DataFrame, vol_lookback: int = PPBP_VOL_LOOKBACK) -> pd.Series:
    up_day       = df["Close"] > df["Close"].shift(1)
    above_sma10  = df["Close"] > df["SMA10"]
    down_day_vol = df["Volume"].where(df["Close"] < df["Close"].shift(1))
    max_down_vol = down_day_vol.shift(1).rolling(vol_lookback, min_periods=1).max()
    return up_day & above_sma10 & (df["Volume"] > max_down_vol)


def scan_buyable_gap_up(df: pd.DataFrame,
                        gap_pct: float = BGU_GAP_PCT,
                        vol_mult: float = BGU_VOL_MULT) -> pd.Series:
    gap = (df["Open"] - df["Close"].shift(1)) / df["Close"].shift(1) * 100
    return (gap >= gap_pct) & (df["Volume"] >= vol_mult * df["VOL_SMA50"])


def scan_52w_high(df: pd.DataFrame, pct: float = W52_PROXIMITY) -> pd.Series:
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
    count = sum(r.fillna(False).astype(int) for r in rolled.values())
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


# ── 5) RS Rating (per-market) ──────────────────────────────────────────────────
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

    pct = pct.replace([np.inf, -np.inf], np.nan).fillna(0)

    return (
        (pct * 98 + 1)
        .round()
        .fillna(1)
        .astype(int)
        .clip(1, 99)
    )

def rs_rating_per_market(combined: dict, ticker_meta: dict) -> pd.Series:
    """
    Compute RS Rating scoped per market — avoids cross-currency comparison.
    Returns a single Series indexed by ticker with RS 1-99 within its market.
    """
    all_rs: dict[str, int] = {}
    markets = set(m["market"] for m in ticker_meta.values())
    for mkt in markets:
        tickers_in_mkt = [t for t, m in ticker_meta.items() if m["market"] == mkt and t in combined]
        if not tickers_in_mkt:
            continue
        bl = pd.Series({t: blended_return(combined[t]["Close"]) for t in tickers_in_mkt})
        rs = rs_rating_table(bl)
        all_rs.update(rs.to_dict())
    return pd.Series(all_rs)


# ── 6) Drawdown Tracker ────────────────────────────────────────────────────────
def max_drawdown(close: pd.Series) -> float:
    """Max drawdown from peak (negative %). Returns e.g. -15.3 for 15.3% drawdown."""
    if len(close) < 2:
        return 0.0
    roll_max = close.expanding().max()
    dd = (close - roll_max) / roll_max * 100
    return round(float(dd.min()), 2)


def current_drawdown_from_peak(close: pd.Series, lookback: int = TRADING_DAYS_YEAR) -> float:
    """Current % below peak over lookback period."""
    tail = close.tail(lookback)
    if len(tail) < 2:
        return 0.0
    peak    = float(tail.max())
    current = float(tail.iloc[-1])
    if peak == 0:
        return 0.0
    return round((current - peak) / peak * 100, 2)


# ── 7) Theme / Sector rotation ────────────────────────────────────────────────
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


# ── 8) Correlation Matrix ─────────────────────────────────────────────────────
def compute_correlation_matrix(combined: dict, tickers: list[str],
                                 days: int = CORR_PERIOD_DAYS) -> dict:
    """
    Compute pairwise correlation of daily returns over `days` lookback.
    Returns {"labels": [...], "matrix": [[...]], "period_days": days}
    """
    available = [t for t in tickers if t in combined]
    if len(available) < 2:
        return {"ok": False, "error": "Not enough tickers", "labels": [], "matrix": []}

    closes = {}
    for t in available:
        c = combined[t]["Close"].tail(days + 1)
        if len(c) > 1:
            closes[t] = c

    if len(closes) < 2:
        return {"ok": False, "error": "Not enough data", "labels": [], "matrix": []}

    df = pd.DataFrame({t: s for t, s in closes.items()}).pct_change().dropna()
    corr = df.corr()

    labels = list(corr.columns)
    matrix = []
    for row_label in labels:
        row = []
        for col_label in labels:
            v = corr.loc[row_label, col_label]
            row.append(round(float(v), 3) if not np.isnan(v) else None)
        matrix.append(row)

    return {
        "ok":          True,
        "labels":      labels,
        "matrix":      matrix,
        "period_days": days,
        "n_tickers":   len(labels),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ── 9) Technical Indicators: RSI / MACD / Stochastic / Bollinger / VWAP ────
# ══════════════════════════════════════════════════════════════════════════════

def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI (Wilder smoothing)."""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(close: pd.Series,
              fast: int = 12, slow: int = 26, signal: int = 9
              ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast   = close.ewm(span=fast,   adjust=False).mean()
    ema_slow   = close.ewm(span=slow,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line= macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_stochastic(df: pd.DataFrame,
                    k_period: int = 14, d_period: int = 3
                    ) -> tuple[pd.Series, pd.Series]:
    """Returns (%K, %D)."""
    low_min  = df["Low"].rolling(k_period).min()
    high_max = df["High"].rolling(k_period).max()
    denom    = (high_max - low_min).replace(0, np.nan)
    pct_k    = (df["Close"] - low_min) / denom * 100
    pct_d    = pct_k.rolling(d_period).mean()
    return pct_k, pct_d


def calc_bollinger(close: pd.Series,
                   period: int = 20, n_std: float = 2.0
                   ) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Returns (upper, mid, lower, %B)."""
    mid   = close.rolling(period).mean()
    std   = close.rolling(period).std()
    upper = mid + n_std * std
    lower = mid - n_std * std
    pct_b = (close - lower) / (upper - lower).replace(0, np.nan) * 100
    return upper, mid, lower, pct_b


def calc_vwap(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Rolling VWAP over last `lookback` bars (typical price × volume)."""
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3
    pv  = tp * df["Volume"]
    return pv.rolling(lookback).sum() / df["Volume"].rolling(lookback).sum().replace(0, np.nan)


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    hl  = df["High"] - df["Low"]
    hc  = (df["High"] - df["Close"].shift(1)).abs()
    lc  = (df["Low"]  - df["Close"].shift(1)).abs()
    tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append RSI, MACD, Stochastic, Bollinger, VWAP, ATR to df."""
    out = df.copy()
    out["RSI"]         = calc_rsi(out["Close"])
    ml, sl, hist       = calc_macd(out["Close"])
    out["MACD"]        = ml
    out["MACD_SIGNAL"] = sl
    out["MACD_HIST"]   = hist
    k, d               = calc_stochastic(out)
    out["STOCH_K"]     = k
    out["STOCH_D"]     = d
    ub, mb, lb, pb     = calc_bollinger(out["Close"])
    out["BB_UPPER"]    = ub
    out["BB_MID"]      = mb
    out["BB_LOWER"]    = lb
    out["BB_PCT"]      = pb          # 0=at lower, 100=at upper
    out["VWAP"]        = calc_vwap(out)
    out["ATR"]         = calc_atr(out)
    return out


def tech_snapshot(df: pd.DataFrame) -> dict:
    """
    Return latest-bar snapshot of all technical indicators.
    Includes signal interpretation for frontend display.
    """
    df2  = add_technical_indicators(df)
    last = df2.iloc[-1]
    close = float(last["Close"])

    rsi  = round(float(last["RSI"]),  1) if not np.isnan(last["RSI"])  else None
    macd = round(float(last["MACD"]), 4) if not np.isnan(last["MACD"]) else None
    macd_sig = round(float(last["MACD_SIGNAL"]), 4) if not np.isnan(last["MACD_SIGNAL"]) else None
    macd_hist= round(float(last["MACD_HIST"]), 4)   if not np.isnan(last["MACD_HIST"])   else None
    stoch_k  = round(float(last["STOCH_K"]), 1) if not np.isnan(last["STOCH_K"]) else None
    stoch_d  = round(float(last["STOCH_D"]), 1) if not np.isnan(last["STOCH_D"]) else None
    bb_upper = round(float(last["BB_UPPER"]), 2) if not np.isnan(last["BB_UPPER"]) else None
    bb_lower = round(float(last["BB_LOWER"]), 2) if not np.isnan(last["BB_LOWER"]) else None
    bb_pct   = round(float(last["BB_PCT"]), 1)   if not np.isnan(last["BB_PCT"])   else None
    vwap     = round(float(last["VWAP"]), 2)     if not np.isnan(last["VWAP"])     else None
    atr      = round(float(last["ATR"]), 2)      if not np.isnan(last["ATR"])      else None
    atr_pct  = round(atr / close * 100, 2)       if atr and close else None

    # Signal interpretations
    rsi_signal = (
        "Overbought" if rsi and rsi > 70 else
        "Oversold"   if rsi and rsi < 30 else
        "Neutral"
    )
    macd_signal = (
        "Bullish"  if macd_hist and macd_hist > 0 else
        "Bearish"  if macd_hist and macd_hist < 0 else
        "Neutral"
    )
    stoch_signal = (
        "Overbought" if stoch_k and stoch_k > 80 else
        "Oversold"   if stoch_k and stoch_k < 20 else
        "Neutral"
    )
    bb_signal = (
        "Upper Break" if bb_pct and bb_pct >= 100 else
        "Lower Break" if bb_pct and bb_pct <= 0   else
        "Upper Zone"  if bb_pct and bb_pct >= 80  else
        "Lower Zone"  if bb_pct and bb_pct <= 20  else
        "Mid"
    )
    vwap_signal = "Above VWAP" if vwap and close > vwap else "Below VWAP"

    # Sparklines: last 20 bars of RSI and MACD_HIST
    rsi_spark  = [round(float(v), 1) for v in df2["RSI"].tail(20).tolist()
                  if not np.isnan(v)]
    macd_spark = [round(float(v), 4) for v in df2["MACD_HIST"].tail(20).tolist()
                  if not np.isnan(v)]

    return {
        "rsi":         rsi,        "rsi_signal":   rsi_signal,
        "macd":        macd,       "macd_signal_line": macd_sig,
        "macd_hist":   macd_hist,  "macd_signal":  macd_signal,
        "stoch_k":     stoch_k,    "stoch_d": stoch_d,
        "stoch_signal":stoch_signal,
        "bb_upper":    bb_upper,   "bb_lower": bb_lower,
        "bb_pct":      bb_pct,     "bb_signal": bb_signal,
        "vwap":        vwap,       "vwap_signal": vwap_signal,
        "atr":         atr,        "atr_pct": atr_pct,
        "rsi_spark":   rsi_spark,
        "macd_spark":  macd_spark,
        "close":       close,
    }


# ── 10) Relative Strength vs Benchmark ────────────────────────────────────────
def rs_vs_benchmark(stock_close: pd.Series,
                    bench_close: pd.Series,
                    periods: list[int] | None = None) -> dict:
    """
    Compare stock return vs benchmark (e.g. SPY) over multiple periods.
    Returns alpha (stock_return - bench_return) per period.
    """
    if periods is None:
        periods = [5, 21, 63, 126, 252]   # 1W 1M 3M 6M 1Y

    aligned = pd.concat([stock_close, bench_close], axis=1, join="inner")
    aligned.columns = ["stock", "bench"]

    result = {}
    for n in periods:
        if len(aligned) <= n:
            continue
        s_ret  = float(aligned["stock"].iloc[-1] / aligned["stock"].iloc[-1-n] - 1) * 100
        b_ret  = float(aligned["bench"].iloc[-1] / aligned["bench"].iloc[-1-n] - 1) * 100
        alpha  = round(s_ret - b_ret, 2)
        result[f"p{n}"] = {
            "stock_ret": round(s_ret, 2),
            "bench_ret": round(b_ret, 2),
            "alpha":     alpha,
            "outperform": alpha > 0,
        }

    # Rolling 63d RS ratio (outperformance trend)
    rel    = aligned["stock"] / aligned["bench"]
    rs_63  = rel.pct_change(63).tail(63)
    trend  = [round(float(v)*100, 2) for v in rs_63.tolist() if not np.isnan(v)]

    return {"periods": result, "rs_trend_63d": trend}


# ── 11) Sector Relative Strength ──────────────────────────────────────────────
# GICS sector → SPDR ETF mapping
SECTOR_ETF_MAP = {
    "Information Technology":  "XLK",
    "Financials":              "XLF",
    "Energy":                  "XLE",
    "Health Care":             "XLV",
    "Industrials":             "XLI",
    "Consumer Discretionary":  "XLY",
    "Consumer Staples":        "XLP",
    "Utilities":               "XLU",
    "Materials":               "XLB",
    "Communication Services":  "XLC",
    "Real Estate":             "IYR",
    # Broad fallback
    "Semiconductors":          "SMH",
    "Biotech":                 "XBI",
}

def sector_relative_strength(stock_close: pd.Series,
                              sector_close: pd.Series,
                              periods: list[int] | None = None) -> dict:
    """Compare stock vs its sector ETF."""
    return rs_vs_benchmark(stock_close, sector_close, periods)


# ── 12) Relative Rotation Graph (RRG) ────────────────────────────────────────
def compute_rrg(weekly_prices: pd.DataFrame, tickers: list[str], benchmark: str) -> dict:
    """
    Computes JdK RS-Ratio and JdK RS-Momentum for RRG plots.
    Args:
        weekly_prices (pd.DataFrame): DataFrame with weekly close prices for all assets.
        tickers (list[str]): List of tickers to analyze.
        benchmark (str): The benchmark ticker symbol.
    Returns:
        dict: A dictionary with RRG data for each ticker.
    """
    # ✨ NOTE: These constants need to be defined in `constants.py`
    from constants import RRG_SMOOTHING, RRG_ROC_SHIFT, RRG_TAIL_WEEKS

    results = {}
    bench_series = weekly_prices[benchmark]

    for ticker in tickers:
        if ticker not in weekly_prices.columns:
            continue

        asset_series = weekly_prices[ticker].dropna()
        if len(asset_series) < RRG_SMOOTHING + RRG_ROC_SHIFT:
            continue

        # 1. RS-Ratio
        rs_raw = (asset_series / bench_series).dropna()
        rs_ratio_smoothed = rs_raw.ewm(span=RRG_SMOOTHING, adjust=False).mean()

        # 2. RS-Momentum
        rs_momentum = rs_ratio_smoothed.pct_change(RRG_ROC_SHIFT)

        # 3. Normalize (JdK method)
        jrs = 100 + ((rs_ratio_smoothed / rs_ratio_smoothed.mean() - 1) * 10)
        jmo = 100 + (rs_momentum / rs_momentum.std()) * 10

        # Get latest values and tail
        latest_jrs = jrs.iloc[-1]
        latest_jmo = jmo.iloc[-1]

        tail_data = list(zip(jrs.tail(RRG_TAIL_WEEKS).tolist(), jmo.tail(RRG_TAIL_WEEKS).tolist()))

        # Determine quadrant
        if latest_jrs > 100 and latest_jmo > 100: quadrant = "Leading"
        elif latest_jrs > 100 and latest_jmo < 100: quadrant = "Weakening"
        elif latest_jrs < 100 and latest_jmo < 100: quadrant = "Lagging"
        else: quadrant = "Improving"

        results[ticker] = {
            "jrs": round(latest_jrs, 2),
            "jmo": round(latest_jmo, 2),
            "quadrant": quadrant,
            "tail": tail_data
        }
    return results
