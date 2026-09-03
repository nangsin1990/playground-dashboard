"""Personal watchlist + in-dashboard alerts."""
from __future__ import annotations

from datetime import datetime

from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA

INVALID_YAHOO = {"DRAM", "FOTO", "QNT"}

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


def _days(v, default=99) -> int:
    try:
        if v is None or v == "":
            return default
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _close_series(df):
    if df is None:
        return None
    cols = list(getattr(df, "columns", []))
    if "Close" in cols:
        close = df["Close"]
    else:
        close = df
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    try:
        close = close.dropna()
    except Exception:
        pass
    return close


def _px_pct(close):
    if close is None:
        return None, None
    try:
        if len(close) < 1:
            return None, None
        price = float(close.iloc[-1])
        if price != price:
            return None, None
        pct = None
        if len(close) >= 2 and float(close.iloc[-2]) != 0:
            pct = round((price / float(close.iloc[-2]) - 1) * 100, 2)
        return round(price, 4 if price < 1 else 2), pct
    except Exception:
        return None, None


@ttl_cache(CACHE_TTL_DATA)
def _batch_quotes_cached(tickers: tuple) -> dict:
    return _batch_quotes_raw(list(tickers))


def _batch_quotes(tickers: list[str]) -> dict:
    clean = tuple(sorted({t for t in tickers if t and t not in INVALID_YAHOO}))
    if not clean:
        return {}
    return _batch_quotes_cached(clean)


def _batch_quotes_raw(tickers: list[str]) -> dict:
    out = {}
    tickers = [t for t in tickers if t]
    if not tickers:
        return out
    try:
        import yfinance as yf
        raw = yf.download(
            tickers if len(tickers) > 1 else tickers[0],
            period="1mo",
            auto_adjust=True,
            progress=False,
            threads=False,
            group_by="ticker",
        )
    except Exception:
        return out
    if raw is None or getattr(raw, "empty", True):
        return out

    def take_close(frame, name):
        try:
            if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
                lvl0 = set(map(str, frame.columns.get_level_values(0)))
                if name in lvl0:
                    sub = frame[name]
                    return sub["Close"] if "Close" in sub.columns else sub.iloc[:, 0]
                if "Close" in lvl0:
                    return frame["Close"][name] if name in frame["Close"].columns else frame["Close"]
            if "Close" in frame.columns:
                return frame["Close"]
        except Exception:
            return None
        return None

    if len(tickers) == 1:
        close = take_close(raw, tickers[0])
        px, pct = _px_pct(close.dropna() if close is not None else None)
        if px is not None:
            out[tickers[0]] = (px, pct)
        return out

    for t in tickers:
        close = take_close(raw, t)
        if close is None:
            continue
        try:
            close = close.dropna()
        except Exception:
            continue
        px, pct = _px_pct(close)
        if px is not None:
            out[t] = (px, pct)
    return out


def resolve_key(ticker: str, combined: dict) -> str | None:
    if ticker in combined:
        return ticker
    up = ticker.upper()
    for k in combined:
        if str(k).split(".")[0].upper() == up:
            return k
    return None


def build_my_watchlist(combined: dict, ticker_meta: dict, ticker_signal: dict,
                       rs_now, calendar_events: list | None = None) -> dict:
    earn_map: dict[str, dict] = {}
    next_macros: list[dict] = []
    for ev in calendar_events or []:
        try:
            if ev.get("category") == "EARNINGS":
                for t in ev.get("tickers") or []:
                    earn_map.setdefault(str(t).upper(), ev)
            elif ev.get("importance") == "HIGH" and 0 <= _days(ev.get("days_away")) <= 7:
                next_macros.append(ev)
        except Exception:
            continue

    pack_px = {}
    missing = []
    for t in PERSONAL_TICKERS:
        key = resolve_key(t, combined or {})
        px = pct = None
        if key:
            px, pct = _px_pct(_close_series((combined or {}).get(key)))
        if px is None:
            missing.append(t)
        else:
            pack_px[t] = (px, pct)

    if missing:
        pack_px.update(_batch_quotes(missing))

    rows, alerts = [], []
    for t in PERSONAL_TICKERS:
        name, theme = PERSONAL_META.get(t, (t, "Unknown"))
        key = resolve_key(t, combined or {})
        if key and ticker_meta and key in ticker_meta:
            name = ticker_meta[key].get("name") or name
            theme = ticker_meta[key].get("theme") or theme
        sig = (ticker_signal or {}).get(key or "", {}) if key else {}
        rolled = sig.get("rolled") or {}
        patterns = [k for k, v in rolled.items() if v]
        rs = None
        if key is not None and rs_now is not None:
            try:
                raw_rs = rs_now.get(key) if hasattr(rs_now, "get") else None
                if raw_rs is not None and raw_rs == raw_rs:
                    rs = int(raw_rs)
            except Exception:
                rs = None

        price, pct1d = pack_px.get(t, (None, None))
        loaded = price is not None
        drawdown = None
        if key and key in (combined or {}):
            try:
                import data_engine as eng
                close = _close_series(combined.get(key))
                if close is not None and len(close):
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
            "price": price,
            "pct1d": pct1d,
            "drawdown_pct": drawdown,
            "patterns": patterns,
            "confluence": bool(sig.get("confluence")),
            "next_earnings": earn.get("date") if earn else None,
            "next_earnings_in": earn.get("days_away") if earn else None,
        }
        rows.append(row)

        if not loaded:
            if t in INVALID_YAHOO:
                alerts.append({"level": "warn", "ticker": t, "text": f"{t} ไม่มีบน Yahoo — ตัดออกจากรายการได้"})
            continue
        if row["confluence"]:
            alerts.append({"level": "buy", "ticker": t, "text": f"{t} สัญญาณซ้อน {', '.join(patterns) or 'confluence'}"})
        if rs is not None and rs >= 80 and "PPBP" in patterns:
            alerts.append({"level": "buy", "ticker": t, "text": f"{t} RS {rs} + Pocket Pivot"})
        if "52W" in patterns:
            alerts.append({"level": "info", "ticker": t, "text": f"{t} ใกล้สูงสุด 52 สัปดาห์"})
        if drawdown is not None and drawdown >= 15:
            alerts.append({"level": "risk", "ticker": t, "text": f"{t} ย่อจากยอด {drawdown:.1f}%"})
        if earn and t not in ETF_SKIP_EARNINGS and _days(earn.get("days_away")) <= 7:
            alerts.append({"level": "event", "ticker": t, "text": f"{t} ประกาศงบใน {earn.get('days_away')} วัน ({earn.get('date')})"})

    for ev in next_macros[:3]:
        alerts.append({
            "level": "event",
            "ticker": "MACRO",
            "text": f"{ev.get('date')} {ev.get('title') or ev.get('category')} — ข่าวใหญ่ใกล้เข้า",
        })

    failed_valid = [r["ticker"] for r in rows if not r["loaded"] and r["ticker"] not in INVALID_YAHOO]
    if len(failed_valid) >= 5:
        alerts.insert(0, {
            "level": "warn",
            "ticker": "FEED",
            "text": f"Yahoo ช้าหรือถูกจำกัดครั้งนี้ {len(failed_valid)} ตัว — อย่ารีเฟรชถี่ รอ 2-3 นาที",
        })
    elif failed_valid:
        alerts.insert(0, {
            "level": "warn",
            "ticker": "FEED",
            "text": "ยังไม่มีราคา: " + ", ".join(failed_valid[:8]),
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
