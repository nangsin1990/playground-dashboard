# Playground Dashboard

แดชบอร์ดดูภาพรวมตลาดหลายประเทศ ใช้ข้อมูลหลักจาก Yahoo Finance (`yfinance`)
มี FastAPI เป็น backend และหน้า HTML/JS แยกตามหน้าที่

สถานะ: ใช้ศึกษา/วิจัยได้ เวอร์ชัน 6.2
โทเคนใช้แบบเดิม: ใส่ `NGROK_TOKEN` / `FRED_API_KEY` ใน `colab_start.py` ตรงๆ ตอนรัน
API ของแดชบอร์ดไม่บังคับ API key

---

## สิ่งที่มีจริงในแพ็กเกจนี้

- Market breadth / regime
- Leadership + Laggards
- Screener
- Theme matrix
- Rotation (RRG)
- ETF board
- Global market
- Gold Command Center (`gold.py` / `gold.html` / `GET /api/gold`)
- Economic calendar (FRED ถ้ามี key, ไม่มีแล้วใช้ fallback ที่ติดป้ายชัด)
- Earnings board / event impact
- Technicals รายตัว, sector RS, dividends, options IV
- Personal watchlist

สิ่งที่ README เก่าพูดถึงแต่ยังไม่มีโมดูลในซอร์สชุดนี้:
- Portfolio Analytics
- Risk Dashboard
- Trading Journal
- Smart Alerts

## โทเคน (แบบเดิม ไม่ได้เปลี่ยน)

ใส่ใน `colab_start.py` ช่องด้านบนของเซลล์ แล้วรัน:

```python
NGROK_TOKEN = ""
FRED_API_KEY = ""   # ไม่บังคับ
```

อย่า commit ค่าจริงขึ้น GitHub
API แดชบอร์ดยังเปิดใช้ได้โดยไม่ต้องใส่ DASHBOARD_API_KEY
`?refresh=1` ใช้ได้เหมือนเดิม

---

## โครงสร้างไฟล์จริง

