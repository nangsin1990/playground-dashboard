"""Personal watchlist + in-dashboard alerts."""
from __future__ import annotations

from datetime import datetime

PERSONAL_TICKERS = [
    "AAPL", "AMAT", "AMZN", "ARM", "AVGO", "COHR", "CRWD", "CSCO",
    "DBJP", "DRAM", "FOTO", "GOOGL", "JEPQ", "KLAC", "LRCX", "NBIS",
    "NVDA", "NVO", "OKTA", "QNT", "QQQM", "SMH", "TSLA", "TSM", "VRT",
]

PERSONAL_META = {
    "AAPL":  ("Apple", "Information Technology"),
    "AMAT":  ("Applied Materials", "Semiconductors"),
    "AMZN":  ("Amazon", "Consumer Discretionary"),
    "ARM":   ("Arm Holdings", "Semiconductors"),
    "AVGO":  ("Broadcom", "Semiconductors"),
    "COHR":  ("Coherent", "Information Technology"),
    "CRWD":  ("CrowdStrike", "Information Technology"),
    "CSCO":  ("Cisco", "Information Technology"),
    "DBJP":  ("Xtrackers MSCI Japan Hedged", "ETF - International/EM"),
    "DRAM":  ("DRAM", "Semiconductors"),
    "FOTO":  ("FOTO", "Unknown"),
    "GOOGL": ("Alphabet", "Communication Services"),
    "JEPQ":  ("JPMorgan Nasdaq Premium Income", "ETF - Broad Market"),
    "KLAC":  ("KLA", "Semiconductors"),
    "LRCX":  ("Lam Research", "Semiconductors"),
    "NBIS":  ("Nebius Group", "Information Technology"),
    "NVDA":  ("NVIDIA", "Semiconductors"),
    "NVO":   ("Novo Nordisk", "Health Care"),
    "OKTA":  ("Okta", "Information Technology"),
    "QNT":   ("QNT", "Unknown"),
    "QQQM":  ("Invesco Nasdaq 100", "ETF - Broad Market"),
    "SMH":   ("VanEck Semiconductor", "ETF - Sector Equity"),
    "TSLA":  ("Tesla", "Consumer Discretionary"),
    "TSM":   ("TSMC", "Semiconductors"),
    "VRT":   ("Vertiv", "Industrials"),
}

ETF_SKIP_EARNINGS = {"DBJP", "JEPQ", "QQQM", "SMH"}


def resolve_key(ticker: str, combined: dict) -> str | None:
    if ticker in combined:
        return ticker
    for k in combined:
        if k.split(".")[0].upper() == ticker.upper():
            return k
    return None


def build_my_watchlist(combined: dict, ticker_meta: dict, ticker_signal: dict,
                       rs_now, calendar_events: list | None = None) -> dict:
    earn_map: dict[str, dict] = {}
    next_macros: list[dict] = []
    for ev in calendar_events or []:
        if ev.get("category") == "EARNINGS":
            for t in ev.get("tickers") or []:
                earn_map.setdefault(str(t).upper(), ev)
        elif ev.get("importance") == "HIGH" and 0 <= int(ev.get("days_away") or 99) <= 7:
            next_macros.append(ev)

    rows, alerts = [], []
    for t in PERSONAL_TICKERS:
        name, theme = PERSONAL_META.get(t, (t, "Unknown"))
        key = resolve_key(t, combined or {})
        sig = (ticker_signal or {}).get(key or "", {}) if key else {}
        rolled = sig.get("rolled") or {}
        patterns = [k for k, v in rolled.items() if v]
        rs = int(rs_now.get(key, 0)) if key is not None and rs_now is not None else 0
        loaded = key is not None

        pct1d = None
        drawdown = None
        if loaded:
            try:
                import data_engine as eng
                close = combined[key]["Close"]
                pct1d = eng.pct_change(close, 1)
                drawdown = eng.current_drawdown_from_peak(close)
            except Exception:
                pass

        earn = earn_map.get(t.upper())
        row = {
            "ticker": t,
            "full_ticker": key or t,
            "name": name,
            "theme": theme,
            "loaded": loaded,
            "rs": rs,
            "pct1d": pct1d,
            "drawdown_pct": drawdown,
            "patterns": patterns,
            "confluence": bool(sig.get("confluence")),
            "next_earnings": earn.get("date") if earn else None,
            "next_earnings_in": earn.get("days_away") if earn else None,
        }
        rows.append(row)

        if not loaded:
            alerts.append({"level": "warn", "ticker": t, "text": f"{t} ดึงราคาไม่ได้ — ตรวจทิกเกอร์บน Yahoo"})
            continue
        if row["confluence"]:
            alerts.append({"level": "buy", "ticker": t, "text": f"{t} สัญญาณซ้อน {', '.join(patterns) or 'confluence'}"})
        if rs >= 80 and "PPBP" in patterns:
            alerts.append({"level": "buy", "ticker": t, "text": f"{t} RS {rs} + Pocket Pivot"})
        if "52W" in patterns:
            alerts.append({"level": "info", "ticker": t, "text": f"{t} ใกล้สูงสุด 52 สัปดาห์"})
        if drawdown is not None and drawdown >= 15:
            alerts.append({"level": "risk", "ticker": t, "text": f"{t} ย่อจากยอด {drawdown:.1f}%"})
        if earn and int(earn.get("days_away") or 99) <= 7:
            alerts.append({"level": "event", "ticker": t, "text": f"{t} ประกาศงบใน {earn.get('days_away')} วัน ({earn.get('date')})"})

    for ev in next_macros[:3]:
        alerts.append({
            "level": "event",
            "ticker": "MACRO",
            "text": f"{ev.get('date')} {ev.get('title') or ev.get('category')} — ข่าวใหญ่ใกล้เข้า",
        })

    return {
        "ok": True,
        "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "tickers": PERSONAL_TICKERS,
        "rows": rows,
        "alerts": alerts,
        "loaded": sum(1 for r in rows if r["loaded"]),
        "total": len(rows),
    }
