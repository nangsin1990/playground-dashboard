# FILE: pipeline.py
from __future__ import annotations
import logging
import threading
import time
from datetime import datetime
import concurrent.futures

import numpy as np
import pandas as pd

import data_engine as eng
import data_io
from universe import FLAGS, UNIVERSE
from cache_utils import ttl_cache
from constants import (
    CORE_N, PIPELINE_BATCH_SIZE, CACHE_TTL_DATA,
    BREADTH_HISTORY_DAYS, BREADTH_BEAR_THRESHOLD, BREADTH_BEAR_FALL, BREADTH_BEAR_MIN_MKT,
    WATCHLIST_TOP_N, THEME_TOP_N, RS_MOVERS_TOP_N,
    TRADING_DAYS_MONTH, TRADING_DAYS_QUARTER, FETCH_RATE_DELAY,
)
from personal_watchlist import PERSONAL_META, PERSONAL_TICKERS

log = logging.getLogger("playground.pipeline")
SIGNAL_NAMES = ["VDU", "PPBP", "BGU", "52W", "VCP"]

FETCH_STATE = {}
_lock = threading.Lock()

def _upd(**kwargs):
    with _lock:
        FETCH_STATE.update(kwargs)

def get_fetch_state():
    with _lock:
        return FETCH_STATE.copy()

_SUFFIX_MARKET = {
    ".BK": "TH", ".HK": "HK", ".T": "JP", ".KS": "KR",
    ".SS": "CN", ".SZ": "CN", ".DE": "DE", ".PA": "FR", ".L": "GB",
}


def _market_of_ticker(ticker: str) -> str:
    t = str(ticker)
    for suf, mkt in _SUFFIX_MARKET.items():
        if t.endswith(suf):
            return mkt
    return "US"


def _with_personal(active: dict) -> dict:
    existing = set()
    for names in active.values():
        existing.update(names.keys())
    extra_by_mkt: dict[str, dict] = {}
    for t in PERSONAL_TICKERS:
        if t in existing:
            continue
        mkt = _market_of_ticker(t)
        extra_by_mkt.setdefault(mkt, {})[t] = PERSONAL_META.get(t, (t, "Unknown"))
    if not extra_by_mkt:
        return active
    out = {m: dict(v) for m, v in active.items()}
    for mkt, extra in extra_by_mkt.items():
        bucket = dict(out.get(mkt, {}))
        bucket.update(extra)
        out[mkt] = bucket
    return out


def active_universe(mode: str) -> dict:
    if mode != "full":
        base = {
            mkt: {t: UNIVERSE[mkt][t] for i, t in enumerate(UNIVERSE[mkt]) if i < CORE_N.get(mkt, 10)}
            for mkt in UNIVERSE
        }
    else:
        base = {m: dict(v) for m, v in UNIVERSE.items()}
    return _with_personal(base)


@ttl_cache(CACHE_TTL_DATA)
def load_market_pack(mode: str) -> dict:
    """ดึงราคา + คำนวณแดชบอร์ดครั้งเดียว แล้วให้หน้าอื่นใช้ก้อนเดียวกัน."""
    t0 = time.time()
    active = active_universe(mode)
    combined, ticker_meta, fetch_results = fetch_universe(active)
    t_fetch = time.time() - t0

    t1 = time.time()
    try:
        dash = compute_dashboard(combined, ticker_meta, fetch_results, active)
    except Exception as e:
        log.exception("compute_dashboard crashed")
        dash = {
            "ok": False,
            "error": str(e),
            "sync": data_io.sync_report(fetch_results, active),
            "updated": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "watchlist": [],
            "universe_loaded": len(combined),
            "universe_total": sum(len(v) for v in active.values()),
        }
    t_compute = time.time() - t1

    log.info(
        "load_market_pack(%s) TIMING: fetch=%.1fs compute=%.1fs total=%.1fs (%d tickers)",
        mode, t_fetch, t_compute, t_fetch + t_compute, len(combined),
    )
    return {
        "ok": bool(dash.get("ok")),
        "active": active,
        "combined": combined,
        "ticker_meta": ticker_meta,
        "fetch_results": fetch_results,
        "dash": dash,
        "timing": {"fetch_sec": round(t_fetch, 1), "compute_sec": round(t_compute, 1)},
    }

