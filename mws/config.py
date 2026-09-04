# -*- coding: utf-8 -*-
"""Config — no secrets. Keys must come from environment variables. MWS v3.8.0"""

from __future__ import annotations

import os

REVISION = "3.8.0"
REVISION_TAG = "20260904-r7"

FRED_API_KEY = os.getenv("FRED_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

CACHE_TTL_SEC = int(os.getenv("MWS_CACHE_TTL", "300"))
LOG_LEVEL = os.getenv("MWS_LOG_LEVEL", "INFO").upper()

DEFAULT_PORTFOLIO = float(os.getenv("MWS_PORTFOLIO", "100000"))
DEFAULT_RISK_PCT = float(os.getenv("MWS_RISK_PCT", "0.5"))

SECTOR_ETF = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}

SECTOR_FWD_PE_BANDS = {
    "Technology": (18, 32, 50),
    "Communication Services": (16, 28, 45),
    "Healthcare": (14, 24, 40),
    "Consumer Cyclical": (12, 22, 35),
    "Consumer Defensive": (14, 22, 32),
    "Financial Services": (8, 14, 22),
    "Energy": (8, 14, 22),
    "Industrials": (12, 20, 30),
    "Basic Materials": (10, 16, 26),
    "Real Estate": (12, 20, 32),
    "Utilities": (12, 18, 26),
    "default": (12, 22, 35),
}

PEER_MAP = {
    "AAPL": ["MSFT", "GOOGL", "AMZN", "META"],
    "MSFT": ["AAPL", "GOOGL", "AMZN", "ORCL"],
    "NVDA": ["AMD", "AVGO", "TSM", "ASML", "INTC"],
    "AMD": ["NVDA", "INTC", "AVGO", "QCOM"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL"],
    "META": ["GOOGL", "SNAP", "PINS", "TTD"],
    "AMZN": ["MSFT", "GOOGL", "WMT", "COST"],
    "TSLA": ["RIVN", "LCID", "F", "GM"],
    "DLTR": ["DG", "WMT", "TGT", "COST", "BJ"],
    "DG": ["DLTR", "WMT", "TGT", "COST"],
    "WMT": ["TGT", "COST", "AMZN", "BJ"],
    "COST": ["WMT", "TGT", "BJ", "AMZN"],
    "KO": ["PEP", "MNST", "KDP"],
    "PEP": ["KO", "MNST", "KDP"],
    "JNJ": ["PFE", "MRK", "ABBV", "LLY"],
    "LLY": ["NVO", "JNJ", "MRK", "PFE"],
    "UNH": ["ELV", "CVS", "CI", "HUM"],
    "JPM": ["BAC", "WFC", "C", "GS"],
    "BAC": ["JPM", "WFC", "C", "USB"],
    "XOM": ["CVX", "COP", "SLB", "EOG"],
    "CVX": ["XOM", "COP", "BP", "SHEL"],
    "AMAT": ["LRCX", "KLAC", "ASML", "TER"],
    "LRCX": ["AMAT", "KLAC", "ASML", "TER"],
    "KLAC": ["AMAT", "LRCX", "ASML", "TER"],
    "ASML": ["AMAT", "LRCX", "KLAC", "TSM"],
}

INDUSTRY_PEERS = {
    "Discount Stores": ["DLTR", "DG", "WMT", "TGT", "COST"],
    "Consumer Electronics": ["AAPL", "SONY", "HPQ"],
    "Semiconductors": ["NVDA", "AMD", "AVGO", "TSM", "QCOM", "INTC"],
    "Semiconductor Equipment & Materials": ["AMAT", "LRCX", "KLAC", "ASML", "TER"],
    "Software—Infrastructure": ["MSFT", "ORCL", "CRM", "NOW"],
    "Internet Content & Information": ["GOOGL", "META", "SNAP"],
    "Internet Retail": ["AMZN", "BABA", "MELI"],
    "Auto Manufacturers": ["TSLA", "F", "GM", "RIVN"],
    "Drug Manufacturers—General": ["JNJ", "PFE", "MRK", "ABBV", "LLY"],
    "Banks—Diversified": ["JPM", "BAC", "WFC", "C"],
    "Oil & Gas Integrated": ["XOM", "CVX", "BP", "SHEL"],
}

POSITIVE_KW = [
    "beats estimates", "beat estimates", "raises guidance", "raised guidance",
    "price target raised", "upgraded to", "strong growth", "record revenue",
    "record profit", "partnership with", "wins contract", "fda approval",
    "share buyback", "raises dividend", "outperform rating", "beats expectations",
]
NEGATIVE_KW = [
    "misses estimates", "missed estimates", "cuts guidance", "cut guidance",
    "price target cut", "downgraded to", "earnings miss", "revenue miss",
    "layoffs", "class action", "sec investigation", "going concern",
    "bankruptcy", "profit warning", "demand slowdown", "underperform rating",
]

DQ_WEIGHTS = {
    "price": 0.10,
    "history": 0.10,
    "spy": 0.10,
    "fundamental": 0.30,
    "valuation": 0.15,
    "technical": 0.10,
    "peers": 0.05,
    "news": 0.05,
    "macro": 0.05,
}

# Research composite (sums to 1.0). Risk is inverted: lower risk → higher contribution.
COMPOSITE_WEIGHTS = {
    "quality": 0.35,
    "momentum": 0.25,
    "valuation": 0.20,
    "risk_attractiveness": 0.20,
}

DQ_GATE = 55.0
QUALITY_SKIP = 48.0
QUALITY_PASS = 55.0
QUALITY_STRONG = 72.0
MOMENTUM_STRETCH_RSI = 72.0
MOMENTUM_STRETCH_RSI_HARD = 75.0
MA20_STRETCH = 1.08
MA20_STRETCH_HARD = 1.12
VALUATION_EXTREME = 38
VALUATION_EXPENSIVE_ZONE = 45
RSI_STRETCH_ZONE = 68.0
MA20_STRETCH_ZONE = 1.05
EARNINGS_WINDOW_DAYS = 10
SERIES_BARS = 180

MA_FAST = 20
MA_MID = 50
MA_SLOW = 200
RS_LOOKBACK = 63
BREAKOUT_LOOKBACK = 60
VOL_MA = 20
VOL_BREAKOUT_MULT = 1.5
RSI_LEN = 14
RSI_MAX_ENTRY = 72
RSI_PB_LO, RSI_PB_HI = 40, 60
ATR_LEN = 14
ATR_STOP_MULT = 2.0
MAX_HOLD = 40
TP_R = 2.0
COST_BPS = 5.0
PENDING_EXPIRY_DAYS = 10
