"""
ETF Metadata Database
ข้อมูล ETF ที่ hardcode ไว้ (yfinance ไม่ให้ expense ratio / category แบบ structured)
ครอบคลุม Top 100 most-active US ETFs ใน universe.py
"""

# Symbol -> {name, category, sub, index, er, aum_b, desc}
# er = expense ratio (%), aum_b = AUM in billion USD (approx)
# category: Broad | Sector | FixedIncome | Commodity | Leveraged | EM | Volatility | Crypto | Currency | Thematic

ETF_META = {
    # ── BROAD MARKET ────────────────────────────────────────────────────────
    "SPY":  {"name":"SPDR S&P 500 ETF Trust",             "cat":"Broad Market",  "sub":"US Large Cap",    "index":"S&P 500",              "er":0.0945,"aum":490,  "desc":"ETF หลักติดตาม S&P 500 — ใหญ่ที่สุดในโลก"},
    "QQQ":  {"name":"Invesco QQQ Trust",                   "cat":"Broad Market",  "sub":"US Tech/Growth",  "index":"Nasdaq-100",           "er":0.20,  "aum":240,  "desc":"Nasdaq-100 เน้น Tech/Growth"},
    "IWM":  {"name":"iShares Russell 2000 ETF",            "cat":"Broad Market",  "sub":"US Small Cap",    "index":"Russell 2000",         "er":0.19,  "aum":64,   "desc":"หุ้น Small Cap สหรัฐ 2000 ตัว"},
    "DIA":  {"name":"SPDR Dow Jones Industrial ETF",       "cat":"Broad Market",  "sub":"US Blue Chip",    "index":"Dow Jones 30",         "er":0.16,  "aum":33,   "desc":"Dow Jones 30 หุ้น Blue Chip"},
    "VTI":  {"name":"Vanguard Total Stock Market ETF",     "cat":"Broad Market",  "sub":"US Total Market", "index":"CRSP US Total Market", "er":0.03,  "aum":380,  "desc":"ตลาดหุ้น US ทั้งหมด ค่าธรรมเนียมถูกที่สุด"},
    "RSP":  {"name":"Invesco S&P 500 Equal Weight ETF",    "cat":"Broad Market",  "sub":"US Equal Weight", "index":"S&P 500 Equal Weight", "er":0.20,  "aum":55,   "desc":"S&P 500 แบบ Equal Weight ไม่เน้น Big Tech"},

    # ── SECTOR EQUITY ────────────────────────────────────────────────────────
    "XLK":  {"name":"Technology Select Sector SPDR",       "cat":"Sector",        "sub":"Technology",      "index":"S&P 500 Tech",         "er":0.09,  "aum":67,   "desc":"AAPL, MSFT, NVDA — Tech sector"},
    "XLF":  {"name":"Financial Select Sector SPDR",        "cat":"Sector",        "sub":"Finance",         "index":"S&P 500 Financial",    "er":0.09,  "aum":38,   "desc":"JPM, BAC, WFC — Finance sector"},
    "XLE":  {"name":"Energy Select Sector SPDR",           "cat":"Sector",        "sub":"Energy",          "index":"S&P 500 Energy",       "er":0.09,  "aum":35,   "desc":"XOM, CVX — Energy sector"},
    "XLV":  {"name":"Health Care Select Sector SPDR",      "cat":"Sector",        "sub":"Healthcare",      "index":"S&P 500 Health Care",  "er":0.09,  "aum":38,   "desc":"UNH, JNJ, PFE — Healthcare sector"},
    "XLI":  {"name":"Industrial Select Sector SPDR",       "cat":"Sector",        "sub":"Industrials",     "index":"S&P 500 Industrials",  "er":0.09,  "aum":25,   "desc":"CAT, HON, UPS — Industrials sector"},
    "XLY":  {"name":"Consumer Discretionary Select SPDR",  "cat":"Sector",        "sub":"Consumer Disc.",  "index":"S&P 500 Cons. Disc.",  "er":0.09,  "aum":22,   "desc":"AMZN, TSLA, HD — Consumer Discretionary"},
    "XLP":  {"name":"Consumer Staples Select Sector SPDR", "cat":"Sector",        "sub":"Consumer Stap.",  "index":"S&P 500 Cons. Stap.",  "er":0.09,  "aum":16,   "desc":"PG, KO, COST — Consumer Staples"},
    "XLU":  {"name":"Utilities Select Sector SPDR",        "cat":"Sector",        "sub":"Utilities",       "index":"S&P 500 Utilities",    "er":0.09,  "aum":14,   "desc":"NEE, DUK — Utilities ปันผลสูง"},
    "XLB":  {"name":"Materials Select Sector SPDR",        "cat":"Sector",        "sub":"Materials",       "index":"S&P 500 Materials",    "er":0.09,  "aum":7,    "desc":"LIN, SHW, FCX — Materials sector"},
    "XLC":  {"name":"Communication Services Select SPDR",  "cat":"Sector",        "sub":"Comm. Services",  "index":"S&P 500 Comm. Svc.",   "er":0.09,  "aum":18,   "desc":"GOOGL, META, NFLX — Communication"},
    "XOP":  {"name":"SPDR Oil & Gas Exploration ETF",      "cat":"Sector",        "sub":"Oil & Gas E&P",   "index":"S&P O&G Exp & Prod",   "er":0.35,  "aum":4,    "desc":"E&P companies — upstream oil & gas"},
    "XBI":  {"name":"SPDR S&P Biotech ETF",                "cat":"Sector",        "sub":"Biotech",         "index":"S&P Biotech",          "er":0.35,  "aum":7,    "desc":"Biotech equal weight — high risk/reward"},
    "SMH":  {"name":"VanEck Semiconductor ETF",            "cat":"Sector",        "sub":"Semiconductors",  "index":"MVIS US Listed Semicon","er":0.35,  "aum":22,   "desc":"NVDA, TSM, ASML — Semiconductors"},
    "IBB":  {"name":"iShares Biotechnology ETF",           "cat":"Sector",        "sub":"Biotech",         "index":"ICE Biotech",          "er":0.45,  "aum":8,    "desc":"Biotech market cap weighted"},
    "KRE":  {"name":"SPDR S&P Regional Banking ETF",       "cat":"Sector",        "sub":"Regional Banks",  "index":"S&P Regional Banks",   "er":0.35,  "aum":3,    "desc":"Regional banks — sensitive to rates"},
    "KBE":  {"name":"SPDR S&P Bank ETF",                   "cat":"Sector",        "sub":"Banks",           "index":"S&P Banks",            "er":0.35,  "aum":2,    "desc":"ธนาคารสหรัฐ broader than KRE"},
    "OIH":  {"name":"VanEck Oil Services ETF",             "cat":"Sector",        "sub":"Oil Services",    "index":"MVIS Oil Services",    "er":0.35,  "aum":2,    "desc":"SLB, HAL — oilfield services"},
    "ITB":  {"name":"iShares U.S. Home Construction ETF",  "cat":"Sector",        "sub":"Homebuilders",    "index":"Dow Jones Homebuild",  "er":0.40,  "aum":3,    "desc":"DHI, LEN — บ้านและก่อสร้าง"},
    "XHB":  {"name":"SPDR S&P Homebuilders ETF",           "cat":"Sector",        "sub":"Homebuilders",    "index":"S&P Homebuilders",     "er":0.35,  "aum":1,    "desc":"Homebuilders + related retail"},
    "XRT":  {"name":"SPDR S&P Retail ETF",                 "cat":"Sector",        "sub":"Retail",          "index":"S&P Retail",           "er":0.35,  "aum":1,    "desc":"Retail sector equal weight"},
    "XME":  {"name":"SPDR S&P Metals & Mining ETF",        "cat":"Sector",        "sub":"Metals & Mining", "index":"S&P Metals & Mining",  "er":0.35,  "aum":2,    "desc":"Steel, copper, gold miners"},
    "IYR":  {"name":"iShares U.S. Real Estate ETF",        "cat":"Sector",        "sub":"Real Estate",     "index":"Dow Jones US RE",      "er":0.40,  "aum":5,    "desc":"REITs and real estate companies"},
    "IHI":  {"name":"iShares U.S. Medical Devices ETF",    "cat":"Sector",        "sub":"Med Devices",     "index":"Dow Jones US Med Dev", "er":0.40,  "aum":5,    "desc":"MDT, ABT, SYK — medical devices"},
    "JETS": {"name":"U.S. Global Jets ETF",                "cat":"Thematic",      "sub":"Airlines",        "index":"U.S. Global Jets",     "er":0.60,  "aum":1,    "desc":"Airlines & travel sector"},
    "ICLN": {"name":"iShares Global Clean Energy ETF",     "cat":"Thematic",      "sub":"Clean Energy",    "index":"S&P Global Clean Enrg","er":0.40,  "aum":2,    "desc":"Clean energy global"},
    "TAN":  {"name":"Invesco Solar ETF",                   "cat":"Thematic",      "sub":"Solar Energy",    "index":"MAC Global Solar",     "er":0.69,  "aum":1,    "desc":"Solar energy companies"},
    "ARKK": {"name":"ARK Innovation ETF",                  "cat":"Thematic",      "sub":"Disruptive Tech", "index":"Active Managed",       "er":0.75,  "aum":6,    "desc":"Actively managed — TSLA, CRISPR, AI"},
    "ARKG": {"name":"ARK Genomic Revolution ETF",          "cat":"Thematic",      "sub":"Genomics",        "index":"Active Managed",       "er":0.75,  "aum":1,    "desc":"Genomics, bioinformatics, CRISPR"},
    "MSOS": {"name":"AdvisorShares Pure US Cannabis ETF",  "cat":"Thematic",      "sub":"Cannabis",        "index":"Active Managed",       "er":0.80,  "aum":0.3,  "desc":"US cannabis companies"},
    "SCHD": {"name":"Schwab US Dividend Equity ETF",       "cat":"Sector",        "sub":"Dividend",        "index":"Dow Jones US Div 100", "er":0.06,  "aum":57,   "desc":"หุ้นปันผลสูง คุณภาพดี ค่าธรรมเนียมถูก"},

    # ── FIXED INCOME ────────────────────────────────────────────────────────
    "TLT":  {"name":"iShares 20+ Year Treasury Bond ETF",  "cat":"Fixed Income",  "sub":"Long-Term Bond",  "index":"ICE 20+ Yr Treasury",  "er":0.15,  "aum":55,   "desc":"พันธบัตรรัฐบาล US อายุ 20+ ปี — inverse กับ yield"},
    "IEF":  {"name":"iShares 7-10 Year Treasury Bond ETF", "cat":"Fixed Income",  "sub":"Mid-Term Bond",   "index":"ICE 7-10 Yr Treasury", "er":0.15,  "aum":30,   "desc":"พันธบัตรรัฐบาล US อายุ 7-10 ปี"},
    "HYG":  {"name":"iShares iBoxx USD High Yield Corp",   "cat":"Fixed Income",  "sub":"High Yield Bond", "index":"Markit iBoxx USD HY",  "er":0.48,  "aum":17,   "desc":"High Yield Bond — risk appetite indicator"},
    "LQD":  {"name":"iShares iBoxx USD Inv Grade Corp",    "cat":"Fixed Income",  "sub":"IG Corp Bond",    "index":"Markit iBoxx USD IG",  "er":0.14,  "aum":35,   "desc":"Investment Grade Corporate Bond"},
    "EMB":  {"name":"iShares JP Morgan USD EM Bond ETF",   "cat":"Fixed Income",  "sub":"EM Bond",         "index":"JPM EMBI Global Core", "er":0.39,  "aum":14,   "desc":"Emerging Market sovereign bonds USD"},

    # ── COMMODITY ────────────────────────────────────────────────────────────
    "GLD":  {"name":"SPDR Gold Shares",                    "cat":"Commodity",     "sub":"Gold",            "index":"Gold Spot Price",      "er":0.40,  "aum":68,   "desc":"ทองคำ — safe haven, hedge inflation"},
    "IAU":  {"name":"iShares Gold Trust",                  "cat":"Commodity",     "sub":"Gold",            "index":"Gold Spot Price",      "er":0.25,  "aum":34,   "desc":"ทองคำ ค่าธรรมเนียมถูกกว่า GLD"},
    "SLV":  {"name":"iShares Silver Trust",                "cat":"Commodity",     "sub":"Silver",          "index":"Silver Spot Price",    "er":0.50,  "aum":11,   "desc":"เงิน — industrial + safe haven"},
    "GDX":  {"name":"VanEck Gold Miners ETF",              "cat":"Commodity",     "sub":"Gold Miners",     "index":"NYSE Arca Gold Miners","er":0.51,  "aum":14,   "desc":"บริษัทขุดทอง — leverage กับราคาทอง"},
    "GDXJ": {"name":"VanEck Junior Gold Miners ETF",       "cat":"Commodity",     "sub":"Jr. Gold Miners", "index":"MVIS Jr Gold Miners",  "er":0.52,  "aum":4,    "desc":"Junior gold miners — higher beta"},
    "USO":  {"name":"United States Oil Fund",              "cat":"Commodity",     "sub":"Crude Oil",       "index":"WTI Crude Oil Futures","er":0.60,  "aum":1,    "desc":"น้ำมันดิบ WTI futures"},
    "UNG":  {"name":"United States Natural Gas Fund",      "cat":"Commodity",     "sub":"Natural Gas",     "index":"Natural Gas Futures",  "er":1.11,  "aum":0.4,  "desc":"ก๊าซธรรมชาติ — volatile มาก"},
    "URA":  {"name":"Global X Uranium ETF",                "cat":"Commodity",     "sub":"Uranium",         "index":"Solactive Global Ura.","er":0.69,  "aum":3,    "desc":"ยูเรเนียม — clean energy play"},
    "URNM": {"name":"Sprott Uranium Miners ETF",           "cat":"Commodity",     "sub":"Uranium Miners",  "index":"North Shore Uranium",  "er":0.83,  "aum":1,    "desc":"ยูเรเนียมและเหมืองยูเรเนียม"},
    "SILJ": {"name":"ETFMG Prime Junior Silver Miners",    "cat":"Commodity",     "sub":"Silver Miners",   "index":"Prime Junior Silver",  "er":0.69,  "aum":0.3,  "desc":"Junior silver mining companies"},
    "NUGT": {"name":"Direxion Daily Gold Miners Bull 2x",  "cat":"Leveraged",     "sub":"Gold Miners 2x",  "index":"NYSE Arca Gold Miners","er":1.03,  "aum":0.5,  "desc":"Gold Miners 2x leveraged — daily rebalance"},
    "JNUG": {"name":"Direxion Daily Jr Gold Miners 2x",    "cat":"Leveraged",     "sub":"Jr Gold Miners 2x","index":"MVIS Jr Gold Miners", "er":1.12,  "aum":0.2,  "desc":"Junior Gold Miners 2x — เสี่ยงสูง"},

    # ── LEVERAGED / INVERSE ──────────────────────────────────────────────────
    "TQQQ": {"name":"ProShares UltraPro QQQ 3x",          "cat":"Leveraged",     "sub":"Nasdaq 3x Bull",  "index":"Nasdaq-100 x3",        "er":0.88,  "aum":22,   "desc":"Nasdaq-100 3เท่า — เสี่ยงสูงมาก"},
    "SQQQ": {"name":"ProShares UltraPro Short QQQ -3x",   "cat":"Leveraged",     "sub":"Nasdaq 3x Bear",  "index":"Nasdaq-100 x-3",       "er":0.95,  "aum":5,    "desc":"Short Nasdaq-100 3เท่า — ใช้ hedge"},
    "SOXL": {"name":"Direxion Daily Semicon Bull 3x",      "cat":"Leveraged",     "sub":"Semicon 3x Bull", "index":"ICE Semicon x3",       "er":0.75,  "aum":9,    "desc":"Semiconductor 3เท่า — ตามชิป AI"},
    "SOXS": {"name":"Direxion Daily Semicon Bear 3x",      "cat":"Leveraged",     "sub":"Semicon 3x Bear", "index":"ICE Semicon x-3",      "er":0.75,  "aum":1,    "desc":"Short Semiconductor 3เท่า"},
    "SPXL": {"name":"Direxion Daily S&P 500 Bull 3x",      "cat":"Leveraged",     "sub":"S&P 500 3x Bull", "index":"S&P 500 x3",           "er":0.91,  "aum":3,    "desc":"S&P 500 3เท่า"},
    "SPXS": {"name":"Direxion Daily S&P 500 Bear 3x",      "cat":"Leveraged",     "sub":"S&P 500 3x Bear", "index":"S&P 500 x-3",          "er":0.91,  "aum":1,    "desc":"Short S&P 500 3เท่า"},
    "UPRO": {"name":"ProShares UltraPro S&P 500 3x",       "cat":"Leveraged",     "sub":"S&P 500 3x Bull", "index":"S&P 500 x3",           "er":0.91,  "aum":2,    "desc":"S&P 500 3เท่า (ProShares version)"},
    "TNA":  {"name":"Direxion Daily Small Cap Bull 3x",    "cat":"Leveraged",     "sub":"Russell 3x Bull", "index":"Russell 2000 x3",      "er":1.01,  "aum":1,    "desc":"Russell 2000 Small Cap 3เท่า"},
    "TZA":  {"name":"Direxion Daily Small Cap Bear 3x",    "cat":"Leveraged",     "sub":"Russell 3x Bear", "index":"Russell 2000 x-3",     "er":1.01,  "aum":0.3,  "desc":"Short Small Cap 3เท่า"},
    "FAS":  {"name":"Direxion Daily Financial Bull 3x",    "cat":"Leveraged",     "sub":"Finance 3x Bull", "index":"Russell 1000 Fin x3",  "er":0.99,  "aum":1,    "desc":"Finance sector 3เท่า"},
    "FAZ":  {"name":"Direxion Daily Financial Bear 3x",    "cat":"Leveraged",     "sub":"Finance 3x Bear", "index":"Russell 1000 Fin x-3", "er":0.99,  "aum":0.3,  "desc":"Short Finance 3เท่า"},
    "LABU": {"name":"Direxion Daily S&P Biotech Bull 3x",  "cat":"Leveraged",     "sub":"Biotech 3x Bull", "index":"S&P Biotech x3",       "er":0.97,  "aum":1,    "desc":"Biotech 3เท่า — volatile มาก"},
    "LABD": {"name":"Direxion Daily S&P Biotech Bear 3x",  "cat":"Leveraged",     "sub":"Biotech 3x Bear", "index":"S&P Biotech x-3",      "er":0.97,  "aum":0.2,  "desc":"Short Biotech 3เท่า"},
    "DPST": {"name":"Direxion Daily Regional Banks Bull 3x","cat":"Leveraged",    "sub":"Banks 3x Bull",   "index":"S&P Regional Banks x3","er":0.99,  "aum":0.3,  "desc":"Regional Banks 3เท่า"},
    "TMF":  {"name":"Direxion Daily 20+ Yr Treasury 3x",   "cat":"Leveraged",     "sub":"TLT 3x Bull",     "index":"ICE 20+ Yr Treas. x3", "er":1.05,  "aum":1,    "desc":"Long-term Treasury Bond 3เท่า"},
    "TBT":  {"name":"ProShares UltraShort 20+ Yr Treas.",  "cat":"Leveraged",     "sub":"TLT 2x Bear",     "index":"ICE 20+ Yr Treas. x-2","er":0.90, "aum":1,    "desc":"Short Long-term Treasury 2เท่า"},
    "SDS":  {"name":"ProShares UltraShort S&P500 -2x",     "cat":"Leveraged",     "sub":"S&P 500 2x Bear", "index":"S&P 500 x-2",          "er":0.90,  "aum":1,    "desc":"Short S&P 500 2เท่า"},
    "SH":   {"name":"ProShares Short S&P500 -1x",          "cat":"Leveraged",     "sub":"S&P 500 1x Bear", "index":"S&P 500 x-1",          "er":0.88,  "aum":2,    "desc":"Short S&P 500 1เท่า — ง่ายสุด hedge"},
    "SDOW": {"name":"ProShares UltraPro Short Dow30 -3x",  "cat":"Leveraged",     "sub":"Dow 3x Bear",     "index":"Dow Jones x-3",        "er":0.95,  "aum":0.3,  "desc":"Short Dow Jones 3เท่า"},
    "TSLL": {"name":"Direxion Daily TSLA Bull 1.5x",       "cat":"Leveraged",     "sub":"TSLA 1.5x Bull",  "index":"TSLA x1.5",            "er":1.07,  "aum":1,    "desc":"Tesla 1.5เท่า — single stock ETF"},
    "NVDS": {"name":"AXS 1.25x NVDA Bear Daily ETF",       "cat":"Leveraged",     "sub":"NVDA 1.25x Bear", "index":"NVDA x-1.25",          "er":1.15,  "aum":0.1,  "desc":"Short NVDA 1.25เท่า"},
    "ERX":  {"name":"Direxion Daily Energy Bull 2x",        "cat":"Leveraged",     "sub":"Energy 2x Bull",  "index":"S&P 500 Energy x2",    "er":0.96,  "aum":0.5,  "desc":"Energy sector 2เท่า"},
    "GUSH": {"name":"Direxion Daily S&P O&G Bull 2x",       "cat":"Leveraged",     "sub":"Oil&Gas 2x Bull", "index":"S&P O&G E&P x2",       "er":1.06,  "aum":0.3,  "desc":"Oil & Gas E&P 2เท่า"},
    "UCO":  {"name":"ProShares Ultra Bloomberg Crude 2x",   "cat":"Leveraged",     "sub":"Crude Oil 2x",    "index":"Bloomberg Crude x2",   "er":0.95,  "aum":0.3,  "desc":"Crude oil futures 2เท่า"},
    "BOIL": {"name":"ProShares Ultra Natural Gas 2x",       "cat":"Leveraged",     "sub":"Nat Gas 2x Bull", "index":"Bloomberg NatGas x2",  "er":0.95,  "aum":0.2,  "desc":"Natural gas 2เท่า — volatile มาก"},
    "KOLD": {"name":"ProShares UltraShort Natural Gas -2x", "cat":"Leveraged",     "sub":"Nat Gas 2x Bear", "index":"Bloomberg NatGas x-2", "er":0.95,  "aum":0.1,  "desc":"Short Natural gas 2เท่า"},
    "YINN": {"name":"Direxion Daily FTSE China Bull 3x",    "cat":"Leveraged",     "sub":"China 3x Bull",   "index":"FTSE China 50 x3",     "er":1.45,  "aum":0.5,  "desc":"จีน 3เท่า — YANG คือตรงข้าม"},
    "YANG": {"name":"Direxion Daily FTSE China Bear 3x",    "cat":"Leveraged",     "sub":"China 3x Bear",   "index":"FTSE China 50 x-3",    "er":1.45,  "aum":0.3,  "desc":"Short จีน 3เท่า"},

    # ── VOLATILITY ────────────────────────────────────────────────────────────
    "UVXY": {"name":"ProShares Ultra VIX Short-Term 2x",   "cat":"Volatility",    "sub":"VIX 2x Bull",     "index":"S&P 500 VIX ST Fut x2","er":0.95, "aum":0.5,  "desc":"VIX 2เท่า — ขึ้นเมื่อตลาดตก"},
    "VXX":  {"name":"iPath S&P 500 VIX ST Futures ETN",    "cat":"Volatility",    "sub":"VIX Futures",     "index":"S&P 500 VIX ST Fut",   "er":0.89,  "aum":0.5,  "desc":"VIX short-term futures — decays ช้ากว่า UVXY"},
    "UVIX": {"name":"2x Long VIX Futures ETF",             "cat":"Volatility",    "sub":"VIX 2x Bull",     "index":"S&P 500 VIX ST Fut x2","er":1.28, "aum":0.2,  "desc":"VIX 2เท่า (newer version)"},
    "VIXY": {"name":"ProShares VIX Short-Term Futures ETF","cat":"Volatility",    "sub":"VIX Futures",     "index":"S&P 500 VIX ST Fut",   "er":0.85,  "aum":0.1,  "desc":"VIX futures 1เท่า"},
    "SVXY": {"name":"ProShares Short VIX ST Futures -1x",  "cat":"Volatility",    "sub":"VIX 1x Bear",     "index":"S&P 500 VIX ST Fut x-1","er":0.95,"aum":0.3,  "desc":"Short VIX — ได้เงินเมื่อตลาดสงบ"},

    # ── INTERNATIONAL / EM ────────────────────────────────────────────────────
    "EEM":  {"name":"iShares MSCI Emerging Markets ETF",   "cat":"International", "sub":"Broad EM",        "index":"MSCI Emerging Markets","er":0.68,  "aum":19,   "desc":"Emerging Markets ทั้งหมด — จีน อินเดีย เกาหลี"},
    "EFA":  {"name":"iShares MSCI EAFE ETF",               "cat":"International", "sub":"Developed ex-US", "index":"MSCI EAFE",            "er":0.32,  "aum":56,   "desc":"Developed market นอก US — Europe, Japan, AUS"},
    "FXI":  {"name":"iShares China Large-Cap ETF",         "cat":"International", "sub":"China Large Cap", "index":"FTSE China 50",        "er":0.74,  "aum":5,    "desc":"หุ้นใหญ่จีน H-shares ใน HK"},
    "KWEB": {"name":"KraneShares CSI China Internet ETF",  "cat":"International", "sub":"China Internet",  "index":"CSI Overseas China Int","er":0.70, "aum":5,    "desc":"Internet/Tech จีน BABA, JD, BIDU"},
    "ASHR": {"name":"Xtrackers Harvest CSI 300 A-Shares",  "cat":"International", "sub":"China A-Share",   "index":"CSI 300",              "er":0.65,  "aum":2,    "desc":"CSI 300 A-shares directly"},
    "EWZ":  {"name":"iShares MSCI Brazil ETF",             "cat":"International", "sub":"Brazil",          "index":"MSCI Brazil",          "er":0.59,  "aum":5,    "desc":"ตลาดหุ้นบราซิล"},
    "EWJ":  {"name":"iShares MSCI Japan ETF",              "cat":"International", "sub":"Japan",           "index":"MSCI Japan",           "er":0.50,  "aum":12,   "desc":"ตลาดหุ้นญี่ปุ่น"},
    "EWG":  {"name":"iShares MSCI Germany ETF",            "cat":"International", "sub":"Germany",         "index":"MSCI Germany",         "er":0.50,  "aum":2,    "desc":"ตลาดหุ้นเยอรมัน"},
    "EWA":  {"name":"iShares MSCI Australia ETF",          "cat":"International", "sub":"Australia",       "index":"MSCI Australia",       "er":0.50,  "aum":1,    "desc":"ตลาดหุ้นออสเตรเลีย"},
    "INDA": {"name":"iShares MSCI India ETF",              "cat":"International", "sub":"India",           "index":"MSCI India",           "er":0.64,  "aum":9,    "desc":"ตลาดหุ้นอินเดีย — high growth"},
    "FEZ":  {"name":"SPDR EURO STOXX 50 ETF",              "cat":"International", "sub":"Eurozone",        "index":"Euro Stoxx 50",        "er":0.29,  "aum":3,    "desc":"50 หุ้นใหญ่ Euro zone"},

    # ── CURRENCY ─────────────────────────────────────────────────────────────
    "UUP":  {"name":"Invesco DB US Dollar Index Bullish",  "cat":"Currency",      "sub":"US Dollar",       "index":"Deutsche Bank USD",    "er":0.75,  "aum":0.5,  "desc":"USD long — ขึ้นเมื่อ Dollar แข็ง"},

    # ── CRYPTO ───────────────────────────────────────────────────────────────
    "BITO": {"name":"ProShares Bitcoin Strategy ETF",      "cat":"Crypto",        "sub":"Bitcoin Futures", "index":"BTC Futures",          "er":0.95,  "aum":2,    "desc":"Bitcoin futures ETF — ไม่ได้ถือ BTC จริง"},
}

CATEGORIES = ["Broad Market","Sector","Thematic","Fixed Income","Commodity","Leveraged","Volatility","International","Currency","Crypto"]

CAT_COLORS = {
    "Broad Market":  "#6366f1",
    "Sector":        "#10b981",
    "Thematic":      "#2dd4bf",
    "Fixed Income":  "#3b82f6",
    "Commodity":     "#f59e0b",
    "Leveraged":     "#ef4444",
    "Volatility":    "#8b5cf6",
    "International": "#0ea5e9",
    "Currency":      "#f97316",
    "Crypto":        "#a78bfa",
}

# Tickers for the screener (from universe.py ETFs - all in ETF_META)
ETF_TICKERS = list(ETF_META.keys())
