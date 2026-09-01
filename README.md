# Playground Dashboard

แดชบอร์ดดูภาพรวมตลาดหลายประเทศ ใช้ข้อมูลหลักจาก Yahoo Finance (`yfinance`)
มี FastAPI เป็น backend และหน้า HTML/JS แยกตามหน้าที่

สถานะ: ใช้ศึกษา/วิจัยได้ แต่ยังไม่ควรเปิด public โดยไม่ใส่ API key + reverse proxy

---

## สิ่งที่มีจริงในแพ็กเกจนี้

- Market breadth / regime
- Leadership + Laggards
- Screener
- Theme matrix
- Rotation (RRG)
- ETF board
- Global market
- Economic calendar (FRED ถ้ามี key, ไม่มีแล้วใช้ fallback ที่ติดป้ายชัด)
- Earnings board / event impact
- Technicals รายตัว, sector RS, dividends, options IV
- Personal watchlist

สิ่งที่ README เก่าพูดถึงแต่ยังไม่มีโมดูลในซอร์สชุดนี้:
- Portfolio Analytics
- Risk Dashboard
- Trading Journal
- Smart Alerts

---

## โครงสร้างไฟล์จริง

```
playground-dashboard-main/
├── backend.py
├── security.py
├── pipeline.py
├── data_io.py
├── data_engine.py
├── cache_utils.py
├── universe.py
├── constants.py
├── market_regime.py
├── leadership.py
├── screener.py
├── global_market.py
├── economic_calendar.py
├── earnings_board.py
├── event_impact.py
├── rotation_rrg.py
├── thematic_matrix.py
├── correlation.py
├── etf_board.py
├── etf_meta.py
├── technical_analysis.py
├── personal_watchlist.py
├── index.html / stock.html / global.html / etf.html
├── leaders.html / laggards.html / thematic.html / rotation.html
├── screener.html / correlation.html / calendar.html
├── earnings.html / events.html
├── style.css
├── nav.js
├── requirements.txt
├── requirements-colab.txt
├── Procfile
├── runtime.txt
├── run.sh
└── colab_start.py
```

---

## รันบนเครื่อง

```bash
cd playground-dashboard-main
bash run.sh
```

สคริปต์จะสร้าง `.venv` แล้วเปิดที่ `http://localhost:8000`

ถ้าจะเปิดออกเน็ต ให้ตั้งค่าอย่างน้อย:

```bash
export DASHBOARD_API_KEY="ใส่คีย์ยาวๆ"
export DASHBOARD_ADMIN_KEY="คีย์แยกสำหรับล้าง cache"
export DASHBOARD_CORS_ORIGINS="https://your-domain.example"
export DASHBOARD_ALLOW_TUNNELS=0
export DASHBOARD_CACHE_SECRET="สุ่มยาวๆ"
export FRED_API_KEY="ถ้ามี"
```

---

## Google Colab

ใช้ `colab_start.py` และติดตั้งจาก `requirements-colab.txt` (มี `pyngrok`)
ใส่ `NGROK_TOKEN` ในเซลล์ อย่า commit token ขึ้น GitHub
แนะนำใส่ basic auth ของ ngrok ด้วย เพราะ API แพงต่อการดึง Yahoo

---

## API ที่ใช้จริง

| Method | Endpoint | หมายเหตุ |
|---|---|---|
| GET | `/` | หน้า dashboard |
| GET | `/api/health/live` | process ยังอยู่ |
| GET | `/api/health/ready` | พร้อมรับ request |
| GET | `/api/health/data` | คุณภาพข้อมูล / coverage |
| GET | `/api/status` | boot + cache + fetch stage |
| GET | `/api/dashboard?mode=core\|full` | ชุดหลัก |
| GET | `/api/progress` | progress แยกตามตลาด |
| GET | `/api/regime` | market regime |
| GET | `/api/search?q=` | ค้นใน watchlist |
| GET | `/api/screener` | ไม่ใช่ `/api/scanner` |
| GET | `/api/thematic` | ไม่ใช่ `/api/themes` |
| GET | `/api/leadership` `/api/laggards` | |
| GET | `/api/rotation` `/api/global` `/api/etf` | |
| GET | `/api/calendar` `/api/earnings_board` `/api/event_impact` | |
| GET | `/api/correlation` `/api/my_watchlist` | |
| GET | `/api/technicals?ticker=` | rate-limit เข้มกว่า |
| GET | `/api/sector_rs` `/api/earnings` `/api/dividends` `/api/options_iv` | |
| POST | `/api/admin/refresh` | ล้าง cache — มี cooldown ไม่ใช้ GET |

`?refresh=1` บน GET ถูกตัดแล้ว เพราะเป็นการเปลี่ยนสถานะระบบ

---

## คุณภาพข้อมูล

ระบบไม่ขึ้นว่า Yahoo OK ถ้าโหลดจักรวาลได้ไม่ครบ
ดูที่ `feed_status.yahoo_status` = `ok | partial | down` และ `data_quality`

ปฏิทินเศรษฐกิจถ้าไม่มี FRED จะติด `source_status=fallback` และ `is_fallback=true`

---

## Disclaimer

ใช้เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน
