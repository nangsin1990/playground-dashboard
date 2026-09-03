# @title
#"""
#constants.py — Central config for all magic numbers
#v5.2: PIPELINE_BATCH_SIZE 60→20, FETCH_CHUNK_SIZE 60→20
#      (per Incident Report Fix #5: smaller batches = easier debug + less Yahoo timeout)
#"""

# ── Trading Calendar ──────────────────────────────────────────────────────────
TRADING_DAYS_YEAR   = 252
TRADING_DAYS_MONTH  = 21
TRADING_DAYS_QUARTER= 63
TRADING_DAYS_HALFYR = 126
TRADING_DAYS_3QTR   = 189
TRADING_WEEKS_YEAR  = 52

# ── Indicator Windows ─────────────────────────────────────────────────────────
SMA_SHORT   = 10
SMA_MID     = 50
SMA_TREND   = 150
SMA_LONG    = 200
VOL_SMA     = 50
HIGH_52W    = TRADING_DAYS_YEAR

# ── Scanner Parameters ────────────────────────────────────────────────────────
VDU_VOL_LOW         = 0.40
VDU_VOL_HIGH        = 0.60
BGU_GAP_PCT         = 1.5
BGU_VOL_MULT        = 2.5
W52_PROXIMITY       = 0.95
PPBP_VOL_LOOKBACK   = 10
CONFLUENCE_DAYS     = 5
CONFLUENCE_MIN      = 2

# ── RS Rating ─────────────────────────────────────────────────────────────────
RS_BLEND_3M_WT  = 0.40
RS_BLEND_6M_WT  = 0.20
RS_BLEND_9M_WT  = 0.20
RS_BLEND_12M_WT = 0.20

# ── Cache TTL ─────────────────────────────────────────────────────────────────
# จุดเดียวสำหรับ TTL ทั้งหมด — ไฟล์อื่น (economic_calendar, etf_board, global_market,
# market_regime, rotation_rrg) เดิม hardcode ค่าซ้ำกันเอง ทำให้ TTL ไม่ตรงกันโดยไม่ตั้งใจ
# ตอนนี้ import จากที่นี่ที่เดียว ถ้าจะปรับความถี่ refresh ให้แก้ตรงนี้จุดเดียว
CACHE_TTL_DATA     = 15 * 60   # 15 min — ราคา/OHLCV, screener, leadership, thematic, market_regime
CACHE_TTL_CALENDAR = 30 * 60   # 30 min — economic calendar, earnings board, event impact
CACHE_TTL_GLOBAL   = 10 * 60   # 10 min — global market indices (รีเฟรชไวกว่าเพราะ index เปลี่ยนเร็ว)
CACHE_TTL_GOLD      = 10 * 60   # 10 min — Gold Command Center (spot/futures/macro tape + scores)
CACHE_TTL_FUND      = 24 * 60 * 60  # 24h — P/E ROE EPS Market Cap เปลี่ยนช้า

# ── Gold Command Center ───────────────────────────────────────────────────────
# Spec form: (32.148 × 0.965) / 65.6  ≈ 0.4729088415
# Equivalent to (15.244 g / 31.1034768 g) × 0.965, locked to the spec identity for audit.
THAI_GOLD_FACTOR = (32.148 * 0.965) / 65.6
GOLD_PREMIUM_USD = 2.0          # Model Premium USD/oz — NOT live Thai-shop premium
GOLD_FLAT_PCT    = 0.05         # |return| below this → FLAT in driver decomposition

# ── Data Fetch ────────────────────────────────────────────────────────────────
FETCH_PERIOD        = "18mo"   # yfinance history period
FETCH_TIMEOUT       = 30       # seconds per batch
FETCH_CHUNK_SIZE    = 20       # ← v5.2: 60→20 (Yahoo จัดการได้ดีขึ้น)
FETCH_MIN_ROWS      = 60
FETCH_RATE_DELAY    = 0.3      # ← v5.2: 0.5→0.3s (batch เล็กลงแล้ว ลด delay ได้)
FETCH_RETRY_MAX     = 3
FETCH_RETRY_BASE    = 2.0

