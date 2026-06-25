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
CACHE_TTL_DATA     = 15 * 60   # 15 min
CACHE_TTL_CALENDAR = 30 * 60   # 30 min

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
LB_TOP_N            = 20

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
CAL_MAX_EVENTS      = 30

# ── Pipeline / Universe ───────────────────────────────────────────────────────
PIPELINE_BATCH_SIZE = 10       # ← v5.3: 60→20→10 (per Incident Report Fix #5)
CORE_N = {"US": 40, "HK": 16, "JP": 16, "KR": 12, "CN": 12}

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
    "DXY", "UUP",
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
