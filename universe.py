"""
Universe definition: ticker -> (display_name, theme/sector)

Update: Now includes DE, FR, GB markets and a special RRG_GLOBAL_UNIVERSE.

Yahoo Finance ticker conventions:
  US : plain symbol
  TH : SYMBOL.BK
  HK : 4-digit code + .HK
  JP : 4-digit code + .T
  KR : 6-digit code + .KS
  CN : 6-digit code + .SS (Shanghai) / .SZ (Shenzhen)
  DE : SYMBOL.DE (XETRA)
  FR : SYMBOL.PA (Euronext Paris)
  GB : SYMBOL.L (London Stock Exchange)
"""

UNIVERSE = {
    "US": {
        'MMM': ('3M', 'Industrials'), 'AOS': ('A. O. Smith', 'Industrials'), 'ABT': ('Abbott Laboratories', 'Health Care'),
        'ABBV': ('AbbVie', 'Health Care'), 'ACN': ('Accenture', 'Information Technology'), 'ADBE': ('Adobe Inc.', 'Information Technology'),
        'AMD': ('Advanced Micro Devices', 'Information Technology'), 'AES': ('AES Corporation', 'Utilities'), 'AFL': ('Aflac', 'Financials'),
        'A': ('Agilent Technologies', 'Health Care'), 'APD': ('Air Products', 'Materials'), 'ABNB': ('Airbnb', 'Consumer Discretionary'),
        'AKAM': ('Akamai Technologies', 'Information Technology'), 'ALB': ('Albemarle Corporation', 'Materials'),
        'ARE': ('Alexandria Real Estate Equities', 'Real Estate'), 'ALGN': ('Align Technology', 'Health Care'), 'ALLE': ('Allegion', 'Industrials'),
        'LNT': ('Alliant Energy', 'Utilities'), 'ALL': ('Allstate', 'Financials'), 'GOOGL': ('Alphabet Inc. (Class A)', 'Communication Services'),
        'GOOG': ('Alphabet Inc. (Class C)', 'Communication Services'), 'MO': ('Altria', 'Consumer Staples'), 'AMZN': ('Amazon', 'Consumer Discretionary'),
        # ... (and the rest of the original US universe)
        'SPY': ('SPDR S&P 500 ETF Trust', 'ETF - Broad Market'), 'QQQ': ('Invesco QQQ Trust', 'ETF - Broad Market'),
        'IWM': ('iShares Russell 2000 ETF', 'ETF - Broad Market'), 'DIA': ('SPDR Dow Jones Industrial Average ETF', 'ETF - Broad Market'),
        'VTI': ('Vanguard Total Stock Market ETF', 'ETF - Broad Market'), 'RSP': ('Invesco S&P 500 Equal Weight ETF', 'ETF - Broad Market'),
        # ... (and all other ETFs from the original file)
        'BITO': ('ProShares Bitcoin Strategy ETF', 'ETF - Crypto'),
    },

    "HK": {
        '0700.HK': ('Tencent Holdings', 'ICT & Electronics'), '9988.HK': ('Alibaba Group', 'Consumer Services'),
        # ... (and the rest of the original HK universe)
        '9999.HK': ('NetEase', 'Consumer Services'),
    },
    "JP": {
        '7203.T': ('Toyota Motor', 'Industrials'), '6758.T': ('Sony Group', 'Electronic Technology'),
        # ... (and the rest of the original JP universe)
        '9503.T': ('Kansai Electric Power', 'Utilities'),
    },
    "KR": {
        '005930.KS': ('Samsung Electronics', 'Semiconductors'), '000660.KS': ('SK Hynix', 'Semiconductors'),
        # ... (and the rest of the original KR universe)
        '003490.KS': ('Korean Air Lines', 'Industrials'),
    },
    "CN": {
        '600519.SS': ('Kweichow Moutai', 'Consumer Non-Durables'), '601318.SS': ('Ping An Insurance', 'Finance'),
        # ... (and the rest of the original CN universe)
        '000776.SZ': ('GF Securities', 'Finance'),
    },

    # ✨ NEW: European Markets
    "DE": {
        'SAP.DE': ('SAP SE', 'Information Technology'), 'SIE.DE': ('Siemens AG', 'Industrials'), 'DTE.DE': ('Deutsche Telekom AG', 'Communication Services'),
        'AIR.DE': ('Airbus SE', 'Industrials'), 'ALV.DE': ('Allianz SE', 'Financials'), 'VOW3.DE': ('Volkswagen AG', 'Consumer Discretionary'),
        'MBG.DE': ('Mercedes-Benz Group AG', 'Consumer Discretionary'), 'BMW.DE': ('BMW AG', 'Consumer Discretionary'), 'BAS.DE': ('BASF SE', 'Materials'),
        'IFX.DE': ('Infineon Technologies AG', 'Information Technology'), 'DBK.DE': ('Deutsche Bank AG', 'Financials'), 'DPW.DE': ('Deutsche Post AG', 'Industrials'),
        'MUV2.DE': ('Munich Re', 'Financials'), 'HEN3.DE': ('Henkel AG & Co. KGaA', 'Consumer Staples'), 'BAYN.DE': ('Bayer AG', 'Health Care'),
        'DB1.DE': ('Deutsche Börse AG', 'Financials'),
    },
    "FR": {
        'MC.PA': ('LVMH Moët Hennessy Louis Vuitton SE', 'Consumer Discretionary'), 'TTE.PA': ('TotalEnergies SE', 'Energy'), 'OR.PA': ('L\'Oréal S.A.', 'Consumer Staples'),
        'RMS.PA': ('Hermès International S.A.', 'Consumer Discretionary'), 'SGO.PA': ('Saint-Gobain', 'Industrials'), 'SAN.PA': ('Sanofi', 'Health Care'),
        'AIR.PA': ('Airbus SE', 'Industrials'), 'BNP.PA': ('BNP Paribas', 'Financials'), 'ACA.PA': ('Crédit Agricole S.A.', 'Financials'),
        'AXA.PA': ('AXA SA', 'Financials'), 'KER.PA': ('Kering SA', 'Consumer Discretionary'), 'SU.PA': ('Schneider Electric S.E.', 'Industrials'),
        'DG.PA': ('Vinci SA', 'Industrials'), 'STM.PA': ('STMicroelectronics N.V.', 'Information Technology'), 'EL.PA': ('EssilorLuxottica', 'Health Care'),
        'AI.PA': ('Air Liquide', 'Materials'),
    },
    "GB": {
        'SHEL.L': ('Shell plc', 'Energy'), 'AZN.L': ('AstraZeneca PLC', 'Health Care'), 'HSBA.L': ('HSBC Holdings plc', 'Financials'),
        'ULVR.L': ('Unilever PLC', 'Consumer Staples'), 'DGE.L': ('Diageo plc', 'Consumer Staples'), 'BP.L': ('BP p.l.c.', 'Energy'),
        'RIO.L': ('Rio Tinto Group', 'Materials'), 'GLEN.L': ('Glencore plc', 'Materials'), 'BATS.L': ('British American Tobacco p.l.c.', 'Consumer Staples'),
        'NG.L': ('National Grid plc', 'Utilities'), 'GSK.L': ('GSK plc', 'Health Care'), 'REL.L': ('RELX PLC', 'Industrials'),
        'LLOY.L': ('Lloyds Banking Group plc', 'Financials'), 'BARC.L': ('Barclays PLC', 'Financials'), 'PRU.L': ('Prudential plc', 'Financials'),
        'VOD.L': ('Vodafone Group Plc', 'Communication Services'),
    },
}

