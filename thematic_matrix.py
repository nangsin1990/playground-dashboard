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
        active = pipeline.active_universe(mode)
        combined, ticker_meta, _ = pipeline.fetch_universe(active)

        if not combined:
            return {"ok": False, "error": "No data from yfinance", "themes": []}

        # Per-market RS (no cross-market mixing)
        rs_now = eng.rs_rating_per_market(combined, ticker_meta)

        # Build per-ticker rows
        ticker_rows: dict[str, dict] = {}
        for t, d in combined.items():
            meta  = ticker_meta.get(t, {})
            close = d["Close"]
            ticker_rows[t] = {
                "ticker":  t.split(".")[0],
                "name":    meta.get("name", t),
                "theme":   meta.get("theme", "Unknown"),
                "market":  meta.get("market", ""),
                "r1d":     _safe_pct(close, 1),
                "r1m":     _safe_pct(close, TRADING_DAYS_MONTH),
                "r3m":     _safe_pct(close, TRADING_DAYS_QUARTER),
                "rs":      int(rs_now.get(t, 0)),
                "close":   round(float(close.iloc[-1]), 2),
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
            rs_vals  = [ticker_rows[t]["rs"]  for t in members]

            avg_r1d = round(float(np.mean(r1d_vals)), 2) if r1d_vals else 0.0
            avg_r1m = round(float(np.mean(r1m_vals)), 2) if r1m_vals else 0.0
            avg_r3m = round(float(np.mean(r3m_vals)), 2) if r3m_vals else 0.0
            avg_rs  = int(np.mean(rs_vals)) if rs_vals else 0
            score   = avg_r1m + avg_r3m

            # Markets in this theme
            markets = list(set(ticker_rows[t]["market"] for t in members))

            # Top tickers by RS
            top_tickers = sorted(members, key=lambda t: ticker_rows[t]["rs"], reverse=True)[:THEMATIC_TOP_TICKERS]

            # Member rows (sorted by RS, capped)
            member_list = sorted(
                [ticker_rows[t] for t in members],
                key=lambda r: r["rs"],
                reverse=True,
            )[:THEMATIC_MAX_MEMBERS]

            themes.append({
                "theme":       theme,
                "markets":     markets,
                "count":       len(members),
                "r1d":         avg_r1d,
                "r1m":         avg_r1m,
                "r3m":         avg_r3m,
                "score":       round(score, 2),
                "avg_rs":      avg_rs,
                "top_tickers": [t.split(".")[0] for t in top_tickers],
                "members":     member_list,
            })

        themes.sort(key=lambda x: x["score"], reverse=True)

        return {
            "ok":              True,
            "updated":         datetime.now().strftime("%d/%m/%Y %H:%M"),
            "universe_loaded": len(combined),
            "themes":          themes,
            "total_themes":    len(themes),
        }

    except Exception as e:
        import traceback
        return {
            "ok":    False,
            "error": str(e),
            "trace": traceback.format_exc()[-500:],
            "themes": [],
        }