def _fetch_market(mkt_name: str, tickers_dict: dict) -> tuple[str, dict]:
    tickers = list(tickers_dict.keys())
    results = {}
    total_batches = (len(tickers) + PIPELINE_BATCH_SIZE - 1) // PIPELINE_BATCH_SIZE

    with _lock:
        FETCH_STATE["market"] = mkt_name
        FETCH_STATE["batch"] = 0
        FETCH_STATE["total_batches"] = total_batches
        FETCH_STATE["tickers_done_market"] = 0
        FETCH_STATE["tickers_total_market"] = len(tickers)

    for i, batch in enumerate(data_io.chunk(tickers, PIPELINE_BATCH_SIZE)):
        t0 = time.time()
        _upd(batch=i + 1)
        batch_results = data_io.fetch_batch(tuple(batch))
        results.update({t: v for t, v in batch_results.items() if v is not None})

        with _lock:
            FETCH_STATE["tickers_done_market"] += len(batch)
            FETCH_STATE["tickers_done"] = (FETCH_STATE.get("tickers_done", 0) + len(batch))

        log.info(f"batch {mkt_name} {i+1}/{total_batches} ({len(batch_results)}/{len(batch)} ok) took {time.time()-t0:.2f}s")
        if i < total_batches - 1:
            time.sleep(FETCH_RATE_DELAY)

    return mkt_name, results

def fetch_universe(active: dict) -> tuple[dict, dict, dict]:
    combined: dict[str, pd.DataFrame] = {}
    ticker_meta: dict[str, dict] = {}
    fetch_results: dict[str, dict] = {m: {} for m in active}
    markets = list(active.keys())
    total = sum(len(v) for v in active.values())
    t0 = time.time()
    _upd(stage="fetching", started=t0, tickers_total=total, markets_total=markets, markets_done=[], tickers_done=0)
    log.info("=== fetch_universe START %s (%d tickers) ===", markets, total)

    # ✨ FIX: เดิม cap ที่ 5 workers แต่ full mode มี 9 ตลาด (US TH HK JP KR CN DE FR GB)
    # ทำให้ 4 ตลาดต้องรอคิว ตอนนี้ปรับให้ workers เท่าจำนวนตลาดจริงเสมอ (รันขนานเต็มที่)
    num_workers = max(1, len(active))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=num_workers)
    futures_map = {executor.submit(_fetch_market, m, tk): m for m, tk in active.items()}

    try:
        try:
            for future in concurrent.futures.as_completed(futures_map, timeout=300):
                mkt = futures_map[future]
                try:
                    mkt_name, mkt_results = future.result(timeout=120)
                    fetch_results[mkt_name] = mkt_results
                    tk_dict = active[mkt_name]
                    for t, df in mkt_results.items():
                        name, theme = tk_dict.get(t, (t, "Unknown"))
                        try:
                            combined[t] = eng.add_technical_indicators(df.copy())
                            ticker_meta[t] = {"market": mkt_name, "name": name, "theme": theme}
                        except Exception:
                            log.exception("indicators failed %s", t)
                    with _lock:
                        FETCH_STATE["markets_done"].append(mkt_name)
                    log.info("market %s DONE — %d tickers", mkt_name, len(mkt_results))
                except concurrent.futures.TimeoutError:
                    log.error("market %s TIMEOUT — skip", mkt)
                    _upd(last_error=f"{mkt} timeout")
                except Exception:
                    log.exception("market %s ERROR — skip", mkt)
                    _upd(last_error=f"{mkt} error")
        except concurrent.futures.TimeoutError:
            pending = [m for f, m in futures_map.items() if not f.done()]
            log.error("fetch_universe overall TIMEOUT — pending %s", pending)
            _upd(last_error="universe timeout 300s")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    log.info("=== fetch_universe END %d tickers %.1fs ===", len(combined), time.time() - t0)
    _upd(stage="computing")
    return combined, ticker_meta, fetch_results

