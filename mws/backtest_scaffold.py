#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest Scaffold v3.8.0 — price/tech/RS rules only

Changes vs 3.2
  - Timezone-safe history index
  - No same-bar re-entry after an exit
  - --annual-slice is the real name; --walk-forward kept as alias and prints a warning
  - Does NOT claim to validate Winner composite (fundamentals/news not in this test)

Look-ahead still blocked: prior 60D high shift(1), vol_ma shift(1), next-open fill.
RSI/ATR: Wilder smoothing.

Usage:
  python backtest_scaffold.py AAPL
  python backtest_scaffold.py AAPL MSFT --years 8
  python backtest_scaffold.py AAPL --annual-slice --years 10
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance pandas numpy")
    sys.exit(1)

from decision import evaluate_decision, stance_allows_entry
from config import (
    MA_FAST, MA_MID, MA_SLOW, RS_LOOKBACK, BREAKOUT_LOOKBACK,
    VOL_MA, VOL_BREAKOUT_MULT, RSI_LEN, RSI_MAX_ENTRY,
    RSI_PB_LO, RSI_PB_HI, ATR_LEN, ATR_STOP_MULT,
    MAX_HOLD, TP_R, COST_BPS, PENDING_EXPIRY_DAYS,
    DEFAULT_PORTFOLIO, DEFAULT_RISK_PCT, REVISION,
)
from utils import naive_frame, strip_tz


def _history_retry(symbol: str, period: str, max_retries: int = 3) -> pd.DataFrame:
    last_err = None
    for attempt in range(max_retries):
        try:
            h = yf.Ticker(symbol).history(period=period, auto_adjust=True)
            if h is not None and not h.empty:
                h = naive_frame(h)
                if "Close" in h.columns:
                    h = h.dropna(subset=["Close"])
                return h
            last_err = "EMPTY"
        except Exception as e:
            last_err = str(e)
        time.sleep(min(1.5 * (2 ** attempt), 8))
    print(f"  warn {symbol} history failed after retries: {last_err}")
    return pd.DataFrame()


@dataclass
class Trade:
    ticker: str
    setup: str
    entry_date: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    shares: int
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    bars_held: Optional[int] = None
    r_multiple: Optional[float] = None


@dataclass
class BacktestResult:
    ticker: str
    trades: List[Trade] = field(default_factory=list)
    equity_curve: Optional[pd.Series] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    note: str = ""


def wilder_smooth(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi_series(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = wilder_smooth(gain, n)
    avg_loss = wilder_smooth(loss, n)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr_series(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev_c = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_c).abs(), (low - prev_c).abs()],
        axis=1,
    ).max(axis=1)
    return wilder_smooth(tr, n)


def aligned_rs(stock_close: pd.Series, spy_close: pd.Series, lookback: int) -> pd.Series:
    a = stock_close.copy()
    b = spy_close.copy()
    a.index = strip_tz(a.index)
    b.index = strip_tz(b.index)
    m = pd.concat([a.rename("s"), b.rename("b")], axis=1, join="inner").dropna()
    s_ret = m["s"] / m["s"].shift(lookback) - 1
    b_ret = m["b"] / m["b"].shift(lookback) - 1
    return ((s_ret - b_ret) * 100).reindex(stock_close.index)


def prepare_frame(ticker: str, spy: pd.DataFrame, years: int = 8) -> pd.DataFrame:
    hist = _history_retry(ticker, f"{years}y")
    if hist.empty or len(hist) < MA_SLOW + 50:
        raise ValueError(f"{ticker}: insufficient history")
    hist = naive_frame(hist)
    spy = naive_frame(spy)

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    vol = hist["Volume"]
    opn = hist["Open"]

    df = pd.DataFrame(index=hist.index)
    df["open"] = opn
    df["close"] = close
    df["high"] = high
    df["low"] = low
    df["volume"] = vol
    df["ma20"] = close.rolling(MA_FAST).mean()
    df["ma50"] = close.rolling(MA_MID).mean()
    df["ma200"] = close.rolling(MA_SLOW).mean()
    df["vol_ma20"] = vol.rolling(VOL_MA).mean().shift(1)
    df["high_60_prior"] = high.rolling(BREAKOUT_LOOKBACK).max().shift(1)
    df["low_60"] = low.rolling(BREAKOUT_LOOKBACK).min()
    df["rsi"] = rsi_series(close, RSI_LEN)
    df["atr"] = atr_series(high, low, close, ATR_LEN)
    df["rs3m"] = aligned_rs(close, spy["Close"], RS_LOOKBACK)
    return df.dropna()