```
playground-dashboard-main/
├── backend.py
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

Env var ที่โค้ดจริงอ่าน (จาก `economic_calendar.py`):

```bash
export FRED_API_KEY="ถ้ามี — ไม่มีก็ fallback ไปใช้ static schedule อัตโนมัติ"
```

ตัวแปรอื่น (`DASHBOARD_API_KEY` ฯลฯ) ที่เคยเขียนไว้ในเวอร์ชันก่อนหน้าไม่มีผลกับโค้ดชุดนี้
ดูหัวข้อ "ช่องโหว่ที่ยังไม่ได้แก้" ด้านบน

---

## Google Colab

ใช้ `colab_start.py` และติดตั้งจาก `requirements-colab.txt` (มี `pyngrok`)
ใส่ `NGROK_TOKEN` ในเซลล์ อย่า commit token ขึ้น GitHub
แนะนำใส่ basic auth ของ ngrok ด้วย เพราะ API แพงต่อการดึง Yahoo

---

## API ที่ใช้จริง (ตรงกับ `backend.py`)

| Method | Endpoint | หมายเหตุ |
|---|---|---|
| GET | `/` | หน้า dashboard |
| GET | `/api/health` | เช็คว่า process ยังอยู่ (ไม่มี `/live` `/ready` `/data` แยก) |
| GET | `/api/status` | boot time + เวลาปัจจุบัน |
| GET | `/api/dashboard?mode=core\|full&refresh=1` | ชุดหลัก — `refresh=1` ล้าง cache ก่อนดึงใหม่ |
| GET | `/api/my_watchlist?mode=core\|full` | watchlist ส่วนตัว (25 ticker ใน `personal_watchlist.py`) |
| GET | `/api/progress` | progress แยกตามตลาด (ใช้ตอนกำลังโหลด full) |
| GET | `/api/regime` | market regime |
| GET | `/api/search?q=` | ค้นใน watchlist |
| GET | `/api/screener` `/api/thematic` `/api/leadership` `/api/laggards` | รับ `refresh=1` ได้เหมือนกัน |
| GET | `/api/screener/presets` | ดึง preset ที่บันทึกไว้ทั้งหมด (Drive-backed, ดูหัวข้อด้านล่าง) |
| POST | `/api/screener/presets` | บันทึก/แก้ preset — body `{"name": "...", "filters": {...}}` |
| DELETE | `/api/screener/presets/{name}` | ลบ preset |
| GET | `/api/rotation?mode=&market=` `/api/global` `/api/etf` | |
| GET | `/api/gold?refresh=1` | Gold Command Center — ล้างเฉพาะ cache ทอง ไม่แตะ pipeline หุ้น |
| GET | `/api/calendar` `/api/earnings_board` `/api/event_impact` `/api/correlation` | |
| GET | `/api/technicals?ticker=` `/api/sector_rs?ticker=&theme=` `/api/earnings?ticker=` `/api/dividends?ticker=` `/api/options_iv?ticker=` | |

**ไม่มี** `POST /api/admin/refresh` แยกต่างหาก — การล้าง cache ทำผ่าน query param
`?refresh=1` บน endpoint GET ที่มี `Depends(get_cache_clearer(...))` แนบอยู่ (คนละแบบกับที่
README เวอร์ชันก่อนบอกว่า "`?refresh=1` บน GET ถูกตัดแล้ว")

### Cache pre-warm (ใหม่)

`backend.py` มี background thread (`_prewarm_loop`, เริ่มตอน FastAPI lifespan)
ยิง `pipeline.load_market_pack("core")` ล่วงหน้าทุก `PREWARM_INTERVAL_SEC`
(ตั้งไว้ 14 นาที ใน `constants.py`, สั้นกว่า `CACHE_TTL_DATA` 15 นาทีเล็กน้อย)
ไม่ pre-warm `full` เพราะกด Yahoo หนักถ้าเปิดค้างทั้งวัน
มี `POST /api/admin/refresh?scope=data|gold|all` สำหรับล้าง cache แบบมีคีย์ admin

---

## Gold Command Center

หน้า `/gold.html` ดึง `GET /api/gold`

- Spot จาก `XAUUSD=X` (ถ้าขาดจะ fallback เป็น `GC=F`)
- Volume ยืนยันใช้ `GC=F` เท่านั้น และเทียบแท่งที่ปิดแล้ว / ช่องชั่วโมงเดียวกัน
- Thai Fair Value = `(Spot + Model Premium) × USD/THB × (32.148 × 0.965 / 65.6)`
- Model Premium ค่าเริ่มต้น `$2.00` ใน `constants.GOLD_PREMIUM_USD` — **ไม่ใช่พรีเมียมร้านทองจริง**
- Fair Value ≠ ราคาสมาคมค้าทองคำ และไม่ใช่ราคาซื้อขาย
- คะแนนทั้งหมดเป็น heuristic ไม่ใช่ความน่าจะเป็น
- Event ทองดึงเฉพาะ FOMC / Minutes / CPI / NFP / PCE / PPI ไม่ดึง earnings ทั้งกระดาน

---

## คุณภาพข้อมูล

ระบบไม่ขึ้นว่า Yahoo OK ถ้าโหลดจักรวาลได้ไม่ครบ
ดูที่ `feed_status.yahoo_status` = `ok | partial | down` และ `data_quality`

ปฏิทินเศรษฐกิจถ้าไม่มี FRED จะติด `source_status=fallback` และ `is_fallback=true`

---

## Performance — v5.4 (แก้ปัญหา "Full 913 โหลดนานมาก")

สาเหตุหลักไม่ใช่ yfinance ช้า แต่เป็น batch size เล็กเกินไปหลังโดนลดมาหลายรอบ + market
concurrency ถูก cap ต่ำเกินไป + loop คำนวณ signal ทีละ ticker แบบ sequential:

| จุด | เดิม | แก้เป็น | ไฟล์ |
|---|---|---|---|
| `PIPELINE_BATCH_SIZE` | 10 (≈91 batches/900 ticker) | 25 | `constants.py` |
| Market concurrency | cap ที่ 5 (มี 9 ตลาดใน full mode) | เต็มจำนวนตลาดจริง | `pipeline.py` → `fetch_universe` |
| Signal computation loop | sequential ทีละ ticker | parallel ผ่าน `ThreadPoolExecutor` | `pipeline.py` → `compute_dashboard` |
| Timing visibility | ไม่มี | log `fetch_sec` / `compute_sec` แยกใน `load_market_pack` (ดูใน log หรือ key `timing` ของ pack) | `pipeline.py` |
| Cold-cache ตอน TTL หมด | user คนแรกรอเอง | pre-warm scheduler ยิงล่วงหน้าทุก 14 นาที | `backend.py` |

`CACHE_TTL_DATA` / `CACHE_TTL_CALENDAR` / `CACHE_TTL_GLOBAL` รวมมาไว้จุดเดียวใน
`constants.py` แล้ว (เดิมมี `CACHE_TTL` hardcode ซ้ำกันคนละค่าใน `economic_calendar.py`,
`etf_board.py`, `global_market.py`, `market_regime.py`, `rotation_rrg.py`)

---

## Screener Saved Presets (v5.5)

Preset ที่ผู้ใช้บันทึกเอง (ปุ่ม "💾 Save current filters" ในหน้า screener) เก็บฝั่ง
**backend + Google Drive** ผ่าน `preset_store.py` ไม่ใช่ localStorage ของ browser —
เหตุผล: ถ้าใช้หลายเครื่อง (คอมที่ทำงาน/คอมบ้าน) แม้เปิด URL เดียวกันเป๊ะ localStorage
ก็ยังผูกกับเครื่องนั้นๆ ไม่ sync ข้ามเครื่อง แต่ backend เก็บกลางที่เดียว ทุกเครื่องที่
เรียก URL/backend เดียวกันเห็นชุดเดียวกันเสมอ

**สำคัญ:** ต้อง mount Google Drive ก่อนรัน server (เหมือนที่ `cache_utils.py` ต้องการ
อยู่แล้ว) ไม่งั้น preset จะตกไปอยู่ `/tmp` ซึ่งหายเมื่อ Colab restart:
```python
from google.colab import drive
drive.mount('/content/drive')
```
เช็คว่า Drive mount อยู่จริงและ preset ถูกเก็บที่ไหน ผ่าน `/api/status` (มี key
`preset_store.drive_mounted` และ `preset_store.store_path`)

ไฟล์เก็บที่ `/content/drive/MyDrive/playground_cache/screener_presets.json`
(JSON ไฟล์เดียว ไม่มี TTL — ไม่หมดอายุเอง ต้องลบเองผ่านปุ่ม ✕ ในหน้า UI)

---

## Disclaimer

ใช้เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน
