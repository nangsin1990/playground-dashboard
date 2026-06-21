"""
constants.py — Central config for all magic numbers
ไม่ต้องแก้หลายไฟล์ แก้ที่นี่ที่เดียว
"""

# ── Trading Calendar ──────────────────────────────────────────────────────────
TRADING_DAYS_YEAR   = 252   # approx trading days per year
TRADING_DAYS_MONTH  = 21    # approx trading days per month (1M lookback)
TRADING_DAYS_QUARTER= 63    # approx trading days per quarter (3M lookback)
TRADING_DAYS_HALFYR = 126   # approx trading days per half-year (6M lookback)
TRADING_DAYS_3QTR   = 189   # 9-month lookback (blended RS)
TRADING_WEEKS_YEAR  = 52    # weeks per year (RS ratio rolling window)

# ── Indicator Windows ─────────────────────────────────────────────────────────
SMA_SHORT   = 10    # short SMA (pocket pivot, VDU)
SMA_MID     = 50    # medium SMA (breadth, VDU)
SMA_TREND   = 150   # trend SMA (Minervini trend template)
SMA_LONG    = 200   # long SMA (trend template, slope)
VOL_SMA     = 50    # volume average window
HIGH_52W    = TRADING_DAYS_YEAR  # 52-week high lookback

# ── Scanner Parameters ────────────────────────────────────────────────────────
VDU_VOL_LOW         = 0.40   # Volume Dry-Up: min % of avg vol
VDU_VOL_HIGH        = 0.60   # Volume Dry-Up: max % of avg vol
BGU_GAP_PCT         = 1.5    # Buyable Gap-Up: min open gap %
BGU_VOL_MULT        = 2.5    # Buyable Gap-Up: min volume multiple
W52_PROXIMITY       = 0.95   # 52W High Breakout: within 5% of high
PPBP_VOL_LOOKBACK   = 10     # Pocket Pivot: lookback days for down-vol max
CONFLUENCE_DAYS     = 5      # rolling window for confluence
CONFLUENCE_MIN      = 2      # min signals to confirm confluence

# ── RS Rating ─────────────────────────────────────────────────────────────────
RS_BLEND_3M_WT  = 0.40   # blended return weights (IBD style)
RS_BLEND_6M_WT  = 0.20
RS_BLEND_9M_WT  = 0.20
RS_BLEND_12M_WT = 0.20

# ── Cache TTL ─────────────────────────────────────────────────────────────────
CACHE_TTL_DATA     = 15 * 60   # 15 min — market data
CACHE_TTL_CALENDAR = 30 * 60   # 30 min — economic calendar (changes less often)

# ── Data Fetch ────────────────────────────────────────────────────────────────
FETCH_PERIOD        = "18mo"   # yfinance history period
FETCH_TIMEOUT       = 30       # seconds per batch before giving up
FETCH_CHUNK_SIZE    = 60       # tickers per yf.download() call
FETCH_MIN_ROWS      = 60       # discard ticker if fewer rows returned
FETCH_RATE_DELAY    = 0.5      # seconds to sleep between chunks (rate limit)
FETCH_RETRY_MAX     = 3        # max retries on rate-limit / timeout
FETCH_RETRY_BASE    = 2.0      # exponential backoff base (seconds)

# ── Leadership Board ──────────────────────────────────────────────────────────
LB_TREND_LOOKBACK   = 21       # SMA200 slope window (bars)
LB_ACCUM_LOOKBACK   = 20       # accumulation score window (days)
LB_TIGHTNESS_WEEKS  = 6        # base tightness lookback (weeks)
LB_UD_RATIO_LOOKBACK= 10       # up/down volume ratio window
LB_VOL_WINDOW       = 51       # vol ratio lookback (excl today)
LB_BREAKOUT_PROX    = 5.0      # % below 52W high = "near breakout"
LB_ACCUM_MIN        = 0.2      # institutional filter: min accum score
LB_UD_MIN           = 1.3      # institutional filter: min U/D ratio
LB_VOL_MIN          = 1.5      # volume surge filter
LB_TOP_N            = 20       # rows per leaderboard tab

# ── RRG ──────────────────────────────────────────────────────────────────────
RRG_SMOOTHING       = 14       # ← v2: EMA span 10→14 (reduces quadrant noise)
RRG_ROLL_MIN        = 10       # min periods for rolling stats
RRG_TAIL_WEEKS      = 16       # tail history points  (was RRG_RRG_TAIL_WEEKS — typo fixed)
RRG_TAIL_STEP       = 5        # trading days between tail points
RRG_CLAMP_LO        = 90.0     # display clamp
RRG_CLAMP_HI        = 115.0
RRG_ROC_SHIFT       = 14       # ← v2: RS-Momentum ROC lookback 10→14 (matches smoothing)
RRG_MIN_TICKERS     = 1        # min tickers to compute theme RRG
RRG_MIN_HISTORY     = 30       # min trading days of history needed

# ── Thematic Matrix ───────────────────────────────────────────────────────────
THEMATIC_TOP_TICKERS    = 4    # top RS tickers displayed per theme
THEMATIC_MAX_MEMBERS    = 30   # max member rows per theme

# ── Economic Calendar ─────────────────────────────────────────────────────────
CAL_LOOK_AHEAD_DAYS = 120      # days ahead to show
CAL_LOOK_BACK_DAYS  = 7        # days back (show recent past as dimmed)
CAL_MAX_EVENTS      = 30       # max events to return

# ── Pipeline / Universe ───────────────────────────────────────────────────────
PIPELINE_BATCH_SIZE = 60
CORE_N = {"US": 40, "HK": 16, "JP": 16, "KR": 12, "CN": 12}

# ── Breadth ───────────────────────────────────────────────────────────────────
BREADTH_HISTORY_DAYS    = 20   # days of breadth chart history
BREADTH_BEAR_THRESHOLD  = 40.0 # ma50% below this = bear signal per market
BREADTH_BEAR_FALL       = -5.0 # 5-day change below this = falling
BREADTH_BEAR_MIN_MKT    = 3    # min markets in bear = global bear

# ── Watchlist ─────────────────────────────────────────────────────────────────
WATCHLIST_TOP_N         = 10   # confluence watchlist size
THEME_TOP_N             = 5    # top themes in overview
RS_MOVERS_TOP_N         = 5    # RS movers count

# ── Correlation Matrix ────────────────────────────────────────────────────────
CORR_TICKERS = [
    "SPY", "QQQ", "IWM", "DIA",      # broad US equity
    "XLK", "XLF", "XLE", "XLV",      # sectors
    "TLT", "IEF", "HYG",             # bonds
    "GLD", "SLV", "USO",             # commodities
    "DXY", "UUP",                    # dollar
    "VXX",                           # volatility
]
CORR_PERIOD_DAYS = 63   # lookback for correlation (~3 months)
CORR_BENCHMARK   = "SPY"

# ── Sector ETF Map (GICS → SPDR) ──────────────────────────────────────────────
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
