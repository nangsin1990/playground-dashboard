# 🛝 Playground — Market Dashboard

Quantitative Market Breadth + Confluence Scanner  
**Real data from Yahoo Finance · FastAPI backend · ngrok tunnel**

Universe: S&P500 + Nasdaq100 + ETF100 · SET100 · HSI · Nikkei225 · KOSPI200 · CSI300 (~913 tickers)

A lightweight market intelligence dashboard for systematic investors.

Playground Dashboard combines global market breadth, quantitative stock screening, leadership analysis, thematic investing, ETF monitoring, and sector rotation into a single web application powered primarily by Yahoo Finance data.

The goal is simple:
> 💡 Understand the market, discover opportunities, manage risk, and make better investment decisions.
---

## ▶️ Run as Google Colab (Simple way)

Open > notebook in Colab already:

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/nangsin1990/stock-homework-dashboard/blob/main/Stock_Homework_Dashboard.ipynb)

---

## 🚀 Work instruction

### OGoogle Colab + ngrok (recommend) ###

1. Open [colab.research.google.com](https://colab.research.google.com)
2. File → Open notebook → GitHub → input `nangsin1990/playground-dashboard`
3. Open `playground-dashboard.ipynb`
4. ใส่ ngrok token ใน Cell 2
5. Runtime → Run all → Open URL

---

## ✨ Features

### 📈 Market Overview

Monitor overall market health across multiple countries.

* 🌐 Market Breadth
    * 📈 % Above MA50
    * 📉 % Above MA200
    * 📊 Advance / Decline
    * 🆕 New High / New Low
* 🔍 Market Regime Detection
* 📥 Signal Accumulation
* 🏥 Market Health Indicators

---

### 🌍 Global Market

Track major global asset classes.

* 🇺🇸 Equity Markets
    * 🇺🇸 United States
    * 🇹🇭 Thailand
    * 🇯🇵 Japan
    * 🇭🇰 Hong Kong
    * 🇨🇳 China
    * 🇰🇷 South Korea
* 📦 ETFs
* 📊 Major Indices
* 🏆 Market Performance Rankings

---

### ⚡ Quant Scanner

Find high-quality stock setups automatically.

Supported scans include:

* 💧 Volume Dry-Up (VDU)
* 🎯 Pocket Pivot
* 🚀 Buyable Gap-Up (BGU)
* 🔝 Near 52-Week High
* 💪 Relative Strength Ranking
* 📈 Trend-Based Screening

---

### 👑 Leadership Board

Identify market leaders.

* 🥇 Top Relative Strength Stocks
* 🔥 Momentum Leaders
* 💥 Breakout Candidates
* 🐳 Volume Expansion Leaders

---

### 🌌 Theme Matrix

Monitor capital flows across investment themes.

Examples:

* 🤖 Artificial Intelligence
* 🔌 Semiconductors
* ☁️ Cloud Computing
* 🛡️ Cybersecurity
* 🍃 Clean Energy
* 🦾 Robotics

Features:

* 🏅 Theme Ranking
* 📊 Theme Performance
* 🔄 Theme Rotation
* 👥 Theme Leaders

---

### 🌀 Rotation Chart

Analyze sector and theme rotation.

* 🗺️ Relative Rotation Graph (RRG)
* ⚖️ Relative Strength Ratio
* 🏎️ Relative Momentum
* 🌊 Capital Flow Visualization

---

### 📂 ETF Board

Track major ETFs across multiple asset classes.

* 📌 Index ETFs
* 🏭 Sector ETFs
* 🪙 Commodity ETFs
* 🗺️ International ETFs

---

### 📅 Economic Calendar

Monitor upcoming macroeconomic events.

Examples:

* 🦅 FOMC Meetings
* 🏷️ CPI Releases
* 💼 Employment Reports
* 📦 GDP Announcements

---

### ⭐ Watchlist

Create and monitor personalized stock lists.

* ❤️ Favorite Stocks
* 📡 Signal Tracking
* 🎯 Relative Strength Monitoring

---

### 💼 Portfolio Analytics

Monitor portfolio exposure and allocation.

* 📝 Holdings Overview
* 🍕 Position Allocation
* 🏭 Sector Exposure
* 🗺️ Country Exposure

---

### 🛡️ Risk Dashboard

Measure portfolio risk.

* 📉 Max Drawdown
* 🌊 Volatility
* 📊 Sharpe Ratio
* 🎯 Portfolio Concentration
* ⚠️ Risk Exposure

---

### 📖 Trading Journal

Track and evaluate trading performance.

* 📝 Trade Log
* 🎯 Setup Tracking
* 🎯 Win Rate
* ⚖️ Risk/Reward Analysis
* 📊 Performance Statistics

---

### 🔔 Smart Alerts

Receive notifications for important market events.

* 💥 Breakouts
* 📡 Scanner Signals
* 🔄 Watchlist Updates
* ⚡ Relative Strength Changes

---

## 🔌 Data Sources

Primary Data Source:

* 🟢 Yahoo Finance (yfinance)

Optional Sources:

* 🏛️ FRED Economic Data
* 📊 Trading Economics
* 📅 Custom Economic Calendar Feeds

---

## 🗺️ Supported Markets

* 🇺🇸 United States
* 🇹🇭 Thailand
* 🇯🇵 Japan
* 🇭🇰 Hong Kong
* 🇨🇳 China
* 🇰🇷 South Korea

---

## 🛠️ Technology Stack

**Backend**
* 🐍 Python
* ⚡ FastAPI
* 🐼 Pandas
* 🔢 NumPy
* 🟢 yfinance

**Frontend**
* 🌐 HTML
* 🎨 CSS
* 💛 Vanilla JavaScript

**Deployment**
* 🚂 Railway
* ☁️ Render
* 🚀 Google Colab

---

## 📁 Project Structure

text
playground-dashboard/
│
├── ⚙️ backend.py

📱 NS: ├── ⚙️ pipeline.py
├── ⚙️ data_io.py
├── ⚙️ data_engine.py
├── ⚙️ cache_utils.py
├── ⚙️ universe.py
│
├── 🧠 economic_calendar.py
├── 🧠 leadership_board.py
├── 🧠 rotation_rrg.py
├── 🧠 thematic_matrix.py
├── 🧠 portfolio_engine.py
├── 🧠 risk_engine.py
├── 🧠 watchlist_engine.py
├── 🧠 alert_engine.py
├── 🧠 journal_engine.py
│
├── 📄 requirements.txt
├── 📄 Procfile
├── 📄 runtime.txt
│
├── 📂 static/
│   ├── 🌐 index.html
│   ├── 💛 app.js
│   ├── 🎨 styles.css
│   └── 📂 pages/
│
└── 📓 Playground_Dashboard.ipynb

## 🛣️ API Endpoints
| Method | Endpoint | Description |
|---|---|---|
| 🌐 | / | Dashboard UI |
| 💓 | /api/status | System health check |
| 📊 | /api/dashboard | Dashboard snapshot |
| 🎯 | /api/dashboard?mode=core | Core universe |
| 📦 | /api/dashboard?mode=full | Full universe |
| 🔍 | /api/scanner | Quant scanner |
| 👑 | /api/leadership | Leadership board |
| 🌌 | /api/themes | Theme matrix |
| 🌀 | /api/rotation | Rotation chart |
| 🌍 | /api/global | Global market data |
| 📅 | /api/calendar | Economic calendar |
| 🔎 | /api/search?q=NVDA | Symbol search |
## 🧠 Philosophy
Playground is designed as an investment operating system rather than a traditional stock screener.
The platform focuses on answering five critical questions:
 1. 🏥 Is the market healthy?
 2. 🌊 Where is capital flowing?
 3. 👑 Which stocks are leading?
 4. ⚡ What opportunities exist today?
 5. 🛡️ How much risk should I take?
## ⚠️ Disclaimer
This project is intended for educational and research purposes only.
Nothing in this project should be considered financial advice. Users are responsible for conducting their own research and making their own investment decisions.
```
