# FILE: data_engine.py
import pandas as pd
import numpy as np
from constants import (
    TRADING_DAYS_MONTH, TRADING_DAYS_QUARTER, TRADING_DAYS_HALFYR, TRADING_DAYS_3QTR, TRADING_DAYS_YEAR,
    RS_BLEND_3M_WT, RS_BLEND_6M_WT, RS_BLEND_9M_WT, RS_BLEND_12M_WT,
    VDU_VOL_LOW, VDU_VOL_HIGH, BGU_GAP_PCT, BGU_VOL_MULT, W52_PROXIMITY, PPBP_VOL_LOOKBACK, CONFLUENCE_DAYS,
    RRG_SMOOTHING, RRG_ROLL_MIN, RRG_TAIL_WEEKS, RRG_ROC_SHIFT, RRG_CLAMP_LO, RRG_CLAMP_HI,
)

# --- Core Price & Return Calculations ---

def as_close(price_series) -> pd.Series | None:
    if price_series is None:
        return None
    if isinstance(price_series, pd.DataFrame):
        if price_series.shape[1] < 1:
            return None
        price_series = price_series.iloc[:, 0]
    if not isinstance(price_series, pd.Series):
        try:
            price_series = pd.Series(price_series)
        except Exception:
            return None
    try:
        return pd.to_numeric(price_series, errors="coerce")
    except Exception:
        return price_series


def pct_change(price_series: pd.Series, period: int = 1) -> float | None:
    price_series = as_close(price_series)
    if price_series is None or len(price_series) < period + 1:
        return None
    start_price = price_series.iloc[-(period + 1)]
    end_price = price_series.iloc[-1]
    if start_price is None or end_price is None or pd.isna(start_price) or pd.isna(end_price):
        return None
    if start_price == 0:
        return None
    change = (end_price / start_price - 1) * 100
    return round(float(change), 2) if np.isfinite(change) else None

def current_drawdown_from_peak(price_series: pd.Series) -> float:
    price_series = as_close(price_series)
    if price_series is None or price_series.empty or len(price_series) < 2:
        return 0.0
    peak = price_series.cummax()
    # ✨ FIX: ใช้ np.where เพื่อป้องกันการหารด้วยศูนย์อย่างสมบูรณ์ ทำให้ Logic Robust ขึ้น
    # ถ้า peak เป็น 0 จะหารด้วย 1 แทน ซึ่ง (price - peak) จะเป็น 0 อยู่แล้ว ผลลัพธ์จึงถูกต้อง
    drawdown = (price_series - peak) / np.where(peak == 0, 1, peak)
    last_drawdown_pct = abs(drawdown.iloc[-1] * 100)
    return float(last_drawdown_pct) if np.isfinite(last_drawdown_pct) else 0.0

def max_drawdown(price_series: pd.Series) -> float:
    price_series = as_close(price_series)
    if price_series is None or price_series.empty or len(price_series) < 2:
        return 0.0
    peak = price_series.cummax()
    # ✨ FIX: ใช้ np.where เช่นเดียวกับฟังก์ชันข้างบนเพื่อความสอดคล้องกันและความปลอดภัย
    drawdown = (price_series - peak) / np.where(peak == 0, 1, peak)
    max_dd = abs(drawdown.min() * 100)
    return float(max_dd) if np.isfinite(max_dd) else 0.0

# --- Indicator Engine (Used by Pipeline & Technicals) ---

def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    need = {"Open", "High", "Low", "Close", "Volume"}
    missing = need - set(map(str, df.columns))
    if missing:
        for col in missing:
            df[col] = 0
    if isinstance(df["Close"], pd.DataFrame):
        df["Close"] = df["Close"].iloc[:, 0]
    df['SMA50'] = df['Close'].rolling(window=50, min_periods=20).mean()
    df['SMA150'] = df['Close'].rolling(window=150, min_periods=50).mean()
    df['SMA200'] = df['Close'].rolling(window=200, min_periods=100).mean()
    df['VOL_SMA50'] = df['Volume'].rolling(window=50, min_periods=20).mean()
    df['HIGH_52W'] = df['High'].rolling(window=TRADING_DAYS_YEAR, min_periods=100).max()

    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50)

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
    idx = pd.to_datetime(df.index, utc=True, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    day_key = pd.DatetimeIndex(idx).normalize()
    tp_vol = typical_price * df['Volume']
    day_tp = tp_vol.groupby(day_key).cumsum()
    day_vol = df['Volume'].groupby(day_key).cumsum()
    df['VWAP'] = day_tp / day_vol.replace(0, np.nan)
    df['VWAP'] = df['VWAP'].ffill()
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

    # ✨ FIX: ปรับการคำนวณทั้งหมดเป็นแบบ Vectorized (Series-based) เพื่อให้คำนวณ Signal ได้ทุกวัน ไม่ใช่แค่วันสุดท้าย
    # ซึ่งเป็นพฤติกรรมที่ถูกต้องสำหรับ `confluence_flags`

    # VDU (Volume Dry-Up)
    is_vdu = (df['Volume'] < (df['VOL_SMA50'] * VDU_VOL_HIGH)) & \
             (df['Volume'] > (df['VOL_SMA50'] * VDU_VOL_LOW)) & \
             (abs(df['Close'].diff() / df['Close'].shift()) < 0.015)
    signals['VDU'] = is_vdu

    # PPBP (Pocket Pivot Buy Point) - Logic ที่รัดกุมและถูกต้อง
    # 1. ต้องเป็นวันบวก
    is_up_day = df['Close'] > df['Close'].shift(1)
    # 2. ต้องอยู่ในเทรนด์ขาขึ้น (ราคาอยู่เหนือ SMA50)
    is_in_uptrend = df['Close'] > df['SMA50']
    # 3. Volume ต้องมากกว่า Volume สูงสุดของ 'วันลบ' ใน 10 วันที่ผ่านมา
    down_day_volume = df['Volume'].where(~is_up_day, 0)
    max_down_vol_lookback = down_day_volume.rolling(window=PPBP_VOL_LOOKBACK, min_periods=1).max()
    is_volume_spike = df['Volume'] > max_down_vol_lookback.shift(1) # shift(1) เพื่อไม่ให้รวม vol วันปัจจุบัน

    signals['PPBP'] = is_up_day & is_in_uptrend & is_volume_spike

    # BGU (Buyable Gap-Up)
    gap_up_pct = (df['Open'] / df['Close'].shift(1) - 1) * 100
    is_bgu = (gap_up_pct > BGU_GAP_PCT) & (df['Volume'] > (df['VOL_SMA50'] * BGU_VOL_MULT))
    signals['BGU'] = is_bgu

    # Near 52W High
    is_near_52w = df['Close'] >= (df['HIGH_52W'] * W52_PROXIMITY)
    signals['52W'] = is_near_52w

    signals['VCP'] = vcp_signal_series(df)

    return signals


def _vcp_frame(df: pd.DataFrame) -> dict | None:
    """เกณฑ์ชุดเดียวสำหรับทั้งธงรายวันและคะแนนรายตัว."""
    if df is None or len(df) < 30 or "Volume" not in df.columns:
        return None
    vol = df["Volume"]
    sma = df["VOL_SMA50"] if "VOL_SMA50" in df.columns else vol.rolling(50, min_periods=10).mean()
    close = df["Close"]
    dry = vol < (sma * VDU_VOL_HIGH)
    wet_groups = (~dry.fillna(False)).cumsum()
    days_dry = dry.groupby(wet_groups).cumsum()
    dry_ratio = vol / sma.replace(0, np.nan) * 100
    peak60 = df["High"].rolling(60, min_periods=20).max()
    off_peak = (close / peak60 - 1) * 100
    lo20 = close.rolling(20, min_periods=10).min()
    hi20 = close.rolling(20, min_periods=10).max()
    tight = (hi20 - lo20) / lo20.replace(0, np.nan) * 100
    def _band(s, rules):
        out = pd.Series(0, index=df.index, dtype="float64")
        s = s.reindex(df.index)
        for cond, pts in rules:
            out = out.where(~cond.reindex(df.index).fillna(False), pts)
        return out
    score = (
        _band(days_dry, [(days_dry >= 10, 30), (days_dry >= 5, 18), (days_dry >= 3, 8)])
        + _band(dry_ratio, [(dry_ratio < 20, 25), (dry_ratio < 50, 16), (dry_ratio < 80, 8)])
        + _band(off_peak.abs(), [(off_peak.abs() <= 5, 25), (off_peak.abs() <= 12, 15), (off_peak.abs() <= 20, 8)])
        + _band(tight, [(tight <= 10, 20), (tight <= 18, 10)])
    ).clip(upper=99)
    is_vcp = (score >= 40) & (days_dry >= 5)
    return {
        "days_dry": days_dry,
        "dry_ratio": dry_ratio,
        "off_peak": off_peak,
        "tight": tight,
        "peak": peak60,
        "score": score,
        "is_vcp": is_vcp.fillna(False),
    }


def vcp_signal_series(df: pd.DataFrame) -> pd.Series:
    frame = _vcp_frame(df)
    if frame is None:
        return pd.Series(False, index=getattr(df, "index", None))
    return frame["is_vcp"]


def vcp_metrics(df: pd.DataFrame) -> dict:
    empty = {
        "vcp_ready": 0, "vcp_days_dry": 0, "vcp_dry_ratio": None,
        "vcp_off_peak": None, "vcp_target": None, "vcp_zone": "—", "is_vcp": False,
    }
    frame = _vcp_frame(df)
    if frame is None:
        return empty
    def _as_int(v, default=0):
        try:
            if v is None or pd.isna(v):
                return default
            f = float(v)
            if not np.isfinite(f):
                return default
            return int(f)
        except (TypeError, ValueError):
            return default

    days = _as_int(frame["days_dry"].iloc[-1], 0)
    dry_ratio = frame["dry_ratio"].iloc[-1]
    off_peak = frame["off_peak"].iloc[-1]
    score = _as_int(frame["score"].iloc[-1], 0)
    peak = frame["peak"].iloc[-1]
    dry_f = None if pd.isna(dry_ratio) else float(dry_ratio)
    if days >= 10 and (dry_f or 100) < 15:
        zone = "EXTREME DRY"
    elif days >= 5 and (dry_f or 100) < 50:
        zone = "DRY"
    else:
        zone = "WATCH"
    return {
        "vcp_ready": score,
        "vcp_days_dry": days,
        "vcp_dry_ratio": None if dry_f is None else round(dry_f, 1),
        "vcp_off_peak": None if pd.isna(off_peak) else round(float(off_peak), 1),
        "vcp_target": None if pd.isna(peak) else round(float(peak), 2),
        "vcp_zone": zone,
        "is_vcp": bool(frame["is_vcp"].iloc[-1]),
    }

# ... ส่วนที่เหลือของไฟล์ไม่มีการเปลี่ยนแปลง และทำงานร่วมกับส่วนที่แก้ไขได้อย่างถูกต้อง ...

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

def rs_rating_asof(combined: dict, ticker_meta: dict, lag_days: int = 0) -> pd.Series:
    """RS สูตรเดียวกับปัจจุบัน ถ้า lag_days > 0 ให้ตัดแท่งท้ายออกแล้วนับใหม่."""
    if not lag_days:
        return rs_rating_per_market(combined, ticker_meta)
    lagged = {}
    for ticker, df in (combined or {}).items():
        if df is None or len(df) <= lag_days + 40:
            continue
        lagged[ticker] = df.iloc[:-int(lag_days)]
    return rs_rating_per_market(lagged, ticker_meta)


def rs_rating_per_market(combined: dict, ticker_meta: dict) -> pd.Series:
    market_groups = _get_market_groups(ticker_meta)
    all_rs_ratings = {}
    market_blends = {}
    all_blends = {}

    for market, tickers in market_groups.items():
        blended_returns = {}
        for ticker in tickers:
            if ticker not in combined:
                continue
            close = as_close(combined[ticker]["Close"])
            if close is None:
                continue
            n = len(close)
            if n < 40:
                continue
            parts, wts = [], []
            r3m = pct_change(close, TRADING_DAYS_QUARTER)
            r6m = pct_change(close, TRADING_DAYS_HALFYR)
            r9m = pct_change(close, TRADING_DAYS_3QTR)
            r12m = pct_change(close, TRADING_DAYS_YEAR)
            if r3m is not None:
                parts.append(r3m); wts.append(RS_BLEND_3M_WT)
            if r6m is not None:
                parts.append(r6m); wts.append(RS_BLEND_6M_WT)
            if r9m is not None:
                parts.append(r9m); wts.append(RS_BLEND_9M_WT)
            if r12m is not None:
                parts.append(r12m); wts.append(RS_BLEND_12M_WT)
            if not parts:
                whole = pct_change(close, max(1, n - 1))
                if whole is None:
                    continue
                parts, wts = [whole], [1.0]
            wsum = sum(wts) or 1.0
            blended_returns[ticker] = sum(p * w / wsum for p, w in zip(parts, wts))

        if blended_returns:
            market_blends[market] = blended_returns
            all_blends.update(blended_returns)

    if not all_blends:
        return pd.Series(dtype=float)
    global_ranks = pd.Series(all_blends).rank(pct=True, method="average")
    for market, blended_returns in market_blends.items():
        s = pd.Series(blended_returns)
        if len(s) < 8:
            ranks = global_ranks.reindex(s.index)
        else:
            ranks = s.rank(pct=True, method="average")
        ratings = (ranks * 98 + 1).fillna(50).astype(int)
        all_rs_ratings.update(ratings.to_dict())

    return pd.Series(all_rs_ratings)

def rs_rating_table(close_df: pd.DataFrame, period: int) -> pd.Series:
    if close_df.empty or len(close_df) < period + 1:
        return pd.Series(dtype=float)

    returns = (close_df.iloc[-1] / close_df.iloc[-1 - period] - 1) * 100
    valid = returns.dropna()
    if len(valid) < 8:
        ranks = valid.rank(pct=True, method="average").fillna(0.5)
        ratings = (ranks * 40 + 30).fillna(50).astype(int)
        return ratings.reindex(returns.index)
    ranks = returns.rank(pct=True, method="average").fillna(0)
    ratings = (ranks * 98 + 1).fillna(50).astype(int)
    return ratings

# --- RRG Engine ---

def _classify_quadrant(jrs: float, jmo: float) -> str:
    if jrs > 100 and jmo > 100: return "Leading"
    if jrs > 100 and jmo <= 100: return "Weakening"
    if jrs < 100 and jmo <= 100: return "Lagging"
    return "Improving"


def _quadrant_persistence(quadrant_series: list[str]) -> int:
    """นับจำนวนสัปดาห์ติดต่อกัน (จากล่าสุดย้อนหลัง) ที่ยังอยู่ quadrant เดิม."""
    if not quadrant_series:
        return 0
    current = quadrant_series[-1]
    streak = 0
    for q in reversed(quadrant_series):
        if q != current:
            break
        streak += 1
    return streak


def calculate_rrg_metrics(
    df_weekly: pd.DataFrame, tickers: list[str], benchmark_ticker: str,
    period: int = RRG_ROC_SHIFT, tail_length: int = RRG_TAIL_WEEKS
) -> dict:
    results = {}
    if benchmark_ticker not in df_weekly.columns: return results
    benchmark_prices = df_weekly[benchmark_ticker].ffill()

    rs_ratio_df = df_weekly[tickers].div(benchmark_prices, axis=0).dropna(axis=1, how='all')
    valid_tickers = [t for t in tickers if t in rs_ratio_df.columns and rs_ratio_df[t].notna().sum() >= RRG_SMOOTHING]
    if not valid_tickers: return results

    rs_ratio_df = rs_ratio_df[valid_tickers]
    # ✨ FIX: เพิ่ม min_periods=RRG_ROLL_MIN — เดิมไม่ใส่ ทำให้ต้องรอครบ RRG_SMOOTHING (14)
    # แท่งเต็มก่อนถึงจะมีค่า ตอนนี้เริ่มมีค่าได้ตั้งแต่ RRG_ROLL_MIN (10) แท่ง
    jrs_val = 100 + ((rs_ratio_df / rs_ratio_df.rolling(RRG_SMOOTHING, min_periods=RRG_ROLL_MIN).mean() - 1) * 10)

    rs_mom_val = rs_ratio_df.pct_change(periods=period)
    rs_mom_std = rs_mom_val.rolling(RRG_SMOOTHING, min_periods=RRG_ROLL_MIN).std()
    jmo_val = 100 + ((rs_mom_val / rs_mom_std.replace(0, np.nan) - 1) * 10)

    for ticker in valid_tickers:
        jrs, jmo = jrs_val[ticker].iloc[-1], jmo_val[ticker].iloc[-1]
        if not (np.isfinite(jrs) and np.isfinite(jmo)): continue

        quadrant = _classify_quadrant(jrs, jmo)

        tail_jrs = jrs_val[ticker].dropna().tail(tail_length).tolist()
        tail_jmo = jmo_val[ticker].dropna().tail(tail_length).tolist()
        tail_data = list(zip(tail_jrs, tail_jmo))

        # ✨ NEW: theme persistence — จัด quadrant ให้ทุกจุดใน tail แล้วนับสัปดาห์
        # ติดต่อกันที่ยังอยู่ quadrant เดิม (นับจากล่าสุดย้อนหลัง)
        quadrant_history = [_classify_quadrant(jr, jm) for jr, jm in tail_data]
        persistence_weeks = _quadrant_persistence(quadrant_history)

        results[ticker] = {
            "jrs": round(jrs, 2), "jmo": round(jmo, 2), "quadrant": quadrant, "tail": tail_data,
            "quadrant_history": quadrant_history,
            "persistence_weeks": persistence_weeks,
        }
    return results

# --- Thematic Matrix Engine ---

def theme_returns(close_df: pd.DataFrame, theme_map: dict, ticker_meta: dict, rs_now: pd.Series):
    theme_data = {}
    for theme, tickers in theme_map.items():
        if not tickers: continue

        valid_tickers = [t for t in tickers if t in close_df.columns]
        if not valid_tickers: continue

        theme_closes = close_df[valid_tickers]
        if theme_closes.empty: continue

        def _leg(n):
            rows = int(getattr(theme_closes, "shape", (0, 0))[0])
            if rows < int(n) + 1:
                return pd.Series(dtype=float)
            try:
                prev = theme_closes.iloc[-(int(n) + 1)].replace(0, np.nan)
                last = theme_closes.iloc[-1]
                return (last / prev - 1) * 100
            except Exception:
                return pd.Series(dtype=float)

        r1d = _leg(1)
        r1m = _leg(TRADING_DAYS_MONTH)
        r3m = _leg(TRADING_DAYS_QUARTER)

        def _avg(s):
            s = s.dropna() if hasattr(s, "dropna") else s
            if s is None or getattr(s, "empty", True):
                return None
            v = float(s.mean())
            return None if not np.isfinite(v) else round(v, 2)

        members = []
        for t in valid_tickers:
            src = ticker_meta.get(t) or {}
            raw_m = r1m.get(t) if hasattr(r1m, "get") else None
            members.append({
                **src,
                "ticker": t,
                "r1m": None if raw_m is None or not np.isfinite(raw_m) else round(float(raw_m), 2),
            })

        def _rs_key(t):
            v = rs_now.get(t) if hasattr(rs_now, "get") else None
            return v if v is not None and v == v else -1

        top_tickers = sorted(valid_tickers, key=_rs_key, reverse=True)
        valid_rs = rs_now.reindex(valid_tickers).dropna() if hasattr(rs_now, "reindex") else pd.Series(dtype=float)

        theme_data[theme] = {
            'count': len(valid_tickers),
            'r1d': _avg(r1d),
            'r1m': _avg(r1m),
            'r3m': _avg(r3m),
            'avg_rs': int(valid_rs.mean()) if not valid_rs.empty else None,
            'top_tickers': [t.split('.')[0] for t in top_tickers[:4]],
            'members': sorted(members, key=lambda x: x["r1m"] if x.get("r1m") is not None else -999, reverse=True)[:30]
        }
    return pd.DataFrame.from_dict(theme_data, orient='index').reset_index().rename(columns={'index': 'theme'})

# --- Correlation Matrix Engine ---

def compute_correlation_matrix(data: dict, tickers: list, days: int) -> dict:
    close_df = pd.DataFrame({t: df['Close'] for t, df in data.items() if df is not None and not df.empty}).tail(days + 1)
    if len(close_df) < days + 1:
        return {"ok": False, "error": f"Not enough data ({len(close_df)} days) for correlation"}

    try:
        returns = close_df.pct_change(fill_method=None)
    except TypeError:
        returns = close_df.pct_change()
    corr_matrix = returns.corr(min_periods=max(10, days // 3))

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

def _last_num(df, col):
    if df is None or col not in getattr(df, "columns", []):
        return None
    s = df[col]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    try:
        v = s.iloc[-1]
    except Exception:
        return None
    if isinstance(v, (pd.Series, pd.Index)):
        try:
            v = v.iloc[0] if len(v) else None
        except Exception:
            return None
    try:
        if hasattr(v, "item"):
            v = v.item()
    except Exception:
        pass
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except Exception:
        return None


def _tail_nums(df, col, n=30):
    if df is None or col not in getattr(df, "columns", []):
        return []
    s = df[col]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    out = []
    for v in s.tail(n).tolist():
        try:
            f = float(v)
            out.append(None if not np.isfinite(f) else round(f, 4))
        except Exception:
            out.append(None)
    return out


def tech_snapshot(df: pd.DataFrame):
    if df is None or getattr(df, "empty", True):
        return {}
    close_s = as_close(df["Close"]) if "Close" in df.columns else None
    rsi = _last_num(df, "RSI")
    rsi_sig = "N/A" if rsi is None else ("Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral")
    macd_hist = _last_num(df, "MACD_HIST")
    macd_prev = None
    if "MACD_HIST" in df.columns and len(df) > 1:
        s = df["MACD_HIST"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        try:
            v = float(s.iloc[-2])
            macd_prev = v if np.isfinite(v) else None
        except Exception:
            macd_prev = None
    macd_sig = "N/A" if macd_hist is None else "Neutral"
    if macd_hist is not None and macd_prev is not None:
        if macd_hist > 0 and macd_prev <= 0:
            macd_sig = "Bullish Crossover"
        elif macd_hist < 0 and macd_prev >= 0:
            macd_sig = "Bearish Crossover"
    stoch_k = _last_num(df, "STOCH_K")
    stoch_sig = "N/A" if stoch_k is None else ("Overbought" if stoch_k > 80 else "Oversold" if stoch_k < 20 else "Neutral")
    bb_upper = _last_num(df, "BB_UPPER")
    bb_lower = _last_num(df, "BB_LOWER")
    last_px = None
    prev_px = None
    if close_s is not None and len(close_s.dropna()):
        cs = close_s.dropna()
        last_px = float(cs.iloc[-1])
        if len(cs) > 1:
            prev_px = float(cs.iloc[-2])
    bb_pct = None
    if last_px is not None and bb_upper is not None and bb_lower is not None and bb_upper != bb_lower:
        bb_pct = (last_px - bb_lower) / (bb_upper - bb_lower) * 100
        if not np.isfinite(bb_pct):
            bb_pct = None
    bb_sig = "Inside Bands"
    if bb_pct is not None:
        if bb_pct > 100:
            bb_sig = "Above Upper Band"
        elif bb_pct < 0:
            bb_sig = "Below Lower Band"
    vwap = _last_num(df, "VWAP")
    vwap_sig = "VWAP unavailable (no volume)" if vwap is None else "N/A"
    if vwap is not None and last_px is not None:
        if abs(last_px - vwap) < 1e-9:
            vwap_sig = "VWAP equals last print"
        else:
            vwap_sig = "Price is Above VWAP" if last_px > vwap else "Price is Below VWAP"
    atr_val = _last_num(df, "ATR")
    atr_pct = None
    if atr_val is not None and last_px and last_px > 0:
        atr_pct = atr_val / last_px * 100
    change_pct = None
    if last_px is not None and prev_px:
        change_pct = round((last_px / prev_px - 1) * 100, 2)
    return {
        "rsi": None if rsi is None else round(float(rsi), 2),
        "rsi_signal": rsi_sig,
        "rsi_spark": _tail_nums(df, "RSI"),
        "macd_hist": None if macd_hist is None else round(float(macd_hist), 4),
        "macd_signal": macd_sig,
        "macd_spark": _tail_nums(df, "MACD_HIST"),
        "stoch_k": None if stoch_k is None else round(float(stoch_k), 2),
        "stoch_signal": stoch_sig,
        "bb_pct": None if bb_pct is None else round(float(bb_pct), 1),
        "bb_signal": bb_sig,
        "vwap": None if vwap is None else round(float(vwap), 4 if abs(vwap) < 1 else 2),
        "vwap_signal": vwap_sig,
        "atr": None if atr_val is None else round(float(atr_val), 4 if atr_val < 1 else 2),
        "atr_pct": None if atr_pct is None else round(float(atr_pct), 1),
        "price": None if last_px is None else round(last_px, 4 if last_px < 1 else 2),
        "prev_close": None if prev_px is None else round(prev_px, 4 if prev_px < 1 else 2),
        "change_pct": change_pct,
    }



def _px(v: float) -> float:
    if v is None or not np.isfinite(v):
        return None
    return round(float(v), 4 if abs(v) < 1 else 2)


def pivot_levels(high: float, low: float, close: float) -> dict:
    h, l, c = float(high), float(low), float(close)
    pp = (h + l + c) / 3.0
    rng = h - l
    classic = {
        "R3": _px(h + 2 * (pp - l)),
        "R2": _px(pp + rng),
        "R1": _px(2 * pp - l),
        "PP": _px(pp),
        "S1": _px(2 * pp - h),
        "S2": _px(pp - rng),
        "S3": _px(l - 2 * (h - pp)),
    }
    fib = {
        "R3": _px(pp + 1.000 * rng),
        "R2": _px(pp + 0.618 * rng),
        "R1": _px(pp + 0.382 * rng),
        "PP": _px(pp),
        "S1": _px(pp - 0.382 * rng),
        "S2": _px(pp - 0.618 * rng),
        "S3": _px(pp - 1.000 * rng),
    }
    return {"classic": classic, "fibonacci": fib}


def pivot_pack(df: pd.DataFrame, hourly: pd.DataFrame | None = None) -> dict:
    empty = {"daily": None, "weekly": None, "monthly": None, "h4": None}
    if df is None or len(df) < 5 or not {"High", "Low", "Close"}.issubset(df.columns):
        return empty

    def _completed(rule: str):
        bars = df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(bars) < 2:
            return None
        prev = bars.iloc[-2]
        src = bars.index[-2]
        lv = pivot_levels(prev["High"], prev["Low"], prev["Close"])
        lv["source"] = str(pd.Timestamp(src).date())
        lv["high"] = _px(float(prev["High"]))
        lv["low"] = _px(float(prev["Low"]))
        lv["close"] = _px(float(prev["Close"]))
        return lv

    daily = None
    if len(df) >= 2:
        prev = df.iloc[-2]
        daily = pivot_levels(prev["High"], prev["Low"], prev["Close"])
        daily["source"] = str(pd.Timestamp(df.index[-2]).date())
        daily["high"] = _px(float(prev["High"]))
        daily["low"] = _px(float(prev["Low"]))
        daily["close"] = _px(float(prev["Close"]))
    h4 = None
    if hourly is not None and len(hourly) >= 3 and {"High", "Low", "Close"}.issubset(hourly.columns):
        bars = hourly.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(bars) >= 2:
            prev = bars.iloc[-2]
            h4 = pivot_levels(prev["High"], prev["Low"], prev["Close"])
            h4["source"] = str(pd.Timestamp(bars.index[-2]))
            h4["high"] = _px(float(prev["High"]))
            h4["low"] = _px(float(prev["Low"]))
            h4["close"] = _px(float(prev["Close"]))
    return {
        "daily": daily,
        "weekly": _completed("W-FRI"),
        "monthly": _completed("ME"),
        "h4": h4,
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