# ── Leadership Board ──────────────────────────────────────────────────────────
LB_TREND_LOOKBACK   = 21
LB_ACCUM_LOOKBACK   = 20
LB_TIGHTNESS_WEEKS  = 6
LB_UD_RATIO_LOOKBACK= 10
LB_VOL_WINDOW       = 51
LB_BREAKOUT_PROX    = 5.0
LB_ACCUM_MIN        = 0.2
LB_UD_MIN           = 1.3
LB_VOL_MIN          = 1.5
LB_TOP_N            = 80

# ── RRG ──────────────────────────────────────────────────────────────────────
RRG_SMOOTHING       = 14
RRG_ROLL_MIN        = 10
RRG_TAIL_WEEKS      = 16
RRG_TAIL_STEP       = 5
RRG_CLAMP_LO        = 90.0
RRG_CLAMP_HI        = 115.0
RRG_ROC_SHIFT       = 14
RRG_MIN_TICKERS     = 1
RRG_MIN_HISTORY     = 30

# ── Thematic Matrix ───────────────────────────────────────────────────────────
THEMATIC_TOP_TICKERS    = 4
THEMATIC_MAX_MEMBERS    = 30

# ── Economic Calendar ─────────────────────────────────────────────────────────
CAL_LOOK_AHEAD_DAYS = 120
CAL_LOOK_BACK_DAYS  = 7
CAL_MAX_EVENTS      = 50

# ── Pipeline / Universe ───────────────────────────────────────────────────────
# v5.4: 10→25 — batch 10 ทำให้ full universe (~913 ticker) แตกเป็น ~90 batches/คิว
# ช้าจาก round-trip overhead สะสม ไม่ใช่จาก Yahoo throttle โดยตรง (Incident Report Fix #5
# แก้ปัญหา timeout ที่ 60 ไปแล้ว 25 ยังอยู่ในโซนปลอดภัยแต่ลดจำนวน batch ลง ~60%)
PIPELINE_BATCH_SIZE = 25
CORE_N = {"US": 40, "TH": 16, "HK": 16, "JP": 16, "KR": 12, "CN": 12, "DE": 10, "FR": 10, "GB": 10}

# ── Pre-warm scheduler ────────────────────────────────────────────────────────
# ยิง load_market_pack("core") ล่วงหน้าเป็นระยะ กัน cold-cache
# ไม่ prewarm full (~900 ตัว) เพราะกด Yahoo หนักถ้าเปิดค้างทั้งวัน
PREWARM_INTERVAL_SEC = 14 * 60
PREWARM_MODES = ("core",)

# ── Breadth ───────────────────────────────────────────────────────────────────
BREADTH_HISTORY_DAYS    = 20
BREADTH_BEAR_THRESHOLD  = 40.0
BREADTH_BEAR_FALL       = -5.0
BREADTH_BEAR_MIN_MKT    = 3

# ── Watchlist ─────────────────────────────────────────────────────────────────
WATCHLIST_TOP_N         = 10
THEME_TOP_N             = 5
RS_MOVERS_TOP_N         = 5

# ── Correlation Matrix ────────────────────────────────────────────────────────
CORR_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA",
    "XLK", "XLF", "XLE", "XLV",
    "TLT", "IEF", "HYG",
    "GLD", "SLV", "USO",
    "DX-Y.NYB", "UUP",
    "VXX",
]
CORR_PERIOD_DAYS = 63
CORR_BENCHMARK   = "SPY"

# ── Sector ETF Map ────────────────────────────────────────────────────────────
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
    "Semiconductors":          "SMH",
    "Biotech":                 "XBI",
    "Electronic Technology":   "XLK",
    "ETF - Broad Market":      "SPY",
    "ETF - Sector Equity":     "XLK",
    "ETF - Fixed Income":      "TLT",
    "ETF - Commodity":         "GLD",
}
