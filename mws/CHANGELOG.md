# Revision log

## v3.8.0  /  20260904-r7

- ราคา ค่าเฉลี่ย RSI ใช้แท่งสมบูรณ์วันเดียวกัน มี as_of และ live_quote แยก
- ตัดแท่ง Close ว่างจาก Yahoo
- RSI 72 ขึ้นไปถือว่ายืด (สอดคล้องเกณฑ์เข้าของแบ็กเทส)
- มูลค่า Extreme ปิดสถานะ CANDIDATE เหลือ WATCH_EXPENSIVE
- ROIC proxy ไม่ใช้ ROE ที่พองจากซื้อหุ้นคืน
- แยก trade_stop กับ thesis_invalidation
- MACD สำรองเมื่อไม่มีไลบรารี ta

# MWS revision log

## v3.3.0  /  20260904-r1

- Split monolith into runnable modules
- Timezone-safe Yahoo index (`strip_tz` / `naive_frame`)
- Debt/Equity unit lock (`normalize_debt_to_equity`)
- Catalyst scores 0 when no news
- Research composite Q35 / M25 / V20 / inverted-R20
- Winner classification uses composite + quality floor
- Separate BUSINESS vs PRICE labels
- DQ technical credit needs ATR/MACD, not RSI alone
- ROIC labeled as proxy only
- Backtest: no same-bar re-entry after exit
- `--annual-slice` replaces misleading walk-forward name
- AMZN peer map no longer retail-only
- Fallback technicals now also compute ATR

## v3.2

- Scorecards, env-only keys, entry zone from setup, swing excludes current bar