def trend_ok(row) -> bool:
    return bool(
        row["close"] > row["ma50"]
        and row["ma50"] > row["ma200"]
        and row["rs3m"] > 0
        and row["rsi"] < RSI_MAX_ENTRY
    )


def signal_breakout(row) -> bool:
    if not trend_ok(row):
        return False
    if pd.isna(row["high_60_prior"]) or pd.isna(row["vol_ma20"]) or row["vol_ma20"] <= 0:
        return False
    return bool(
        row["close"] >= row["high_60_prior"]
        and row["volume"] >= VOL_BREAKOUT_MULT * row["vol_ma20"]
    )


def signal_pullback_ready(row) -> bool:
    if not trend_ok(row):
        return False
    near = abs(row["close"] - row["ma20"]) / row["close"] <= 0.03 or abs(
        row["close"] - row["ma50"]
    ) / row["close"] <= 0.04
    return bool(near and RSI_PB_LO <= row["rsi"] <= RSI_PB_HI)


def compute_stop(row, entry: float) -> float:
    candidates = []
    if not pd.isna(row["low_60"]):
        candidates.append(float(row["low_60"]))
    if not pd.isna(row["ma50"]):
        candidates.append(float(row["ma50"]))
    if not pd.isna(row["atr"]):
        candidates.append(entry - ATR_STOP_MULT * float(row["atr"]))
    below = [c for c in candidates if c < entry]
    stop = max(below) if below else entry * 0.94
    if stop >= entry:
        stop = entry * 0.94
    return stop


def _intraday_exit(row, trade: Trade) -> Tuple[Optional[float], Optional[str]]:
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    stop, target = trade.stop, trade.target
    hit_stop = l <= stop
    hit_tgt = h >= target
    if o <= stop:
        return o, "STOP_GAP"
    if hit_stop and hit_tgt:
        return stop, "STOP_SAMEBAR"
    if hit_stop:
        return stop, "STOP"
    if hit_tgt:
        return target, "TP2R"
    return None, None



def bar_decision(row, quality: int = 70, valuation: int = 50, dq: float = 80.0):
    """Map one prepared bar to v3.7 stance. Fundamentals frozen — price gates are the test."""
    price = float(row["close"]) if row.get("close") == row.get("close") else None
    mas = {"MA20": row.get("ma20"), "MA50": row.get("ma50"), "MA200": row.get("ma200")}
    rsi = None if row.get("rsi") != row.get("rsi") else float(row["rsi"])
    above200 = price is not None and mas["MA200"] == mas["MA200"] and price > float(mas["MA200"])
    above50 = price is not None and mas["MA50"] == mas["MA50"] and price > float(mas["MA50"])
    stack = mas["MA50"] == mas["MA50"] and mas["MA200"] == mas["MA200"] and float(mas["MA50"]) > float(mas["MA200"])
    if above200 and stack and above50:
        tech, stage = "🟢 Uptrend", "Stage 2 — Uptrend"
    elif above200 and not above50:
        tech, stage = "🟡 Correction", "Correction — holding 200-day"
    elif above200:
        tech, stage = "🟡 Base", "Stage 1/3 — Base / Range"
    else:
        tech, stage = "🔴 Downtrend", "Stage 4 — Downtrend"
    rs3 = row.get("rs3m")
    rs_verdict = "🔴 Laggard" if (rs3 == rs3 and rs3 is not None and float(rs3) < -5) else "🟡 Neutral"
    return evaluate_decision(
        dq=dq,
        quality=quality,
        momentum=60,
        valuation=valuation,
        risk=20,
        tech_verdict=tech,
        stage=stage,
        rs_verdict=rs_verdict,
        avg_rs=None if rs3 != rs3 else float(rs3) if rs3 is not None else None,
        price=price,
        mas=mas,
        rsi=rsi,
        regime="🟢 Risk-On",
        days_to_earnings=None,
        fcf=1.0,
        de_norm=40.0,
    )


def decision_allows_bar(row) -> bool:
    return stance_allows_entry(bar_decision(row)["stance"])