def _safe_series(s: pd.Series) -> pd.Series:
    return s.replace([np.inf, -np.inf], np.nan).fillna(0)

def compute_dashboard(combined, ticker_meta, fetch_results, active) -> dict:
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    sync = data_io.sync_report(fetch_results, active)
    if not combined:
        _upd(stage="error", last_error="no data")
        return {"ok": False, "error": "ดึงข้อมูลจาก Yahoo Finance ไม่สำเร็จ", "sync": sync, "updated": now_str}

    log.info("compute_dashboard %d tickers", len(combined))

    close_map = {}
    for ticker, data in combined.items():
        if data is None or getattr(data, "empty", True) or "Close" not in data.columns:
            continue
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        if not isinstance(close, pd.Series):
            continue
        close_map[ticker] = close
    close_df = pd.DataFrame(close_map) if close_map else pd.DataFrame()

    try:
        breadth_rows, breadth_history_all, bear_override = _compute_breadth(combined, ticker_meta)
    except Exception:
        log.exception("breadth failed")
        breadth_rows, breadth_history_all, bear_override = [], {}, False

    # ✨ FIX: เดิม loop คำนวณ signal ทีละ ticker แบบ sync ล้วน — กับ full universe
    # (~900 ticker) ตรงนี้เป็นคอขวดที่ 2 รองจาก fetch เพราะรัน rolling indicator
    # ซ้ำ ๆ ทีละตัว ตอนนี้ parallelize ด้วย thread pool (คำนวณเป็น pandas/numpy
    # ซึ่ง release GIL ระหว่าง numeric ops ได้บางส่วน — ช่วยได้แม้ไม่ใช่ true parallel)
    signal_count_5d = {k: 0 for k in SIGNAL_NAMES}
    ticker_signal = {}

    def _compute_signal_for(item):
        t, d = item
        try:
            sig = eng.run_scanners(d)
            rolled, conf, count = eng.confluence_flags(sig)
            count_val = float(count.iloc[-1]) if count is not None and not count.empty else 0
            conf_val = bool(conf.iloc[-1]) if conf is not None and not conf.empty else False
            last_rolled = {k: bool(v.iloc[-1]) for k, v in rolled.items() if v is not None and not v.empty}
            vcp = eng.vcp_metrics(d)
            if vcp.get("is_vcp"):
                last_rolled["VCP"] = True
            last5 = {}
            for k, series in (sig or {}).items():
                if k in SIGNAL_NAMES and series is not None and getattr(series, "tail", None):
                    last5[k] = bool(series.tail(5).fillna(False).any())
            if last_rolled.get("VCP"):
                last5["VCP"] = True
            return t, {"rolled": last_rolled, "count": int(count_val), "confluence": conf_val, "vcp": vcp, "last5": last5}, None
        except Exception as e:
            return t, {"rolled": {}, "count": 0, "confluence": False, "last5": {}}, e

    sig_workers = min(16, max(4, len(combined) // 20 or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=sig_workers) as sig_ex:
        for t, result, err in sig_ex.map(_compute_signal_for, combined.items()):
            if err is not None:
                log.exception("scanner failed: %s", t, exc_info=err)
            ticker_signal[t] = result
            flags = result.get("last5") or result.get("rolled") or {}
            for k, v in flags.items():
                if v and k in signal_count_5d:
                    signal_count_5d[k] += 1

    # ✨ FIXED: Call real RS rating engines
    try:
        rs_now = eng.rs_rating_per_market(combined, ticker_meta)
    except Exception:
        log.exception("rs_now failed")
        rs_now = pd.Series(dtype=float)
    try:
        rs_7 = eng.rs_rating_table(close_df, 7) if not close_df.empty else pd.Series(dtype=float)
    except Exception:
        log.exception("rs_7 failed")
        rs_7 = pd.Series(dtype=float)

    theme_map = {}
    for t, m in (ticker_meta or {}).items():
        if not isinstance(m, dict):
            continue
        theme_map.setdefault(m.get("theme") or "Unknown", []).append(t)

    theme_rows = []
    try:
        themes_df = eng.theme_returns(close_df, theme_map, ticker_meta, rs_now)
        if themes_df is not None and not getattr(themes_df, "empty", True):
            for col in ("r1d", "r1m", "r3m", "avg_rs"):
                if col in themes_df.columns:
                    themes_df[col] = pd.to_numeric(themes_df[col], errors="coerce")
            sort_cols = [c for c in ("r1m", "r3m") if c in themes_df.columns]
            themes = themes_df.sort_values(by=sort_cols, ascending=False, na_position="last") if sort_cols else themes_df
            theme_rows = themes.head(max(THEME_TOP_N, 10)).to_dict("records")
    except Exception:
        log.exception("theme_returns failed")

    try:
        rs_delta = rs_now.sub(rs_7.reindex(rs_now.index), fill_value=np.nan)
    except Exception:
        rs_delta = pd.Series(dtype=float)
    rs_movers = []
    top_gainers = rs_delta.nlargest(max(RS_MOVERS_TOP_N, 10)).index
    for t in top_gainers:
        last_px = None
        if t in close_df:
            series = close_df[t].dropna()
            if len(series):
                px = float(series.iloc[-1])
                last_px = round(px, 4 if px < 1 else 2)
        meta = ticker_meta.get(t) or {}
        rs_movers.append({
            "ticker": t.split(".")[0], "full_ticker": t,
            "name": meta.get("name", ""),
            "theme": meta.get("theme", ""),
            "market": meta.get("market", ""),
            "rs": (int(rs_now.get(t)) if hasattr(rs_now,"get") and rs_now.get(t)==rs_now.get(t) and rs_now.get(t) is not None else None),
            "drs7": (int(rs_delta.get(t)) if hasattr(rs_delta,"get") and rs_delta.get(t)==rs_delta.get(t) and rs_delta.get(t) is not None else None),
            "price": last_px,
            "spark": _safe_series(close_df[t].tail(7)).tolist() if t in close_df else []
        })

    def _watch_key(item):
        t, s = item
        rs_val = 0
        raw = rs_now.get(t) if hasattr(rs_now, "get") else None
        try:
            rs_val = int(raw) if raw is not None and raw == raw else -1
        except Exception:
            rs_val = -1
        return (1 if s.get("confluence") else 0, int(s.get("count") or 0), rs_val)

    watch = [t for t, s in sorted(ticker_signal.items(), key=_watch_key, reverse=True) if s.get("confluence")][:WATCHLIST_TOP_N]
    watchlist = []
    for t in watch:
        meta = ticker_meta.get(t, {})
        d = combined.get(t)
        if d is None: continue
        watchlist.append({
            "ticker": t.split(".")[0], "full_ticker": t,
            "name": meta.get("name", ""), "theme": meta.get("theme", ""),
            "patterns": [k for k, v in ticker_signal[t]["rolled"].items() if v],
            "price": (lambda px: None if px is None else round(px, 4 if px < 1 else 2))(
                float(eng.as_close(d["Close"]).dropna().iloc[-1]) if eng.as_close(d["Close"]) is not None and len(eng.as_close(d["Close"]).dropna()) else None
            ),
            "pct1d": eng.pct_change(d["Close"], 1),
            "rs": (int(rs_now.get(t)) if hasattr(rs_now, "get") and rs_now.get(t) is not None and rs_now.get(t) == rs_now.get(t) else None),
            "drs7": (int(rs_delta.get(t)) if hasattr(rs_delta, "get") and rs_delta.get(t) is not None and rs_delta.get(t) == rs_delta.get(t) else None),
            "market": meta.get("market", ""),
            "drawdown_pct": eng.current_drawdown_from_peak(d["Close"]),
            "max_dd_pct": eng.max_drawdown(d["Close"]),
        })

    dip_watch = _dip_candidates(combined, ticker_meta, rs_now, rs_7, ticker_signal)

    requested = sum(len(v) for v in active.values()) if active else len(ticker_meta)
    _upd(stage="done")
    return {
        "ok": True, "updated": now_str, "universe_total": requested, "universe_loaded": len(combined),
        "sync": sync, "breadth": breadth_rows, "bear_override": bear_override,
        "stat_cards": {"VDU": signal_count_5d["VDU"], "PPBP": signal_count_5d["PPBP"], "BGU": signal_count_5d["BGU"],
                       "52W": signal_count_5d["52W"], "VCP": signal_count_5d.get("VCP", 0),
                       "total": len(combined),
                       "flagged": sum(signal_count_5d.values())},
        "feed_status": {
            "yahoo": True,
            "yahoo_status": "ok" if len(combined) >= max(1, int(requested * 0.85)) else ("partial" if combined else "down"),
            "updated": now_str, "loaded": len(combined), "total": requested,
        },
        "theme_movers": theme_rows, "rs_movers": rs_movers,
        "watchlist": sorted(watchlist, key=lambda x: (-1 if x.get("rs") is None else -int(x.get("rs") or 0))),
        "dip_watch": dip_watch,
        "breadth_history_all": breadth_history_all,
        "rs_now": rs_now, "rs_7": rs_7, "ticker_signal": ticker_signal
    }

def _dip_candidates(combined, ticker_meta, rs_now, rs_7, ticker_signal, limit: int = 18) -> list:
    """หุ้นที่เคยแข็งแล้วเพิ่งอ่อน แต่โครงยังไม่พังทั้งดอก — เตรียมช้อน ไม่ใช่ของถูกเพราะถูกเททิ้ง."""
    rows = []
    if rs_now is None:
        return rows
    for t, df in combined.items():
        if df is None or len(df) < 60:
            continue
        last = df.iloc[-1]
        rs = int(rs_now.get(t, 0) or 0)
        prev = int(rs_7.get(t, rs) or rs) if rs_7 is not None else rs
        drs = rs - prev
        if rs < 40 or rs > 82 or drs >= 0:
            continue
        high = last.get("HIGH_52W")
        if high is None or pd.isna(high):
            high = float(df["High"].tail(min(252, len(df))).max())
        px = float(last["Close"])
        if not high:
            continue
        off = (px / float(high) - 1) * 100
        if off > -8 or off < -28:
            continue
        sma50 = last.get("SMA50")
        sma200 = last.get("SMA200")
        above50 = pd.notna(sma50) and px > float(sma50)
        above200 = pd.notna(sma200) and px > float(sma200)
        if not above50 and not above200:
            continue
        rolled = (ticker_signal.get(t) or {}).get("rolled") or {}
        drying = bool(rolled.get("VDU") or rolled.get("VCP"))
        score = int(min(99, (82 - rs) + min(25, abs(drs) * 2.5) + min(20, abs(off)) + (10 if drying else 0) + (6 if above200 else 0)))
        meta = ticker_meta.get(t) or {}
        rows.append({
            "ticker": str(t).split(".")[0],
            "full_ticker": t,
            "name": meta.get("name", ""),
            "theme": meta.get("theme", ""),
            "market": meta.get("market", ""),
            "price": round(px, 4 if px < 1 else 2),
            "pct1d": eng.pct_change(df["Close"], 1),
            "rs": rs,
            "drs7": drs,
            "off_52w": round(off, 1),
            "above50": above50,
            "above200": above200,
            "drying": drying,
            "score": score,
            "why": " · ".join(filter(None, [
                f"RS {rs} ทรุด {drs}",
                f"ห่างยอด {off:.0f}%",
                "เหนือ SMA200" if above200 else ("เหนือ SMA50" if above50 else ""),
                "วอลุ่มแห้ง" if drying else "",
            ])),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:limit]


def _as_naive_dates(idx) -> pd.DatetimeIndex:
    parsed = pd.to_datetime(idx, utc=True, errors="coerce")
    if getattr(parsed, "tz", None) is not None:
        parsed = parsed.tz_convert("UTC").tz_localize(None)
    return pd.DatetimeIndex(parsed).normalize()


def _compute_breadth(combined, ticker_meta):
    """% above MA50/MA200 on a shared calendar. Missing that day counts as not-above, divisor stays universe_used."""
    breadth_rows = []
    breadth_history_all = {}
    bear_markets = 0
    mkt_groups = {m: [] for m in FLAGS}
    for t, meta in ticker_meta.items():
        if meta["market"] in mkt_groups:
            mkt_groups[meta["market"]].append(t)

    for mkt, tickers in mkt_groups.items():
        if not tickers:
            continue

        usable = []
        for t in tickers:
            if t not in combined:
                continue
            df = combined[t]
            if df is None or len(df) < 200 or "SMA50" not in df.columns or "SMA200" not in df.columns:
                continue
            usable.append(df)
        if not usable:
            continue

        n_used = len(usable)
        panel50, panel200 = [], []
        for df in usable:
            close, s50, s200 = df["Close"], df["SMA50"], df["SMA200"]
            idx = _as_naive_dates(df.index)
            a50 = (close > s50).astype("float")
            a200 = (close > s200).astype("float")
            a50.index = idx
            a200.index = idx
            a50 = a50[~a50.index.duplicated(keep="last")]
            a200 = a200[~a200.index.duplicated(keep="last")]
            panel50.append(a50)
            panel200.append(a200)

        wide50 = pd.concat(panel50, axis=1).sort_index()
        wide200 = pd.concat(panel200, axis=1).sort_index()
        coverage = wide50.notna().sum(axis=1)
        min_cov = max(1, int(n_used * 0.5))
        eligible = coverage[coverage >= min_cov].index[-BREADTH_HISTORY_DAYS:]
        hist50 = (wide50.reindex(eligible).fillna(0.0).sum(axis=1) / n_used) * 100
        hist200 = (wide200.reindex(eligible).fillna(0.0).sum(axis=1) / n_used) * 100
        if hist50.empty:
            continue
        pct50 = float(hist50.iloc[-1])
        pct200 = float(hist200.iloc[-1]) if len(hist200) else 0.0
        chg = float(hist50.iloc[-1] - hist50.iloc[-2]) if len(hist50) > 1 else 0.0
        if pct50 < BREADTH_BEAR_THRESHOLD and chg < BREADTH_BEAR_FALL:
            bear_markets += 1

        breadth_rows.append({
            "code": mkt, "flag": FLAGS[mkt],
            "ma50": round(float(pct50), 2), "ma200": round(float(pct200), 2),
            "chg": round(float(chg), 2),
            "universe": len(tickers), "universe_used": n_used,
        })
        breadth_history_all[mkt] = {
            "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in hist50.index],
            "ma50": [round(float(v), 2) for v in hist50.tolist()],
            "ma200": [round(float(v), 2) for v in hist200.tolist()],
            "universe": len(tickers),
            "universe_used": n_used,
        }

    bear_override = bear_markets >= BREADTH_BEAR_MIN_MKT
    breadth_rows.sort(key=lambda x: x["ma50"], reverse=True)
    return breadth_rows, breadth_history_all, bear_override
