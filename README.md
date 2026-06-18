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
2. File → Open notebook → GitHub → ใส่ `nangsin1990/stock-homework-dashboard`
3. เปิดไฟล์ `Stock_Homework_Dashboard.ipynb`
4. ใส่ ngrok token ใน Cell 2 (สมัครฟรีที่ [ngrok.com](https://ngrok.com))
5. Runtime → Run all → เปิด URL ที่ได้

### Option B — รันบนเครื่องตัวเอง

```bash
git clone https://github.com/nangsin1990/stock-homework-dashboard.git
cd stock-homework-dashboard
pip install -r requirements.txt
uvicorn backend:app --host 0.0.0.0 --port 8000
# เปิด http://localhost:8000
```

---

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
stock-homework-dashboard/
├── backend.py          ← FastAPI: /api/status, /api/dashboard, /api/search
├── pipeline.py         ← Orchestration: fetch → compute → JSON
├── data_io.py          ← yfinance batch downloader + TTL cache
├── data_engine.py      ← Quant math: indicators, scanners, RS, breadth
├── cache_utils.py      ← Simple TTL cache (no Streamlit dep)
├── universe.py         ← 913 tickers across 6 markets + theme tags
├── requirements.txt
├── Procfile            ← สำหรับ Railway/Render deploy
├── runtime.txt
├── static/
│   └── index.html      ← Dashboard SPA (vanilla JS)
└── Stock_Homework_Dashboard.ipynb  ← Google Colab notebook
```

---

## 🌐 Deploy ฟรีถาวร (ไม่ต้องใช้ Colab)

- **Railway.app** — push repo นี้ขึ้น Railway → deploy อัตโนมัติ URL คงที่
- **Render.com** — connect GitHub repo → deploy ฟรี (sleep หลัง idle)
- **Oracle Cloud Free** — ARM VM 4 CPU 24GB ฟรีตลอดไป (ดูวิธีใน `Oracle_Cloud_Setup_Guide.md`)