def run_ticker_backtest(
    ticker: str,
    spy_hist: pd.DataFrame,
    years: int = 8,
    portfolio: float = DEFAULT_PORTFOLIO,
    risk_pct: float = DEFAULT_RISK_PCT,
    start: Optional[pd.Timestamp] = None,
    end: Optional[pd.Timestamp] = None,
) -> BacktestResult:
    df = prepare_frame(ticker, spy_hist, years=years)
    if start is not None:
        df = df[df.index >= pd.to_datetime(start)]
    if end is not None:
        df = df[df.index <= pd.to_datetime(end)]
    if df.empty or len(df) < 30:
        raise ValueError(f"{ticker}: empty slice after date filter")

    result = BacktestResult(ticker=ticker)
    cash = float(portfolio)
    equity = float(portfolio)

    i = 0
    n = len(df)
    dates = df.index
    open_trade: Optional[Trade] = None
    eq_path = []
    pending_breakout = None
    pending_pullback = None
    pending_pb_fill = None

    while i < n:
        row = df.iloc[i]
        dt = dates[i]
        exited_today = False

        if pending_breakout is not None and (dt - pending_breakout[0]).days > PENDING_EXPIRY_DAYS:
            pending_breakout = None
        if pending_pb_fill is not None:
            pb_sig_dt = pending_pb_fill[0].name
            if (dt - pb_sig_dt).days > PENDING_EXPIRY_DAYS:
                pending_pb_fill = None

        def _open_from(setup: str, sig_row) -> Optional[Trade]:
            nonlocal cash
            entry = float(row["open"])
            stop = compute_stop(sig_row, entry)
            if stop >= entry:
                stop = entry * 0.94
            risk_ps = entry - stop
            if risk_ps <= 0 or cash <= 0:
                return None
            risk_usd = equity * (risk_pct / 100.0)
            shares = int(risk_usd // risk_ps)
            max_shares = int(cash // entry)
            shares = min(shares, max_shares)
            if shares <= 0:
                return None
            notional = shares * entry
            cash -= notional
            return Trade(
                ticker=ticker,
                setup=setup,
                entry_date=dt,
                entry_price=entry,
                stop=stop,
                target=entry + TP_R * risk_ps,
                shares=shares,
                bars_held=0,
            )

        if open_trade is None and pending_breakout is not None:
            open_trade = _open_from("A_BREAKOUT", pending_breakout[1])
            pending_breakout = None

        if open_trade is None and pending_pb_fill is not None:
            open_trade = _open_from("B_PULLBACK", pending_pb_fill[0])
            pending_pb_fill = None

        if open_trade is not None:
            open_trade.bars_held = (open_trade.bars_held or 0) + 1
            exit_px, reason = _intraday_exit(row, open_trade)
            if exit_px is None:
                if row["close"] < row["ma50"] and row["rs3m"] < 0:
                    exit_px = float(row["close"])
                    reason = "TREND_EXIT"
                elif open_trade.bars_held >= MAX_HOLD:
                    exit_px = float(row["close"])
                    reason = "TIME"

            if exit_px is not None:
                cost = (open_trade.entry_price + exit_px) * COST_BPS / 10000.0 * open_trade.shares
                pnl = (exit_px - open_trade.entry_price) * open_trade.shares - cost
                risk_ps = open_trade.entry_price - open_trade.stop
                open_trade.exit_date = dt
                open_trade.exit_price = exit_px
                open_trade.exit_reason = reason
                open_trade.pnl = pnl
                open_trade.pnl_pct = (exit_px / open_trade.entry_price - 1) * 100
                open_trade.r_multiple = (
                    (exit_px - open_trade.entry_price) / risk_ps if risk_ps > 0 else None
                )
                cash += open_trade.shares * exit_px
                cash -= cost
                equity = cash
                result.trades.append(open_trade)
                open_trade = None
                exited_today = True

        marked = cash
        if open_trade is not None:
            marked = cash + open_trade.shares * float(row["close"])
        equity = marked
        eq_path.append(equity)

        if open_trade is not None or exited_today:
            i += 1
            continue

        if pending_pullback is not None:
            pref_dt, ref = pending_pullback
            vol_base = row["vol_ma20"] if not pd.isna(row["vol_ma20"]) else 0
            if (
                row["close"] > ref
                and vol_base > 0
                and row["volume"] >= 1.2 * vol_base
                and trend_ok(row)
            ):
                pending_pb_fill = (row, ref)
                pending_pullback = None
            elif (dt - pref_dt).days > 10:
                pending_pullback = None
            i += 1
            continue

        if signal_breakout(row) and decision_allows_bar(row):
            pending_breakout = (dt, row)
        elif signal_pullback_ready(row) and decision_allows_bar(row):
            pending_pullback = (dt, float(row["close"]))
        i += 1

    if open_trade is not None:
        row = df.iloc[-1]
        exit_px = float(row["close"])
        cost = (open_trade.entry_price + exit_px) * COST_BPS / 10000.0 * open_trade.shares
        pnl = (exit_px - open_trade.entry_price) * open_trade.shares - cost
        risk_ps = open_trade.entry_price - open_trade.stop
        open_trade.exit_date = dates[-1]
        open_trade.exit_price = exit_px
        open_trade.exit_reason = "EOD_FORCE"
        open_trade.pnl = pnl
        open_trade.pnl_pct = (exit_px / open_trade.entry_price - 1) * 100
        open_trade.r_multiple = (
            (exit_px - open_trade.entry_price) / risk_ps if risk_ps > 0 else None
        )
        result.trades.append(open_trade)
        cash += open_trade.shares * exit_px - cost
        eq_path.append(cash)

    result.equity_curve = pd.Series(eq_path, dtype=float)
    result.metrics = summarize_trades(result.trades, portfolio, result.equity_curve)
    return result


def summarize_trades(
    trades: List[Trade], portfolio: float, equity_curve: Optional[pd.Series] = None
) -> Dict[str, float]:
    empty = {
        "n_trades": 0.0, "win_rate": 0.0, "profit_factor": 0.0,
        "expectancy_usd": 0.0, "total_pnl": 0.0, "total_return_pct": 0.0,
        "avg_r": 0.0, "max_dd_pct": 0.0, "avg_hold_bars": 0.0,
    }
    if not trades:
        return empty
    pnls = [t.pnl for t in trades if t.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    rs = [t.r_multiple for t in trades if t.r_multiple is not None]
    holds = [t.bars_held for t in trades if t.bars_held is not None]

    if equity_curve is not None and len(equity_curve) > 1:
        eq_s = equity_curve.astype(float)
    else:
        eq = [portfolio]
        for p in pnls:
            eq.append(eq[-1] + p)
        eq_s = pd.Series(eq)
    peak = eq_s.cummax()
    dd = (eq_s - peak) / peak.replace(0, np.nan) * 100
    max_dd = float(dd.min()) if len(dd) else 0.0
    last_eq = float(eq_s.iloc[-1]) if len(eq_s) else portfolio

    return {
        "n_trades": float(len(trades)),
        "win_rate": float(len(wins) / len(pnls) * 100) if pnls else 0.0,
        "profit_factor": float(pf),
        "expectancy_usd": float(np.mean(pnls)) if pnls else 0.0,
        "total_pnl": float(sum(pnls)),
        "total_return_pct": float((last_eq / portfolio - 1) * 100),
        "avg_r": float(np.mean(rs)) if rs else 0.0,
        "max_dd_pct": max_dd,
        "avg_hold_bars": float(np.mean(holds)) if holds else 0.0,
    }


def buy_hold_return(ticker: str, years: int = 8) -> float:
    h = _history_retry(ticker, f"{years}y")
    if h.empty or len(h) < 2:
        return float("nan")
    return float(h["Close"].iloc[-1] / h["Close"].iloc[0] - 1) * 100


def annual_slice_windows(years: int) -> List[Tuple[str, str, str, str]]:
    """
    Frozen-rule annual test slices. Train dates are printed only as reference;
    thresholds are not fit on train. This is NOT a parameter walk-forward.
    """
    end_year = pd.Timestamp.today().year - 1
    start_year = end_year - years + 1
    folds = []
    train_start = start_year
    test_year = start_year + 4
    while test_year <= end_year:
        folds.append(
            (
                f"{train_start}-01-01",
                f"{test_year-1}-12-31",
                f"{test_year}-01-01",
                f"{test_year}-12-31",
            )
        )
        test_year += 1
    return folds


def print_report(results: List[BacktestResult], years: int, benchmark: str):
    print("=" * 72)
    print(f"BACKTEST SCAFFOLD v{REVISION} — price/tech/RS only (no fundamental/news)")
    print("Look-ahead blocked: prior 60D high shift1; vol_ma20 shift1; RS aligned")
    print("RSI/ATR Wilder | entry next open | stop uses Low (gap = Open)")
    print("Same-bar stop+target → stop first | no re-entry on exit bar")
    print("size = current equity, capped by cash")
    print("Costs: {:.1f} bps/side | max hold {}".format(COST_BPS, MAX_HOLD))
    print("=" * 72)

    bh_bench = buy_hold_return(benchmark, years)
    print(f"\nBenchmark {benchmark} buy&hold ~{years}y: {bh_bench:+.1f}%")
    print("-" * 72)

    for r in results:
        m = r.metrics
        bh = buy_hold_return(r.ticker, years)
        print(f"\n{r.ticker}")
        pf = m["profit_factor"]
        pf_str = "n/a (no losing trades)" if pf >= 999 else f"{pf:.2f}"
        print(
            f"  trades={int(m['n_trades'])}  win%={m['win_rate']:.1f}  "
            f"PF={pf_str}  expectancy=${m['expectancy_usd']:.0f}"
        )
        print(
            f"  total_pnl=${m['total_pnl']:.0f} ({m['total_return_pct']:+.1f}% on equity path)  "
            f"avg_R={m['avg_r']:.2f}  maxDD={m['max_dd_pct']:.1f}%  hold={m['avg_hold_bars']:.1f} bars"
        )
        print(f"  buy&hold {r.ticker}: {bh:+.1f}%  |  vs {benchmark} B&H: {bh_bench:+.1f}%")
        if r.trades:
            by: Dict[str, list] = {}
            for t in r.trades:
                by.setdefault(t.setup, []).append(t.pnl or 0)
            for setup, pnls in by.items():
                w = sum(1 for p in pnls if p > 0)
                print(f"    {setup}: n={len(pnls)} win%={w/len(pnls)*100:.0f} sum=${sum(pnls):.0f}")
            print("  last trades:")
            for t in r.trades[-5:]:
                r_mult = t.r_multiple if t.r_multiple is not None else float("nan")
                print(
                    f"    {t.entry_date.date()} {t.setup} entry={t.entry_price:.2f} "
                    f"exit={t.exit_price:.2f} ({t.exit_reason}) R={r_mult:.2f} pnl=${t.pnl:.0f}"
                )

    if len(results) > 1:
        print("\n" + "=" * 72)
        print("MULTI-TICKER NOTE")
        print("Names above are independent single-name paths.")
        print("They are NOT a portfolio backtest (no shared cash, no correlation,")
        print("no sector cap). Do not sum PnL across names and call it a book.")
        n_all = sum(int(r.metrics.get("n_trades", 0)) for r in results)
        print(f"Total isolated trades printed: {n_all}")

    print("\n" + "=" * 72)
    print("DISCLAIMER")
    print("• Scaffold only — not production / not live trading.")
    print("• Price entries must also pass v3.7 stance CANDIDATE (frozen quality/valuation). Analyst targets unused.")
    print("• Does NOT prove Winner composite has predictive power.")
    print("• Annual slices use frozen rules; they do not tune on train.")
    print("• No survivorship-bias-free universe. Past ≠ future. Not advice.")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description=f"Backtest scaffold v{REVISION}")
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--years", type=int, default=8)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--annual-slice", action="store_true",
                        help="Frozen-rule annual test slices (not parameter walk-forward)")
    parser.add_argument("--walk-forward", action="store_true",
                        help="Deprecated alias of --annual-slice")
    args = parser.parse_args()

    portfolio = float(os.getenv("MWS_PORTFOLIO", str(DEFAULT_PORTFOLIO)))
    risk_pct = float(os.getenv("MWS_RISK_PCT", str(DEFAULT_RISK_PCT)))

    print(f"Loading {args.benchmark} history ({args.years}y)...")
    spy = _history_retry(args.benchmark, f"{args.years}y")
    if spy.empty:
        print("Failed to load benchmark")
        sys.exit(1)

    run_slices = args.annual_slice or args.walk_forward
    if args.walk_forward:
        print("NOTE: --walk-forward is an alias. Rules are frozen; train window is not used to fit.")
        print("      Prefer --annual-slice. This does not validate the Winner composite.\n")

    if run_slices:
        folds = annual_slice_windows(args.years)
        print(f"Frozen-rule annual slices: {len(folds)}")
        for train_s, train_e, test_s, test_e in folds:
            print(f"\n--- SLICE ref-window {train_s}..{train_e} | test {test_s}..{test_e} ---")
            print("(Thresholds are frozen; test-window expectancy only. Not a tuned walk-forward.)")
            fold_results = []
            for t in args.tickers:
                t = t.upper()
                try:
                    fold_results.append(
                        run_ticker_backtest(
                            t, spy, years=args.years,
                            portfolio=portfolio, risk_pct=risk_pct,
                            start=test_s, end=test_e,
                        )
                    )
                except Exception as e:
                    print(f"  SKIP {t}: {e}")
            if fold_results:
                print_report(fold_results, args.years, args.benchmark)
        return

    results = []
    for t in args.tickers:
        t = t.upper()
        print(f"Backtesting {t}...")
        try:
            results.append(
                run_ticker_backtest(
                    t, spy, years=args.years, portfolio=portfolio, risk_pct=risk_pct
                )
            )
        except Exception as e:
            print(f"  SKIP {t}: {e}")
    if not results:
        print("No results")
        sys.exit(1)
    print_report(results, args.years, args.benchmark)


if __name__ == "__main__":
    main()
