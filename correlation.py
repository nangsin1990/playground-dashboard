"""
correlation.py — Correlation Matrix Engine
==========================================
Computes pairwise daily-return correlations for key US assets.
Assets: SPY QQQ IWM DIA / XLK XLF XLE XLV / TLT IEF HYG / GLD SLV USO / UUP VXX

Uses its own small fetch (not the full pipeline) — fast, no universe dependency.
"""

from __future__ import annotations
from datetime import datetime

import numpy as np
import pandas as pd

import data_io
import data_engine as eng
from cache_utils import ttl_cache
from constants import CACHE_TTL_DATA, CORR_TICKERS, CORR_PERIOD_DAYS, CORR_BENCHMARK

# Group labels for display
CORR_GROUPS = {
    "SPY":  "Broad", "QQQ":  "Broad", "IWM":  "Broad", "DIA":  "Broad",
    "XLK":  "Sector","XLF":  "Sector","XLE":  "Sector","XLV":  "Sector",
    "TLT":  "Bonds", "IEF":  "Bonds", "HYG":  "Bonds",
    "GLD":  "Cmdty", "SLV":  "Cmdty", "USO":  "Cmdty",
    "DXY":  "FX",    "UUP":  "FX",
    "VXX":  "Vol",
}

# Color coding per group (for frontend heatmap)
GROUP_COLORS = {
    "Broad":  "#6366f1",
    "Sector": "#3b82f6",
    "Bonds":  "#10b981",
    "Cmdty":  "#f59e0b",
    "FX":     "#ec4899",
    "Vol":    "#ef4444",
}


@ttl_cache(CACHE_TTL_DATA)
def fetch_correlation() -> dict:
    """
    Fetch CORR_TICKERS, compute pairwise correlation of daily returns.
    Returns heatmap-ready matrix with labels, groups, and color hints.
    """
    tickers = tuple(CORR_TICKERS)
    raw     = data_io.fetch_batch(tickers)

    # Build close price dict
    combined = {t: raw[t] for t in tickers if raw.get(t) is not None}

    if len(combined) < 4:
        return {"ok": False, "error": "Not enough data for correlation", "labels": []}

    result = eng.compute_correlation_matrix(combined, list(combined.keys()), days=CORR_PERIOD_DAYS)
    if not result.get("ok"):
        return result

    labels  = result["labels"]
    matrix  = result["matrix"]
    groups  = [CORR_GROUPS.get(t, "Other") for t in labels]
    g_colors = [GROUP_COLORS.get(CORR_GROUPS.get(t, "Other"), "#6b7280") for t in labels]

    # Per-row stats (min, max, avg absolute correlation)
    row_stats = []
    for i, row in enumerate(matrix):
        vals = [v for j, v in enumerate(row) if j != i and v is not None]
        if vals:
            row_stats.append({
                "ticker": labels[i],
                "avg_abs_corr": round(float(np.mean([abs(v) for v in vals])), 3),
                "max_corr":     round(float(max(vals)), 3),
                "min_corr":     round(float(min(vals)), 3),
            })
        else:
            row_stats.append({"ticker": labels[i], "avg_abs_corr": 0, "max_corr": 0, "min_corr": 0})

    # Highest absolute correlations (cross-asset pairs, excluding self)
    pairs = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            v = matrix[i][j]
            if v is not None:
                pairs.append({"a": labels[i], "b": labels[j], "corr": v})
    pairs.sort(key=lambda x: abs(x["corr"]), reverse=True)

    return {
        "ok":          True,
        "updated":     datetime.now().strftime("%d/%m/%Y %H:%M"),
        "labels":      labels,
        "groups":      groups,
        "group_colors": g_colors,
        "matrix":      matrix,
        "row_stats":   row_stats,
        "top_pairs":   pairs[:10],
        "period_days": CORR_PERIOD_DAYS,
        "benchmark":   CORR_BENCHMARK,
        "note":        f"Pairwise daily-return correlations over {CORR_PERIOD_DAYS} trading days (~3 months). Range: −1 to +1.",
    }
