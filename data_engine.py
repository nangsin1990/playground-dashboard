# FILE: data_engine.py

import pandas as pd
import numpy as np

# ✨ NEW: เพิ่มฟังก์ชันคำนวณ Drawdown ที่ leadership.py เรียกใช้
# นี่คือการแก้ไขปัญหา Function Hallucination ครับ
def current_drawdown_from_peak(price_series: pd.Series) -> float:
    """
    Calculates the percentage drawdown of the last price from the series' peak.
    Returns a positive number representing the drawdown percentage (e.g., 15.5 for -15.5%).
    """
    if price_series.empty or len(price_series) < 2:
        return 0.0

    # 1. หาจุดสูงสุดของราคาทั้ง Series (Rolling Peak)
    peak = price_series.cummax()

    # 2. คำนวณ % Drawdown ณ แต่ละจุด (ป้องกันการหารด้วยศูนย์)
    # เราใช้ .replace(0, np.nan) เพื่อให้ผลลัพธ์เป็น NaN ถ้า Peak เป็น 0 ซึ่งจะถูกจัดการทีหลัง
    drawdown = (price_series - peak) / peak.replace(0, np.nan)

    # 3. ดึงค่า Drawdown ล่าสุด, แปลงเป็น %, ทำให้เป็นบวก, และส่งคืน
    # .iloc[-1] จะดึงค่าสุดท้ายของ Series
    last_drawdown_pct = abs(drawdown.iloc[-1] * 100)

    # 4. จัดการกรณีที่ผลลัพธ์ไม่ใช่ตัวเลข (เช่น เกิดจากการหารด้วยศูนย์)
    if not np.isfinite(last_drawdown_pct):
        return 0.0

    return float(last_drawdown_pct)


# ✨ EXISTING: ฟังก์ชัน pct_change ที่ leadership.py เรียกใช้ (ถูกต้องแล้ว)
def pct_change(price_series: pd.Series, period: int = 1) -> float:
    """
    Calculates the percentage change over a given period for a pandas Series.
    """
    if price_series is None or len(price_series) < period + 1:
        return 0.0

    # ดึงข้อมูลย้อนหลังตาม period ที่กำหนด
    subset = price_series.tail(period + 1)

    # ใช้ iloc เพื่อความแม่นยำในการเข้าถึงตำแหน่ง
    start_price = subset.iloc[0]
    end_price = subset.iloc[-1]

    if start_price == 0:
        return 0.0 # หรือจัดการเป็นกรณีพิเศษ เช่น return np.inf

    # คำนวณ % change
    change = (end_price / start_price - 1) * 100

    if not np.isfinite(change):
        return 0.0

    return round(float(change), 2)

# ------------------------------------------------------------------------------------
# --- ✨ REFACTOR: Single Source of Truth for RRG (แหล่งความจริงหนึ่งเดียวสำหรับ RRG) ---
# ------------------------------------------------------------------------------------
# ฟังก์ชันนี้ถูกปรับปรุงให้เป็น "Master Version" สำหรับการคำนวณ RRG
# ไฟล์ rotation_rrg.py ควรจะ import และเรียกใช้ฟังก์ชันนี้แทนการคำนวณเอง
# เพื่อป้องกันไม่ให้สูตรคำนวณในโปรเจกต์ของเราไม่ตรงกัน

def compute_rrg(
    price_df: pd.DataFrame,
    benchmark_ticker: str,
    tickers: list[str],
    period: int = 10,
    tail_length: int = 5
) -> dict:
    """
    The Single Source of Truth for calculating JdK RRG metrics.

    Args:
        price_df (pd.DataFrame): DataFrame with tickers as columns and dates as index, prices as values.
        benchmark_ticker (str): The column name of the benchmark security.
        tickers (list[str]): A list of tickers to calculate RRG for.
        period (int): The lookback period for momentum calculation.
        tail_length (int): The number of recent data points for the RRG tail.

    Returns:
        A dictionary where keys are tickers and values are their RRG metrics.
    """
    if benchmark_ticker not in price_df.columns:
        return {} # ไม่มี Benchmark ก็จบข่าว

    # 1. คำนวณ Price Ratio ของทุก Ticker เทียบกับ Benchmark
    # price_df.div(...) คือการเอาทุกคอลัมน์ไปหารด้วย benchmark_prices ทีเดียว (Vectorized!)
    benchmark_prices = price_df[benchmark_ticker]
    rs_ratio = price_df[tickers].div(benchmark_prices, axis='index')
    rs_ratio = rs_ratio.dropna(how='all', axis=1) # ลบ Ticker ที่ข้อมูลเป็น NaN ล้วน

    if rs_ratio.empty:
        return {}

    # 2. คำนวณ JdK RS-Ratio (แกน X)
    # ทำให้ค่าเฉลี่ยอยู่ที่ 100, ค่ามากกว่า 100 คือ Outperform
    # เราใช้ .apply เพื่อทำกับทุกคอลัมน์ (ทุก Ticker) ทีเดียว
    jrs = rs_ratio.apply(lambda x: 100 + ((x.iloc[-1] / x.mean() - 1) * 10) if not x.empty else 100)

    # 3. คำนวณ JdK RS-Momentum (แกน Y)
    # ใช้ .pct_change() เพื่อหาอัตราการเปลี่ยนแปลงของ RS-Ratio
    rs_mom = rs_ratio.pct_change(periods=period)
    # Normalize โมเมนตัมให้อยู่ในสเกล 100
    jmo = rs_mom.apply(lambda x: 100 + ((x.iloc[-1] / x.std() - 1) * 10) if not x.empty and x.std() > 0 else 100)

    # 4. สร้าง RRG Tail สำหรับวาดกราฟ
    # เราจะเก็บค่า JRS และ JMO ย้อนหลัง `tail_length` วัน
    tail_data = {}
    for ticker in rs_ratio.columns:
        # คำนวณ JRS และ JMO ย้อนหลัง
        jrs_hist = rs_ratio[ticker].rolling(window=period).apply(lambda x: 100 + ((x.iloc[-1] / x.mean() - 1) * 10) if not x.empty else 100)
        jmo_hist = rs_ratio[ticker].pct_change(periods=period).rolling(window=period).apply(lambda x: 100 + ((x.iloc[-1] / x.std() - 1) * 10) if not x.empty and x.std() > 0 else 100)

        # รวมแกน X, Y เข้าด้วยกันแล้วดึงเฉพาะส่วนท้าย
        tail = list(zip(jrs_hist.dropna().values, jmo_hist.dropna().values))
        tail_data[ticker] = tail[-tail_length:]

    # 5. ประกอบร่างผลลัพธ์สุดท้าย
    results = {}
    for ticker in rs_ratio.columns:
        jrs_val = jrs.get(ticker, 100)
        jmo_val = jmo.get(ticker, 100)

        # กำหนด Quadrant
        quadrant = "Leading" if jrs_val > 100 and jmo_val > 100 else \
                   "Weakening" if jrs_val > 100 and jmo_val < 100 else \
                   "Lagging" if jrs_val < 100 and jmo_val < 100 else \
                   "Improving"

        results[ticker] = {
            "jrs": round(jrs_val, 2),
            "jmo": round(jmo_val, 2),
            "quadrant": quadrant,
            "tail": tail_data.get(ticker, []),
        }

    return results
