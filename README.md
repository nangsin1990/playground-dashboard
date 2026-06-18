# 🛝 สนามเด็กเล่น Playground — Market Dashboard

Quantitative Market Breadth + Confluence Scanner  
**Real data from Yahoo Finance · FastAPI backend · ngrok tunnel**

Universe: S&P500 + Nasdaq100 + ETF100 · SET100 · HSI · Nikkei225 · KOSPI200 · CSI300 (~913 tickers)

---

## ▶️ รันใน Google Colab (ง่ายที่สุด)

เปิด notebook นี้ใน Colab ได้เลย:

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/nangsin1990/stock-homework-dashboard/blob/main/Stock_Homework_Dashboard.ipynb)

หรือทำตามขั้นตอนด้านล่าง:

---

## 🚀 วิธีใช้งาน

### Option A — Google Colab + ngrok (แนะนำ)

1. เปิด [colab.research.google.com](https://colab.research.google.com)
2. File → Open notebook → GitHub → ใส่ `nangsin1990/playground-dashboard`
3. เปิดไฟล์ `Stock_Homework_Dashboard.ipynb`
4. ใส่ ngrok token ใน Cell 2 (สมัครฟรีที่ [ngrok.com](https://ngrok.com))
5. Runtime → Run all → เปิด URL ที่ได้


## 📡 API Endpoints

| Path | Description |
|------|-------------|
| `GET /` | Dashboard UI |
| `GET /api/status` | Health check |
| `GET /api/dashboard?mode=core` | Core (~126 tickers, เร็ว) |
| `GET /api/dashboard?mode=full` | Full (~913 tickers) |
| `GET /api/dashboard?market=TH` | Filter เฉพาะตลาดไทย |
| `GET /api/search?q=finance` | ค้นหาใน watchlist |

---

## 📁 โครงสร้างไฟล์

```
playground-dashboard/
│
├── backend.py                  ← FastAPI API Gateway
│                                /api/status
│                                /api/dashboard
│                                /api/search
│                                /api/watchlist
│                                /api/portfolio
│
├── pipeline.py                 ← Data Pipeline Orchestration
│                                fetch → compute → cache → JSON
│
├── data_io.py                  ← yfinance downloader
│                                batch fetch + TTL cache
│
├── data_engine.py              ← Quant Engine
│                                indicators
│                                RS ranking
│                                VDU
│                                Pocket Pivot
│                                BGU
│                                Breadth
│                                Market Regime
│
├── universe.py                 ← Global Universe
│                                913 tickers
│                                6 markets
│                                theme mapping
│
├── cache_utils.py              ← TTL Cache
│
├── economic_calendar.py        ← Economic Events
│                                CPI
│                                NFP
│                                FOMC
│
├── leadership_board.py         ← RS Leaders
│                                Top Momentum
│                                Top Breakout
│
├── rotation_rrg.py             ← Relative Rotation Graph
│
├── thematic_matrix.py          ← Theme Ranking
│                                Theme Rotation
│
├── portfolio_engine.py         ← Portfolio Analytics
│                                Position Size
│                                Exposure
│                                Allocation
│
├── risk_engine.py              ← Risk Analytics
│                                Max Drawdown
│                                Volatility
│                                Sharpe Ratio
│
├── watchlist_engine.py         ← Watchlist Intelligence
│                                Signals
│                                RS Changes
│                                Breakout Tracking
│
├── alert_engine.py             ← Smart Alerts
│                                Email
│                                Telegram
│
├── journal_engine.py           ← Trading Journal
│                                Trade Log
│                                Win Rate
│                                Expectancy
│
├── requirements.txt
├── Procfile
├── runtime.txt
│
├── static/
│   │
│   ├── index.html              ← Playground Dashboard
│   ├── app.js
│   ├── styles.css
│   │
│   ├── pages/
│   │   ├── overview.html
│   │   ├── global_market.html
│   │   ├── scanner.html
│   │   ├── themes.html
│   │   ├── leadership.html
│   │   ├── rotation.html
│   │   ├── portfolio.html
│   │   ├── risk.html
│   │   ├── watchlist.html
│   │   └── calendar.html
│   │
│   └── assets/

```

---

## 🌐 Deploy ฟรีถาวร (ไม่ต้องใช้ Colab)

- **Railway.app** — push repo นี้ขึ้น Railway → deploy อัตโนมัติ URL คงที่
- **Render.com** — connect GitHub repo → deploy ฟรี (sleep หลัง idle)
- **Oracle Cloud Free** — ARM VM 4 CPU 24GB ฟรีตลอดไป (ดูวิธีใน `Oracle_Cloud_Setup_Guide.md`)
