# Audit fixes — 2026-09-05

แก้ตามรายงานตรวจสอบ + ข้อสังเกตเพิ่ม 2 ข้อ

## P0
- BUG-01 VCP `_band` ใช้ `np.select` เลือกแบนด์ที่แข็งที่สุด ไม่เขียนทับลงแบนด์อ่อน
- BUG-02 DQ gate ใน `evaluate_decision` บังคับ `stance=INSUFFICIENT` เมื่อ `dq < DQ_GATE`
- BUG-03 scanner เรียก `decide()` ที่ใช้ engine เดียวกับ payload
- BUG-04 breadth ไม่นับวันขาดข้อมูลเป็นใต้ MA และส่ง `coverage_pct`
- BUG-05 mws ใช้ relative import + fallback
- SEC-01/02 admin refresh และ `?refresh=1` ต้องมี `DASHBOARD_ADMIN_KEY` + rate limit

## P1
- DATA-01 FCF/OCF ได้คะแนน conversion เฉพาะเมื่อ `ocf > 0` และ `fcf >= 0`
- DEP-01 ใส่ `fredapi`, `finnhub-python`, `ta`, `requests` ใน requirements.txt
- PERF-01 cache + data_io มี single-flight กัน stampede
- PERF-02 `DOWNLOAD_MAX_WORKERS = 8`
- MODEL-01 RS ใช้ market-local rank ทุกตลาด
- ข้อสังเกต 1: payload เรียก `market_regime()` และ warning ถ้า `_regime` ยังไม่ถูก set
- ข้อสังเกต 2: grep `_band`/`out.where(~cond` — ไม่มี instance อื่นนอก VCP ที่แก้แล้ว

## P2
- CONFIG-01 `PIPELINE_BATCH_SIZE = FETCH_CHUNK_SIZE` (25)
- MODEL-02 `rs_rating_asof(..., lag_bars=)` — `lag_days` ยังใช้ได้
- DOC-01 README ตรงกับ auth จริง
- Calendar ส่ง `estimated`/`status` และโชว์ badge ใน UI
- colab_start ใช้โฟลเดอร์ local ที่มี `backend.py` ก่อน clone GitHub
- vcp_metrics ค่าว่างใช้ `None` เมื่อคำนวณไม่ได้

## ทดสอบ
`pytest -q` จาก root ต้องผ่าน (รวม test_audit_fixes)

## Admin refresh
```
export DASHBOARD_ADMIN_KEY=...
curl -X POST "$HOST/api/admin/refresh?scope=all" -H "X-Admin-Key: $DASHBOARD_ADMIN_KEY"
```
`?refresh=1` โดยไม่มีคีย์จะถูกเมิน ไม่ล้าง cache
