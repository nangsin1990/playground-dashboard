# FILE: data_engine.py
# ✨ NEW: สร้างไฟล์นี้ขึ้นมาใหม่ทั้งหมดเพื่อเป็นศูนย์กลางการคำนวณ
import pandas as pd
import numpy as np

# --- ฟังก์ชันที่ leadership.py และ pipeline.py เรียกหา ---

def current_drawdown_from_peak(price_series: pd.Series) -> float:
    """
    คำนวณ % การลดลงของราคาล่าสุดจากจุดสูงสุดของ Series
    คืนค่าเป็นตัวเลขบวก เช่น 15.5 สำหรับการลดลง -15.5%
    """
    if price_series.empty or len(price_series) < 2:
        return 0.0
    peak = price_series.cummax()
    drawdown = (price_series - peak) / peak.replace(0, np.nan)
    last_drawdown_pct = abs(drawdown.iloc[-1] * 100)
    if not np.isfinite(last_drawdown_pct):
        return 0.0
    return float(last_drawdown_pct)

def max_drawdown(price_series: pd.Series) -> float:
    """คำนวณ Max Drawdown ของทั้ง Series (จำเป็นสำหรับ pipeline.py)"""
    if price_series.empty or len(price_series) < 2:
        return 0.0
    peak = price_series.cummax()
    drawdown = (price_series - peak) / peak.replace(0, np.nan)
    max_dd = abs(drawdown.min() * 100)
    if not np.isfinite(max_dd):
        return 0.0
    return float(max_dd)

def pct_change(price_series: pd.Series, period: int = 1) -> float:
    """คำนวณ % การเปลี่ยนแปลงของราคาในช่วงเวลาที่กำหนด"""
    if price_series is None or len(price_series) < period + 1:
        return 0.0
    start_price = price_series.iloc[-(period + 1)]
    end_price = price_series.iloc[-1]
    if start_price == 0:
        return 0.0
    change = (end_price / start_price - 1) * 100
    if not np.isfinite(change):
        return 0.0
    return round(float(change), 2)

# --- ฟังก์ชันที่ถูกเรียกแต่ยังไม่มี Logic (สร้างเป็น Placeholder เพื่อให้รันได้) ---

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Placeholder: เพิ่ม Indicators พื้นฐานที่จำเป็นสำหรับส่วนอื่นๆ
    ในอนาคตสามารถเพิ่ม Logic ที่ซับซ้อนขึ้นได้ที่นี่
    """
    df['SMA50'] = df['Close'].rolling(window=50).mean()
    df['SMA150'] = df['Close'].rolling(window=150).mean()
    df['SMA200'] = df['Close'].rolling(window=200).mean()
    return df

def run_scanners(df: pd.DataFrame) -> dict:
    """Placeholder: คืนค่าว่างเพื่อป้องกัน Error"""
    return {}

def confluence_flags(signals: dict) -> tuple:
    """Placeholder: คืนค่าว่างเพื่อป้องกัน Error"""
    return {}, None, None

def rs_rating_per_market(combined: dict, ticker_meta: dict) -> pd.Series:
    """Placeholder: คืนค่า RS Rating เป็น 50 สำหรับทุกตัว"""
    return pd.Series({ticker: 50 for ticker in combined.keys()})

def theme_returns(close_df, theme_map, ret_1d, ret_1m, ret_3m):
    """Placeholder: คืนค่า DataFrame ว่าง"""
    return pd.DataFrame()

def rs_rating_table(close_df: pd.DataFrame, period: int) -> pd.Series:
    """Placeholder: คืนค่า RS เป็น 50 สำหรับทุกตัว"""
    return pd.Series(50, index=close_df.columns)

# --- ฟังก์ชันสำหรับ Technical Analysis (ถูกเรียกจาก technical_analysis.py) ---

def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Placeholder for complex indicators called by technical_analysis.py"""
    df_copy = df.copy()
    df_copy['VWAP'] = (df_copy['Close'] * df_copy['Volume']).cumsum() / df_copy['Volume'].cumsum()
    rolling_20 = df_copy['Close'].rolling(window=20)
    df_copy['BB_MID'] = rolling_20.mean()
    df_copy['BB_STD'] = rolling_20.std()
    df_copy['BB_UPPER'] = df_copy['BB_MID'] + (df_copy['BB_STD'] * 2)
    df_copy['BB_LOWER'] = df_copy['BB_MID'] - (df_copy['BB_STD'] * 2)
    return df_copy

