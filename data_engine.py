# FILE: data_engine.py

import pandas as pd
import numpy as np

# ✨ NEW: เพิ่มฟังก์ชันคำนวณ Drawdown ที่ leadership.py เรียกใช้
def current_drawdown_from_peak(price_series: pd.Series) -> float:
    """
    Calculates the percentage drawdown of the last price from the series' peak.
    Returns a positive number representing the drawdown percentage (e.g., 15.5 for -15.5%).
    """
    if price_series.empty:
        return 0.0

    # 1. หาจุดสูงสุดของราคาทั้ง Series (Rolling Peak)
    peak = price_series.cummax()

    # 2. คำนวณ % Drawdown ณ แต่ละจุด
    drawdown = (price_series - peak) / peak

    # 3. ดึงค่า Drawdown ล่าสุด, แปลงเป็น %, ทำให้เป็นบวก, และส่งคืน
    last_drawdown_pct = abs(drawdown.iloc[-1] * 100)

    # จัดการกรณีที่เป็น NaN หรือ inf
    if not np.isfinite(last_drawdown_pct):
        return 0.0

    return float(last_drawdown_pct)

# ✨ EXISTING: ฟังก์ชัน pct_change ที่ leadership.py เรียกใช้ (ถูกต้องแล้ว)
def pct_change(price_series: pd.Series, period: int = 1) -> float:
    """
    Calculates the percentage change over a given period for a pandas Series.
    """
    if len(price_series) < period + 1:
        return 0.0

    # ดึงข้อมูลย้อนหลังตาม period ที่กำหนด
    subset = price_series.tail(period + 1)

    # คำนวณ % change
    change = (subset.iloc[-1] / subset.iloc[0] - 1) * 100

    if not np.isfinite(change):
        return 0.0

    return round(float(change), 2)

# --- ส่วนของ RRG ที่จะถูก Refactor ต่อไป ---
# (ผมจะใส่โค้ดจากที่คุณปิย่าส่งมาครั้งก่อนไว้ก่อน เพื่อให้ไฟล์สมบูรณ์)
def calculate_rrg_metrics(df_weekly: pd.DataFrame, tickers: list[str], benchmark_ticker: str, period: int = 10):
    """
    Core RRG calculation engine. Receives weekly data and returns raw metrics.
    """
    if benchmark_ticker not in df_weekly.columns:
        return {}

    benchmark = df_weekly[benchmark_ticker].pct_change(period).dropna()
    results = {}

    for ticker in tickers:
        if ticker not in df_weekly.columns:
            continue

        asset = df_weekly[ticker].pct_change(period).dropna()

        # Align data by index
        common_index = benchmark.index.intersection(asset.index)
        if len(common_index) < period:
            continue

        benchmark_aligned = benchmark.loc[common_index]
        asset_aligned = asset.loc[common_index]

        # RS-Ratio (JdK RS-Ratio)
        rs = (asset_aligned + 1).cumprod() / (benchmark_aligned + 1).cumprod()
        jrs = 100 + ((rs.iloc[-1] / rs.mean() - 1) * 100)

        # RS-Momentum (JdK RS-Momentum)
        rs_mom = rs.pct_change(period).dropna()
        if rs_mom.empty:
            continue
        jmo = 100 + ((rs_mom.iloc[-1] / rs_mom.std() - 1) * 100)

        # Quadrant
        quadrant = "Leading" if jrs > 100 and jmo > 100 else \
                   "Weakening" if jrs > 100 and jmo < 100 else \
                   "Lagging" if jrs < 100 and jmo < 100 else \
                   "Improving"

        # Tail data for plotting
        tail = list(zip(rs.rolling(window=period).mean().dropna().pct_change().dropna().values,
                        rs.pct_change(period).dropna().values))

        results[ticker] = {
            "jrs": round(jrs, 2),
            "jmo": round(jmo, 2),
            "quadrant": quadrant,
            "tail": tail[-10:] # Last 10 points for the tail
        }
    return results