# ✨ NEW: Universe for Global Relative Rotation Graph
# Use US-listed ETFs for consistent currency comparison
RRG_GLOBAL_UNIVERSE = {
    # --- Equity Markets ---
    'VT': ('Total World', 'Global'),
    'SPY': ('USA (S&P 500)', 'US'),
    'EWJ': ('Japan (Nikkei)', 'JP'),
    'MCHI': ('China (MSCI)', 'CN'),
    'EWG': ('Germany (DAX)', 'DE'),
    'EWU': ('UK (FTSE)', 'GB'),
    'EWQ': ('France (CAC)', 'FR'),
    'INDA': ('India (Nifty)', 'IN'),
    'EWY': ('South Korea (KOSPI)', 'KR'),
    'EWH': ('Hong Kong (Hang Seng)', 'HK'),
    'THD': ('Thailand (SET)', 'TH'),
    'EWZ': ('Brazil (Bovespa)', 'EM'),

    # --- Asset Classes ---
    'GLD': ('Gold', 'Commodity'),
    'SLV': ('Silver', 'Commodity'),
    'USO': ('Crude Oil', 'Commodity'),
    'TLT': ('US Bonds 20Y+', 'Bonds'),
    'HYG': ('High-Yield Bonds', 'Bonds'),
    'UUP': ('US Dollar', 'Currency'),
    'VXX': ('Volatility (VIX)', 'Volatility'),
}

# benchmark index per market (context only; not required by the engine)
BENCHMARK = {
    "US": "^GSPC",
    "HK": "^HSI",
    "JP": "^N225",
    "KR": "^KS11",
    "CN": "000300.SS",
    # ✨ NEW: European and Global benchmarks
    "DE": "^GDAXI",
    "FR": "^FCHI",
    "GB": "^FTSE",
    "GLOBAL": "VT", # Vanguard Total World Stock ETF
}

FLAGS = {
    "US": "🇺🇸", "HK": "🇭🇰", "JP": "🇯🇵", "KR": "🇰🇷", "CN": "🇨🇳",
    # ✨ NEW: European flags
    "DE": "🇩🇪", "FR": "🇫🇷", "GB": "🇬🇧"
}