def tech_snapshot(df: pd.DataFrame):
    """Placeholder for technical analysis snapshot"""
    return {}

def rs_vs_benchmark(stock_close: pd.Series, benchmark_close: pd.Series):
    """Placeholder for Relative Strength vs Benchmark calculation"""
    return {}

def sector_relative_strength(stock_close: pd.Series, sector_close: pd.Series):
    """Placeholder for Relative Strength vs Sector calculation"""
    return {}


# --- Single Source of Truth for RRG ---

def calculate_rrg_metrics(
    df_weekly: pd.DataFrame,
    tickers: list[str],
    benchmark_ticker: str,
    period: int = 10,
    tail_length: int = 12,
) -> dict:
    """
    The Single Source of Truth for calculating JdK RRG metrics.
    ถูกเรียกโดย rotation_rrg.py
    """
    results = {}
    if benchmark_ticker not in df_weekly.columns:
        return results

    benchmark_prices = df_weekly[benchmark_ticker]

    for ticker in tickers:
        if ticker not in df_weekly.columns or df_weekly[ticker].isnull().all():
            continue

        price_series = df_weekly[ticker]

        # 1. RS-Ratio
        rs_ratio = (price_series / benchmark_prices).dropna()
        if len(rs_ratio) < period:
            continue

        jrs = 100 + ((rs_ratio.iloc[-1] / rs_ratio.rolling(period).mean().iloc[-1] - 1) * 10)

        # 2. RS-Momentum
        momentum = rs_ratio.pct_change(periods=period).dropna()
        if len(momentum) < period:
            continue

        jmo = 100 + ((momentum.iloc[-1] / momentum.rolling(period).std().iloc[-1] - 1) * 10) if momentum.rolling(period).std().iloc[-1] > 0 else 100

        # 3. Quadrant
        if jrs > 100 and jmo > 100: quadrant = "Leading"
        elif jrs > 100 and jmo < 100: quadrant = "Weakening"
        elif jrs < 100 and jmo < 100: quadrant = "Lagging"
        else: quadrant = "Improving"

        # 4. Tail
        tail_jrs = 100 + ((rs_ratio.rolling(period).apply(lambda x: x[-1] / x.mean()) - 1) * 10)
        tail_mom = rs_ratio.pct_change(periods=period)
        tail_jmo = 100 + ((tail_mom.rolling(period).apply(lambda x: x[-1] / x.std()) - 1) * 10)

        tail_data = list(zip(tail_jrs.dropna().values, tail_jmo.dropna().values))[-tail_length:]

        results[ticker] = {
            "jrs": round(jrs, 2),
            "jmo": round(jmo, 2),
            "quadrant": quadrant,
            "tail": tail_data
        }

    return results

# --- Single Source of Truth for Correlation Matrix ---
# ✨ NEW: เพิ่มฟังก์ชันที่ขาดหายไปนี้เข้ามา

def compute_correlation_matrix(combined: dict[str, pd.DataFrame], tickers: list[str], days: int) -> dict:
    """
    Computes a pairwise correlation matrix for the given tickers over a specific period.
    Called by correlation.py
    """
    try:
        # 1. สร้าง DataFrame ของราคาปิดจาก tickers ที่ระบุ
        close_df = pd.DataFrame({
            t: data['Close']
            for t, data in combined.items()
            if t in tickers and data is not None and not data.empty and len(data) >= days
        })

        if close_df.empty or len(close_df.columns) < 2:
            return {"ok": False, "error": "Not enough valid historical data to compute correlation."}

        # 2. Trim ข้อมูลให้เหลือเฉพาะช่วงเวลาที่ต้องการ และคำนวณ daily returns
        returns = close_df.tail(days).pct_change().dropna(how='all')

        if len(returns) < 2:
            return {"ok": False, "error": "Not enough return data points for correlation."}

        # 3. คำนวณ Correlation Matrix
        corr_matrix_df = returns.corr(method='pearson')

        # 4. แปลง NaN เป็น None เพื่อให้ JSON serializable
        corr_matrix_df = corr_matrix_df.where(pd.notnull(corr_matrix_df), None)

        # 5. จัดรูปแบบผลลัพธ์
        matrix_list = corr_matrix_df.values.tolist()
        labels = corr_matrix_df.columns.tolist()

        return {
            "ok": True,
            "labels": labels,
            "matrix": matrix_list,
        }
    except Exception as e:
        return {"ok": False, "error": f"An error occurred in compute_correlation_matrix: {e}"}
