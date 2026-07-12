# FILE: data_engine.py
import pandas as pd
import numpy as np
from constants import (
    TRADING_DAYS_MONTH, TRADING_DAYS_QUARTER, TRADING_DAYS_HALFYR, TRADING_DAYS_3QTR, TRADING_DAYS_YEAR,
    RS_BLEND_3M_WT, RS_BLEND_6M_WT, RS_BLEND_9M_WT, RS_BLEND_12M_WT,
    VDU_VOL_LOW, VDU_VOL_HIGH, BGU_GAP_PCT, BGU_VOL_MULT, W52_PROXIMITY, PPBP_VOL_LOOKBACK, CONFLUENCE_DAYS,
    RRG_SMOOTHING
)

# --- Core Price & Return Calculations ---

def pct_change(price_series: pd.Series, period: int = 1) -> float | None:
    if not isinstance(price_series, pd.Series) or len(price_series) < period + 1:
        return None
    start_price = price_series.iloc[-(period + 1)]
    end_price = price_series.iloc[-1]
    if start_price == 0:
        return 0.0
    change = (end_price / start_price - 1) * 100
    return round(float(change), 2) if np.isfinite(change) else None

def current_drawdown_from_peak(price_series: pd.Series) -> float:
    if not isinstance(price_series, pd.Series) or price_series.empty or len(price_series) < 2:
        return 0.0
    peak = price_series.cummax()
    drawdown = (price_series - peak) / peak.replace(0, np.nan)
    last_drawdown_pct = abs(drawdown.iloc[-1] * 100)
    return float(last_drawdown_pct) if np.isfinite(last_drawdown_pct) else 0.0

def max_drawdown(price_series: pd.Series) -> float:
    if not isinstance(price_series, pd.Series) or price_series.empty or len(price_series) < 2:
        return 0.0
    peak = price_series.cummax()
    drawdown = (price_series - peak) / peak.replace(0, np.nan)
    max_dd = abs(drawdown.min() * 100)
    return float(max_dd) if np.isfinite(max_dd) else 0.0

# --- Indicator Engine (Used by Pipeline & Technicals) ---

