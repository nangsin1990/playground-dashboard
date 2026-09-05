"""
thematic_matrix.py — Thematic/Sector heatmap engine
v3: per-market RS, wrapped try/except, US-only themes
"""
from __future__ import annotations
from datetime import datetime

import numpy as np
import pandas as pd

import data_engine as eng
import pipeline
from cache_utils import ttl_cache
from constants import (
    CACHE_TTL_DATA, THEMATIC_TOP_TICKERS, THEMATIC_MAX_MEMBERS,
    TRADING_DAYS_MONTH, TRADING_DAYS_QUARTER,
)


def _safe_pct(close: pd.Series, n: int) -> float | None:
    try:
        if len(close) <= n or close.iloc[-1 - n] == 0:
            return None
        v = float(close.iloc[-1] / close.iloc[-1 - n] - 1) * 100
        return None if (np.isnan(v) or np.isinf(v)) else round(v, 2)
    except Exception:
        return None


@ttl_cache(CACHE_TTL_DATA)
def fetch_thematic(mode: str = "core") -> dict:
    try:
        pack = pipeline.load_market_pack(mode)
        combined = pack.get("combined") or {}
        ticker_meta = pack.get("ticker_meta") or {}

        if not combined:
            return {"ok": False, "error": "No data from yfinance", "themes": []}

        # Per-market RS (no cross-market mixing)
        rs_now = eng.rs_rating_per_market(combined, ticker_meta)

        # Build per-ticker rows
        ticker_rows: dict[str, dict] = {}
        for t, d in combined.items():
            if d is None or getattr(d, "empty", True) or "Close" not in d.columns:
                continue
            close = d["Close"].dropna()
            if len(close) < 2:
                continue
            meta  = ticker_meta.get(t, {})
            above_sma50 = None
            if "SMA50" in d.columns and pd.notna(d["SMA50"].iloc[-1]) and d["SMA50"].iloc[-1]:
                above_sma50 = bool(float(close.iloc[-1]) > float(d["SMA50"].iloc[-1]))
            raw_rs = rs_now.get(t) if hasattr(rs_now, "get") else None
            try:
                rs_val = int(raw_rs) if raw_rs is not None and pd.notna(raw_rs) else None
            except (TypeError, ValueError):
                rs_val = None
            last_px = float(close.iloc[-1])
            ticker_rows[t] = {
                "ticker":  t,
                "symbol":  t.split(".")[0],
                "name":    meta.get("name", t),
                "theme":   meta.get("theme") or "Unknown",
                "market":  meta.get("market", ""),
                "r1d":     _safe_pct(close, 1),
                "r1m":     _safe_pct(close, TRADING_DAYS_MONTH),
                "r3m":     _safe_pct(close, TRADING_DAYS_QUARTER),
                "rs":      rs_val,
                "close":   round(last_px, 4 if last_px < 1 else 2),
                "above_ema50": above_sma50,
            }

        # Group by theme
        theme_map: dict[str, list[str]] = {}
        for t, row in ticker_rows.items():
            theme_map.setdefault(row["theme"], []).append(t)

        themes = []
        for theme, members in theme_map.items():
            if not members:
                continue

            r1d_vals = [ticker_rows[t]["r1d"] for t in members if ticker_rows[t]["r1d"] is not None]
            r1m_vals = [ticker_rows[t]["r1m"] for t in members if ticker_rows[t]["r1m"] is not None]
            r3m_vals = [ticker_rows[t]["r3m"] for t in members if ticker_rows[t]["r3m"] is not None]
            rs_vals  = [ticker_rows[t]["rs"]  for t in members if ticker_rows[t]["rs"] is not None]

            avg_r1d = round(float(np.mean(r1d_vals)), 2) if r1d_vals else None
            avg_r1m = round(float(np.mean(r1m_vals)), 2) if r1m_vals else None
            avg_r3m = round(float(np.mean(r3m_vals)), 2) if r3m_vals else None
            avg_rs  = int(round(float(np.mean(rs_vals)))) if rs_vals else None
            if avg_r1m is None and avg_r3m is None:
                score = None
            else:
                score = round((avg_r1m or 0) + (avg_r3m or 0), 2)
            ema_flags = [ticker_rows[t]["above_ema50"] for t in members if ticker_rows[t].get("above_ema50") is not None]
            pct_ema50 = round(100.0 * sum(1 for x in ema_flags if x) / len(ema_flags), 1) if ema_flags else None

            # Markets in this theme
            markets = list(set(ticker_rows[t]["market"] for t in members))

            # Top tickers by RS
            top_tickers = sorted(
                members,
                key=lambda t: ticker_rows[t]["rs"] if ticker_rows[t]["rs"] is not None else -1,
                reverse=True,
            )[:THEMATIC_TOP_TICKERS]

            member_list = sorted(
                [ticker_rows[t] for t in members],
                key=lambda r: r["rs"] if r["rs"] is not None else -1,
                reverse=True,
            )[:THEMATIC_MAX_MEMBERS]

            themes.append({
                "theme":       theme,
                "markets":     markets,
                "count":       len(members),
                "r1d":         avg_r1d,
                "r1m":         avg_r1m,
                "r3m":         avg_r3m,
                "score":       score,
                "avg_rs":      avg_rs,
                "pct_ema50":   pct_ema50,
                "top_tickers": [ticker_rows[t]["symbol"] for t in top_tickers],
                "top_tickers_full": [t for t in top_tickers],
                "members":     member_list,
            })

        themes.sort(key=lambda x: x["score"] if x["score"] is not None else float("-inf"), reverse=True)

        return {
            "ok":              True,
            "updated":         datetime.now().strftime("%d/%m/%Y %H:%M"),
            "universe_loaded": len(combined),
            "themes":          themes,
            "total_themes":    len(themes),
        }

    except Exception as e:
        return {"ok": False, "error": str(e), "themes": []}
