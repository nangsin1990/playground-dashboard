#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Accumulation Radar v3.8.0
Heuristic research tool — score is NOT a probability of accumulation.

Usage:
    python accumulation_radar.py AAPL
    python accumulation_radar.py AAPL --universe MSFT,NVDA,AMD,AVGO,TSM
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

try:
    from .config import REVISION
    from .data_provider import yf_history_retry
    from .utils import align_closes, fmt, fmt_pct, naive_frame, safe_div
except ImportError:
    from config import REVISION
    from data_provider import yf_history_retry
    from utils import align_closes, fmt, fmt_pct, naive_frame, safe_div

logging.basicConfig(
    level=os.getenv("MWS_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("accumulation_radar")


class AccumulationRadar:
    def __init__(
        self,
        ticker: str,
        universe: Optional[List[str]] = None,
        lookback_days: int = 63,
        down_day_lookback: int = 20,
        period: str = "1y",
    ):
        self.ticker = ticker.upper().strip()
        self.universe = [t.upper().strip() for t in (universe or []) if t]
        if self.ticker not in self.universe and self.universe:
            self.universe = [self.ticker] + self.universe
        self.lookback_days = lookback_days
        self.down_day_lookback = down_day_lookback
        self.period = period
        self.hist = pd.DataFrame()
        self.spy_hist = pd.DataFrame()
        self.metrics: Dict[str, Any] = {}
        self.universe_metrics: Dict[str, Dict[str, Any]] = {}
        self.percentile: Optional[float] = None
        self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def fetch(self) -> bool:
        try:
            self.hist = yf_history_retry(self.ticker, self.period)
            if self.hist.empty or len(self.hist) < 40:
                logger.error("%s: insufficient history", self.ticker)
                return False
            self.spy_hist = yf_history_retry("SPY", self.period)
            if self.spy_hist.empty:
                logger.warning("SPY history failed — resilience metrics degraded")
            self.metrics = self._compute_metrics(self.hist, self.spy_hist)
            if self.universe:
                self._fetch_universe()
            return True
        except Exception:
            logger.exception("fetch failed for %s", self.ticker)
            return False

    def _fetch_universe(self):
        tickers = list(dict.fromkeys(self.universe))
        try:
            raw = yf.download(
                tickers, period=self.period, group_by="ticker",
                auto_adjust=True, progress=False, threads=True,
            )
        except Exception as e:
            logger.warning("Universe batch download failed: %s", e)
            return
        for t in tickers:
            try:
                h = raw if len(tickers) == 1 else raw[t] if t in raw.columns.get_level_values(0) else None
                if h is None or h.empty or len(h) < 40:
                    continue
                self.universe_metrics[t] = self._compute_metrics(naive_frame(h), self.spy_hist)
            except Exception as e:
                logger.debug("Universe metric failed for %s: %s", t, e)
        if self.ticker in self.universe_metrics and len(self.universe_metrics) >= 3:
            scores = [m.get("accumulation_score", 0) for m in self.universe_metrics.values()]
            target_score = self.metrics.get("accumulation_score", 0)
            self.percentile = float(100.0 * (np.sum(np.array(scores) <= target_score) / len(scores)))
            self.metrics["percentile"] = self.percentile

    def _compute_metrics(self, hist: pd.DataFrame, spy: pd.DataFrame) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        close = hist["Close"]
        high = hist["High"]
        low = hist["Low"]
        volume = hist["Volume"]

        out["price"] = float(close.iloc[-1])
        out["ma20"] = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else None
        out["ma50"] = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
        out["ma200"] = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta.clip(upper=0))
        avg_gain = gain.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
        avg_loss = loss.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_val = 100 - (100 / (1 + rs.iloc[-1]))
        out["rsi"] = float(rsi_val) if not np.isnan(rsi_val) else None

        over = False
        if out["rsi"] is not None and out["rsi"] > 75:
            over = True
        if out["ma20"] and out["price"] > out["ma20"] * 1.12:
            over = True
        out["overextended"] = over

        daily_clv = (close - low) / (high - low).replace(0, np.nan)
        out["clv_last"] = float(daily_clv.iloc[-1]) if not np.isnan(daily_clv.iloc[-1]) else None
        out["clv_20d_avg"] = float(daily_clv.tail(20).mean()) if len(daily_clv) >= 20 else None

        look = min(self.lookback_days, len(hist) - 1)
        recent = hist.tail(look + 1).copy()
        recent["ret"] = recent["Close"].pct_change()
        up_days = recent[recent["ret"] > 0]
        down_days = recent[recent["ret"] < 0]
        up_vol = up_days["Volume"].mean() if len(up_days) > 0 else np.nan
        down_vol = down_days["Volume"].mean() if len(down_days) > 0 else np.nan
        out["up_vol_avg"] = float(up_vol) if not np.isnan(up_vol) else None
        out["down_vol_avg"] = float(down_vol) if not np.isnan(down_vol) else None
        out["up_down_vol_ratio"] = float(safe_div(up_vol, down_vol, np.nan))

        vol_ma20 = volume.rolling(20).mean()
        down_vol_vs_ma = []
        for i in range(1, min(look + 1, len(hist))):
            if hist["Close"].iloc[-i] < hist["Close"].iloc[-i - 1]:
                v = volume.iloc[-i]
                ma = vol_ma20.iloc[-i]
                if ma and ma > 0:
                    down_vol_vs_ma.append(v / ma)
        out["down_day_vol_vs_ma"] = float(np.mean(down_vol_vs_ma)) if down_vol_vs_ma else None
        out["volume_dryup"] = out["down_day_vol_vs_ma"] is not None and out["down_day_vol_vs_ma"] < 0.85

        tr = pd.concat(
            [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
            axis=1,
        ).max(axis=1)
        atr_10 = tr.rolling(10).mean().iloc[-1] if len(tr) >= 10 else np.nan
        atr_30 = tr.rolling(30).mean().iloc[-1] if len(tr) >= 30 else np.nan
        out["atr_ratio"] = float(safe_div(atr_10, atr_30, np.nan))
        out["range_contracting"] = out["atr_ratio"] is not None and out["atr_ratio"] < 0.85

        out.update(self._down_market_resilience(hist, spy))

        pullback_quality = "WEAK"
        if out["ma50"] and out["price"] > out["ma50"]:
            near_ma20 = out["ma20"] and abs(out["price"] - out["ma20"]) / out["price"] < 0.04
            near_ma50 = abs(out["price"] - out["ma50"]) / out["price"] < 0.05
            rsi_ok = out["rsi"] is not None and 35 <= out["rsi"] <= 65
            if (near_ma20 or near_ma50) and rsi_ok:
                pullback_quality = "STRONG"
            elif near_ma20 or near_ma50:
                pullback_quality = "MODERATE"
        out["pullback_quality"] = pullback_quality
        out["accumulation_score"] = self._score_accumulation(out)
        return out

    def _down_market_resilience(self, hist: pd.DataFrame, spy: pd.DataFrame) -> Dict[str, Any]:
        out = {
            "down_market_resilience": None,
            "down_days_count": 0,
            "avg_stock_on_spy_down": None,
            "avg_spy_on_down_days": None,
        }
        if spy is None or spy.empty or hist.empty:
            return out
        stock_c, spy_c = align_closes(hist["Close"], spy["Close"])
        if len(stock_c) < 30:
            return out
        stock_ret = stock_c.pct_change()
        spy_ret = spy_c.pct_change()
        window = min(self.down_day_lookback * 3, len(stock_ret) - 1)
        s_ret = stock_ret.iloc[-window:]
        sp_ret = spy_ret.iloc[-window:]
        mask = sp_ret < 0
        down_days = mask.sum()
        out["down_days_count"] = int(down_days)
        if down_days < 3:
            return out
        avg_stock = float(s_ret[mask].mean() * 100)
        avg_spy = float(sp_ret[mask].mean() * 100)
        out["avg_stock_on_spy_down"] = avg_stock
        out["avg_spy_on_down_days"] = avg_spy
        out["down_market_resilience"] = avg_stock - avg_spy
        return out

    def _score_accumulation(self, m: Dict[str, Any]) -> int:
        score = 35
        res = m.get("down_market_resilience")
        if res is not None:
            if res >= 1.5:
                score += 25
            elif res >= 0.8:
                score += 20
            elif res >= 0.3:
                score += 12
            elif res >= 0:
                score += 5
            elif res >= -0.5:
                score += 0
            else:
                score -= 8
        ud = m.get("up_down_vol_ratio")
        if ud is not None and not np.isnan(ud):
            if ud >= 1.8:
                score += 20
            elif ud >= 1.4:
                score += 15
            elif ud >= 1.15:
                score += 9
            elif ud >= 0.95:
                score += 3
            else:
                score -= 5
        if m.get("volume_dryup"):
            score += 12
        elif m.get("down_day_vol_vs_ma") is not None and m["down_day_vol_vs_ma"] < 1.0:
            score += 5
        clv = m.get("clv_20d_avg")
        if clv is not None:
            if clv >= 0.70:
                score += 10
            elif clv >= 0.55:
                score += 6
            elif clv >= 0.45:
                score += 2
        if m.get("range_contracting"):
            score += 8
        elif m.get("atr_ratio") is not None and m["atr_ratio"] < 1.0:
            score += 3
        pq = m.get("pullback_quality")
        if pq == "STRONG":
            score += 8
        elif pq == "MODERATE":
            score += 4
        if m.get("ma50") and m.get("price") and m["price"] > m["ma50"]:
            score += 4
        if m.get("ma200") and m.get("price") and m["price"] > m["ma200"]:
            score += 3
        if m.get("overextended"):
            score -= 15
        return int(max(0, min(100, score)))

    def verdict(self) -> str:
        m = self.metrics
        score = m.get("accumulation_score", 0)
        over = m.get("overextended", False)
        res = m.get("down_market_resilience")
        if over:
            return "OVEREXTENDED — wait for healthier entry"
        if score >= 80 and (res is None or res >= 0.3):
            return "STEALTH ACCUMULATION CANDIDATE (heuristic)"
        if score >= 70:
            return "ACCUMULATION LIKELY (heuristic)"
        if score >= 55:
            return "MIXED / WATCH"
        if score >= 40:
            return "WEAK ACCUMULATION SIGNAL"
        return "NO CLEAR ACCUMULATION"

    def report(self) -> str:
        m = self.metrics
        if not m:
            return f"No metrics computed for {self.ticker}"
        lines = [
            f"ACCUMULATION RADAR — {self.ticker}  (v{REVISION})",
            f"Scan Time (UTC): {self.timestamp}",
            "",
            "This number is a heuristic score, not P(accumulation).",
            "",
            f"Accumulation Score     {m.get('accumulation_score', 0)}/100",
        ]
        if self.percentile is not None:
            lines.append(f"Percentile (universe)  {self.percentile:.0f}th")
        else:
            lines.append("Percentile (universe)  N/A")
        lines += [
            "",
            f"Down-Market Resilience {fmt_pct(m.get('down_market_resilience'))}",
            f"  (stock on SPY-down: {fmt_pct(m.get('avg_stock_on_spy_down'))} | "
            f"SPY: {fmt_pct(m.get('avg_spy_on_down_days'))} | n={m.get('down_days_count', 0)})",
            f"Up/Down Volume Ratio   {fmt(m.get('up_down_vol_ratio'), 2)}x",
            f"Volume Dry-up (red)    {'PASS' if m.get('volume_dryup') else 'FAIL / N/A'}"
            + (
                f"  (down-day vol vs MA20 = {fmt(m.get('down_day_vol_vs_ma'), 2)}x)"
                if m.get("down_day_vol_vs_ma") is not None
                else ""
            ),
            f"Close Location (20d)   {fmt(m.get('clv_20d_avg')*100 if m.get('clv_20d_avg') is not None else None, 0)}%",
            f"Range Contracting      {'YES' if m.get('range_contracting') else 'NO'}",
            f"Pullback Quality       {m.get('pullback_quality', 'N/A')}",
            f"Overextended           {'YES' if m.get('overextended') else 'NO'}",
            "",
            f"Verdict: {self.verdict()}",
            "",
            "Context",
            f"  Price          {fmt(m.get('price'))}",
            f"  MA20 / MA50    {fmt(m.get('ma20'))} / {fmt(m.get('ma50'))}",
            f"  RSI(14)        {fmt(m.get('rsi'), 1)}",
            "",
            "Read as phenotype, not probability.",
            "Pair with Market Winner Scanner + regime. Not a buy signal.",
        ]
        if self.universe and self.universe_metrics:
            lines.append("")
            lines.append(f"Universe size: {len(self.universe_metrics)}")
            ranked = sorted(
                self.universe_metrics.items(),
                key=lambda x: x[1].get("accumulation_score", 0),
                reverse=True,
            )[:5]
            lines.append("Top 5 by heuristic score:")
            for t, mm in ranked:
                marker = " <-" if t == self.ticker else ""
                lines.append(
                    f"  {t:<6} {mm.get('accumulation_score', 0):>3}/100  "
                    f"Res={fmt_pct(mm.get('down_market_resilience'))}  "
                    f"UD={fmt(mm.get('up_down_vol_ratio'), 2)}x{marker}"
                )
        return "\n".join(lines)


def main():
    import argparse
    import json
    from pathlib import Path
    try:
        from .payload import build_radar_payload
    except ImportError:
        from payload import build_radar_payload
    parser = argparse.ArgumentParser(description="Accumulation Radar v3.4")
    parser.add_argument("ticker")
    parser.add_argument("--universe", type=str, default="")
    parser.add_argument("--lookback", type=int, default=63)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()
    universe = [t.strip() for t in args.universe.split(",") if t.strip()]
    print(f"Running Accumulation Radar v{REVISION} on {args.ticker.upper()} ...\n")
    radar = AccumulationRadar(args.ticker, universe=universe, lookback_days=args.lookback)
    if not radar.fetch():
        sys.exit(1)
    payload = build_radar_payload(radar)
    payload["ok"] = True
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"Wrote {args.out}")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(radar.report())


if __name__ == "__main__":
    main()