def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df['SMA50'] = df['Close'].rolling(window=50, min_periods=20).mean()
    df['SMA150'] = df['Close'].rolling(window=150, min_periods=50).mean()
    df['SMA200'] = df['Close'].rolling(window=200, min_periods=100).mean()
    df['VOL_SMA50'] = df['Volume'].rolling(window=50, min_periods=20).mean()
    df['HIGH_52W'] = df['High'].rolling(window=TRADING_DAYS_YEAR, min_periods=100).max()
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    df['EMA12'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['EMA26'] = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = df['EMA12'] - df['EMA26']
    df['MACD_SIGNAL'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_HIST'] = df['MACD'] - df['MACD_SIGNAL']
    low_14 = df['Low'].rolling(14).min()
    high_14 = df['High'].rolling(14).max()
    df['STOCH_K'] = (df['Close'] - low_14) * 100 / (high_14 - low_14).replace(0, np.nan)
    df['STOCH_D'] = df['STOCH_K'].rolling(3).mean()
    df['BB_MID'] = df['Close'].rolling(window=20).mean()
    std_dev = df['Close'].rolling(window=20).std()
    df['BB_UPPER'] = df['BB_MID'] + (std_dev * 2)
    df['BB_LOWER'] = df['BB_MID'] - (std_dev * 2)
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    df['VWAP'] = (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
    tr1 = abs(df['High'] - df['Low'])
    tr2 = abs(df['High'] - df['Close'].shift())
    tr3 = abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(window=14).mean()
    return df

# --- Scanner Engine ---

def run_scanners(df: pd.DataFrame) -> dict:
    if df.empty or len(df) < 51: return {}
    signals = {}
    last = df.iloc[-1]
    prev = df.iloc[-2]

    # VDU (Volume Dry-Up)
    is_vdu = last['Volume'] < (last['VOL_SMA50'] * VDU_VOL_HIGH) and \
             last['Volume'] > (last['VOL_SMA50'] * VDU_VOL_LOW) and \
             abs(last['Close'] - prev['Close']) / prev['Close'] < 0.015
    signals['VDU'] = pd.Series(is_vdu, index=df.index)

    # PPBP (Pocket Pivot Buy Point)
    up_days = df['Close'] > df['Close'].shift(1)
    down_day_volumes = df['Volume'][~up_days].tail(PPBP_VOL_LOOKBACK)
    is_ppbp = up_days.iloc[-1] and (down_day_volumes.empty or last['Volume'] > down_day_volumes.max())
    signals['PPBP'] = pd.Series(is_ppbp, index=df.index)

    # BGU (Buyable Gap-Up)
    gap_up_pct = (last['Open'] / prev['Close'] - 1) * 100
    is_bgu = gap_up_pct > BGU_GAP_PCT and last['Volume'] > (last['VOL_SMA50'] * BGU_VOL_MULT)
    signals['BGU'] = pd.Series(is_bgu, index=df.index)

    # Near 52W High
    is_near_52w = last['Close'] >= (last['HIGH_52W'] * W52_PROXIMITY) if pd.notna(last['HIGH_52W']) else False
    signals['52W'] = pd.Series(is_near_52w, index=df.index)

    return signals

def confluence_flags(signals: dict) -> tuple:
    rolled, conf, count = {}, None, None
    if not signals: return rolled, conf, count

    df = pd.DataFrame(signals).fillna(False)
    rolled_sum = df.rolling(window=CONFLUENCE_DAYS, min_periods=1).sum()

    rolled = {col: rolled_sum[col] > 0 for col in df.columns}
    count = rolled_sum.sum(axis=1)
    conf = count >= 2

    return rolled, conf, count

# --- RS Rating Engine ---

def _get_market_groups(ticker_meta: dict) -> dict[str, list[str]]:
    market_groups = {}
    for ticker, meta in ticker_meta.items():
        market = meta.get("market")
        if market:
            market_groups.setdefault(market, []).append(ticker)
    return market_groups

def rs_rating_per_market(combined: dict, ticker_meta: dict) -> pd.Series:
    market_groups = _get_market_groups(ticker_meta)
    all_rs_ratings = {}

    for market, tickers in market_groups.items():
        blended_returns = {}
        for ticker in tickers:
            if ticker in combined and len(combined[ticker]) > TRADING_DAYS_YEAR:
                close = combined[ticker]['Close']
                r3m = pct_change(close, TRADING_DAYS_QUARTER) or 0
                r6m = pct_change(close, TRADING_DAYS_HALFYR) or 0
                r9m = pct_change(close, TRADING_DAYS_3QTR) or 0
                r12m = pct_change(close, TRADING_DAYS_YEAR) or 0
                blend = (r3m * RS_BLEND_3M_WT) + (r6m * RS_BLEND_6M_WT) + \
                        (r9m * RS_BLEND_9M_WT) + (r12m * RS_BLEND_12M_WT)
                blended_returns[ticker] = blend

        if blended_returns:
            s = pd.Series(blended_returns)
            ranks = s.rank(pct=True, method="average")
            ratings = (ranks * 98 + 1).fillna(1).astype(int)
            all_rs_ratings.update(ratings.to_dict())

    return pd.Series(all_rs_ratings)

def rs_rating_table(close_df: pd.DataFrame, period: int) -> pd.Series:
    if close_df.empty or len(close_df) < period + 1:
        return pd.Series(dtype=float)

    returns = (close_df.iloc[-1] / close_df.iloc[-1 - period] - 1) * 100
    ranks = returns.rank(pct=True, method="average").fillna(0)
    ratings = (ranks * 98 + 1).fillna(1).astype(int)
    return ratings

# --- RRG Engine ---

def calculate_rrg_metrics(
    df_weekly: pd.DataFrame, tickers: list[str], benchmark_ticker: str,
    period: int = 10, tail_length: int = 12
) -> dict:
    results = {}
    if benchmark_ticker not in df_weekly.columns: return results
    benchmark_prices = df_weekly[benchmark_ticker].ffill()

    rs_ratio_df = df_weekly[tickers].div(benchmark_prices, axis=0).dropna(axis=1, how='all')
    valid_tickers = [t for t in tickers if t in rs_ratio_df.columns and rs_ratio_df[t].notna().sum() >= RRG_SMOOTHING]
    if not valid_tickers: return results

    rs_ratio_df = rs_ratio_df[valid_tickers]
    jrs_val = 100 + ((rs_ratio_df / rs_ratio_df.rolling(RRG_SMOOTHING).mean() - 1) * 10)

    rs_mom_val = rs_ratio_df.pct_change(periods=period)
    rs_mom_std = rs_mom_val.rolling(RRG_SMOOTHING).std()
    jmo_val = 100 + ((rs_mom_val / rs_mom_std.replace(0, np.nan) - 1) * 10)

    for ticker in valid_tickers:
        jrs, jmo = jrs_val[ticker].iloc[-1], jmo_val[ticker].iloc[-1]
        if not (np.isfinite(jrs) and np.isfinite(jmo)): continue

        if jrs > 100 and jmo > 100: quadrant = "Leading"
        elif jrs > 100 and jmo <= 100: quadrant = "Weakening"
        elif jrs < 100 and jmo <= 100: quadrant = "Lagging"
        else: quadrant = "Improving"

        tail_jrs = jrs_val[ticker].dropna().tail(tail_length).tolist()
        tail_jmo = jmo_val[ticker].dropna().tail(tail_length).tolist()
        tail_data = list(zip(tail_jrs, tail_jmo))

        results[ticker] = {"jrs": round(jrs, 2), "jmo": round(jmo, 2), "quadrant": quadrant, "tail": tail_data}
    return results

# --- Thematic Matrix Engine ---

def theme_returns(close_df: pd.DataFrame, theme_map: dict, ticker_meta: dict, rs_now: pd.Series):
    theme_data = {}
    for theme, tickers in theme_map.items():
        if not tickers: continue

        theme_closes = close_df[tickers]
        if theme_closes.empty: continue

        r1d = (theme_closes.iloc[-1] / theme_closes.iloc[-2] - 1) * 100 if len(theme_closes) > 1 else pd.Series(0, index=tickers)
        r1m = (theme_closes.iloc[-1] / theme_closes.iloc[-TRADING_DAYS_MONTH] - 1) * 100 if len(theme_closes) > TRADING_DAYS_MONTH else pd.Series(0, index=tickers)
        r3m = (theme_closes.iloc[-1] / theme_closes.iloc[-TRADING_DAYS_QUARTER] - 1) * 100 if len(theme_closes) > TRADING_DAYS_QUARTER else pd.Series(0, index=tickers)

        members = [ticker_meta[t] for t in tickers]
        for m, t in zip(members, tickers):
            m.update({'ticker': t, 'r1m': round(r1m.get(t, 0), 2)})

        top_tickers = sorted(tickers, key=lambda t: rs_now.get(t, 0), reverse=True)

        theme_data[theme] = {
            'count': len(tickers),
            'r1d': round(r1d.mean(), 2),
            'r1m': round(r1m.mean(), 2),
            'r3m': round(r3m.mean(), 2),
            'avg_rs': int(rs_now[tickers].mean()) if not rs_now[tickers].empty else 0,
            'top_tickers': [t.split('.')[0] for t in top_tickers[:4]],
            'members': sorted(members, key=lambda x: rs_now.get(x['ticker'], 0), reverse=True)[:30]
        }
    return pd.DataFrame.from_dict(theme_data, orient='index').reset_index().rename(columns={'index': 'theme'})

# --- Correlation Matrix Engine ---

def compute_correlation_matrix(data: dict, tickers: list, days: int) -> dict:
    close_df = pd.DataFrame({t: df['Close'] for t, df in data.items() if df is not None and not df.empty}).tail(days + 1)
    if len(close_df) < days + 1:
        return {"ok": False, "error": f"Not enough data ({len(close_df)} days) for correlation"}

    returns = close_df.pct_change().dropna()
    corr_matrix = returns.corr()

    # Get final valid tickers from the correlation matrix itself
    final_tickers = corr_matrix.columns.tolist()

    matrix_list = []
    for i in range(len(final_tickers)):
        row = []
        for j in range(len(final_tickers)):
            val = corr_matrix.iloc[i, j]
            row.append(round(float(val), 3) if pd.notna(val) else None)
        matrix_list.append(row)

    return {"ok": True, "labels": final_tickers, "matrix": matrix_list}

# --- Technical Analysis Engine (for Stock Deep Dive) ---

def tech_snapshot(df: pd.DataFrame):
    last = df.iloc[-1]
    rsi, rsi_sig = last['RSI'], "Neutral"
    if rsi > 70: rsi_sig = "Overbought"
    elif rsi < 30: rsi_sig = "Oversold"

    macd_hist, macd_sig = last['MACD_HIST'], "Neutral"
    if macd_hist > 0 and df['MACD_HIST'].iloc[-2] < 0: macd_sig = "Bullish Crossover"
    elif macd_hist < 0 and df['MACD_HIST'].iloc[-2] > 0: macd_sig = "Bearish Crossover"

    stoch_k, stoch_sig = last['STOCH_K'], "Neutral"
    if stoch_k > 80: stoch_sig = "Overbought"
    elif stoch_k < 20: stoch_sig = "Oversold"

    bb_pct = (last['Close'] - last['BB_LOWER']) / (last['BB_UPPER'] - last['BB_LOWER']) * 100 if last['BB_UPPER'] != last['BB_LOWER'] else 50
    bb_sig = "Inside Bands"
    if bb_pct > 100: bb_sig = "Above Upper Band"
    elif bb_pct < 0: bb_sig = "Below Lower Band"

    vwap, vwap_sig = last['VWAP'], "Price is Above VWAP" if last['Close'] > last['VWAP'] else "Price is Below VWAP"
    atr_pct = (last['ATR'] / last['Close'] * 100) if last['Close'] > 0 else 0

    return {
        "rsi": rsi, "rsi_signal": rsi_sig, "rsi_spark": df['RSI'].tail(30).tolist(),
        "macd_hist": macd_hist, "macd_signal": macd_sig, "macd_spark": df['MACD_HIST'].tail(30).tolist(),
        "stoch_k": stoch_k, "stoch_signal": stoch_sig,
        "bb_pct": bb_pct, "bb_signal": bb_sig,
        "vwap": vwap, "vwap_signal": vwap_sig,
        "atr": last['ATR'], "atr_pct": round(atr_pct, 1)
    }

def rs_vs_benchmark(stock_close: pd.Series, bench_close: pd.Series):
    results = {}
    periods = {'p5': 5, 'p21': 21, 'p63': 63, 'p126': 126, 'p252': 252}
    for key, p in periods.items():
        stock_ret = pct_change(stock_close, p)
        bench_ret = pct_change(bench_close, p)
        if stock_ret is not None and bench_ret is not None:
            alpha = stock_ret - bench_ret
            results[key] = {
                'stock_ret': stock_ret, 'bench_ret': bench_ret, 'alpha': round(alpha, 2),
                'outperform': stock_ret > bench_ret
            }
    return {'periods': results}

def sector_relative_strength(stock_close: pd.Series, sector_close: pd.Series):
    return rs_vs_benchmark(stock_close, sector_close)
