#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Market Winner Scanner v3.8.0
Research scanner — NOT an automated trading engine.

Changes vs 3.2
  - Timezone-safe Yahoo history
  - D/E unit lock (ratio vs percent)
  - Catalyst = 0 when no news (no free mid-score)
  - Research composite: Quality 35 / Momentum 25 / Valuation 20 / inverted Risk 20
  - Winner label uses composite, not momentum-heavy legacy total
  - Separate BUSINESS vs PRICE labels
  - DQ technical credit requires ATR/MACD, not RSI alone
  - ROIC shown only as proxy

Usage:
    export FRED_API_KEY="..."
    export FINNHUB_API_KEY="..."
    python market_winner_scanner.py <TICKER>
"""

from __future__ import annotations

import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone, timedelta, date as dt_date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

from config import (
    FRED_API_KEY,
    FINNHUB_API_KEY,
    LOG_LEVEL,
    SECTOR_ETF,
    PEER_MAP,
    INDUSTRY_PEERS,
    CACHE_TTL_SEC,
    REVISION,
    DQ_GATE,
)
from data_provider import (
    cache_get,
    cache_set,
    cache_size,
    record_metric,
    request_with_retry,
    yf_history_retry,
    API_METRICS,
)
from scoring import (
    score_clamp,
    earnings_growth_safe,
    headline_sentiment,
    quality_score,
    momentum_score,
    valuation_score,
    risk_score,
    data_quality_score,
    investment_profile,
    legacy_bucket_scores,
    research_composite,
    dual_labels,
)
from utils import fmt, fmt_pct, safe_get, strip_tz, normalize_debt_to_equity

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("market_winner_scanner")

try:
    from fredapi import Fred
    FREDAPI_AVAILABLE = True
except ImportError:
    FREDAPI_AVAILABLE = False

try:
    import finnhub
    FINNHUB_LIB_AVAILABLE = True
except ImportError:
    FINNHUB_LIB_AVAILABLE = False

try:
    from ta.momentum import RSIIndicator
    from ta.volatility import AverageTrueRange, BollingerBands
    from ta.trend import ADXIndicator, MACD
    from ta.volume import OnBalanceVolumeIndicator, AccDistIndexIndicator
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False


class MarketWinnerScanner:
    def __init__(self, ticker: str):
        self.ticker_symbol = ticker.upper().strip()
        self.stock = yf.Ticker(self.ticker_symbol)
        self.info: Dict[str, Any] = {}
        self.hist: pd.DataFrame = pd.DataFrame()
        self.spy_hist: pd.DataFrame = pd.DataFrame()
        self.qqq_hist: pd.DataFrame = pd.DataFrame()
        self.sector_hist: Optional[pd.DataFrame] = None
        self.sector_etf: Optional[str] = None
        self.data_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self.scores = {
            "market_interest": 0,
            "relative_strength": 0,
            "technical": 0,
            "smart_money": 0,
            "fundamental": 0,
            "valuation_risk": 0,
        }
        self.scorecards: Dict[str, Any] = {}
        self.verdicts = {}
        self.peers_data: List[Dict] = []
        self.news_items: List[Dict] = []
        self.catalyst_score = 0
        self.news_available = False
        self._accum = 0
        self.extra: Dict[str, Any] = {}

    def fetch_data(self) -> bool:
        try:
            self.info = self.stock.info or {}
            if not self.info or (
                safe_get(self.info, "regularMarketPrice") is None
                and safe_get(self.info, "currentPrice") is None
            ):
                try:
                    fi = self.stock.fast_info
                    self.info["currentPrice"] = getattr(fi, "last_price", None)
                    self.info["marketCap"] = getattr(fi, "market_cap", None)
                except Exception:
                    pass

            self.hist = yf_history_retry(self.stock, period="2y", source=self.ticker_symbol)
            if self.hist.empty:
                print(f"No price data found for {self.ticker_symbol}")
                return False

            self.spy_hist = yf_history_retry("SPY", period="2y")
            self.qqq_hist = yf_history_retry("QQQ", period="2y")

            sector = safe_get(self.info, "sector", "")
            self.sector_etf = SECTOR_ETF.get(sector)
            if self.sector_etf:
                try:
                    self.sector_hist = yf_history_retry(self.sector_etf, period="2y")
                    if self.sector_hist.empty:
                        self.sector_hist = None
                except Exception:
                    self.sector_hist = None

            for step, fn in (
                ("extra", self._fetch_extra),
                ("fred", self._fetch_fred_macro),
                ("finnhub", self._fetch_finnhub),
                ("peers", self._fetch_peers),
            ):
                try:
                    fn()
                except Exception as e:
                    logger.warning("%s optional fetch %s failed: %s", self.ticker_symbol, step, e)
            if not self.news_items:
                try:
                    self._fetch_news()
                except Exception as e:
                    logger.warning("%s news fetch failed: %s", self.ticker_symbol, e)
            self.news_available = bool(self.news_items) or bool((self.extra or {}).get("fh_sentiment"))
            return True
        except Exception as e:
            logger.exception("Error fetching data for %s: %s", self.ticker_symbol, e)
            return False

    def _fetch_news(self):
        try:
            raw = self.stock.news or []
            items = []
            pos = neg = 0
            for item in raw[:8]:
                content = item.get("content") or item
                title = content.get("title") or item.get("title") or ""
                summary = content.get("summary") or content.get("description") or ""
                pub = content.get("pubDate") or content.get("providerPublishTime") or ""
                provider = ""
                if isinstance(content.get("provider"), dict):
                    provider = content["provider"].get("displayName", "")
                else:
                    provider = content.get("publisher") or ""
                label, p, n = headline_sentiment(title + " " + summary)
                pos += p
                neg += n
                items.append(
                    {
                        "title": title[:110],
                        "date": str(pub)[:10] if pub else "N/A",
                        "provider": provider,
                        "sentiment": label,
                    }
                )
            self.news_items = items
            if items and not (self.extra or {}).get("fh_sentiment"):
                self.catalyst_score = max(-3, min(3, pos - neg))
            elif not items and not (self.extra or {}).get("fh_sentiment"):
                self.catalyst_score = 0
        except Exception as e:
            logger.warning("Yahoo news failed: %s", e)
            if not self.news_items:
                self.news_items = []
            if not (self.extra or {}).get("fh_sentiment"):
                self.catalyst_score = 0

    def _get_peer_list(self) -> List[str]:
        fh = (self.extra or {}).get("fh_peers")
        if fh and isinstance(fh, list) and len(fh) > 0:
            return [p for p in fh if p and str(p).upper() != self.ticker_symbol][:5]
        if self.ticker_symbol in PEER_MAP:
            return [p for p in PEER_MAP[self.ticker_symbol] if p != self.ticker_symbol][:4]
        industry = safe_get(self.info, "industry", "")
        if industry in INDUSTRY_PEERS:
            return [p for p in INDUSTRY_PEERS[industry] if p != self.ticker_symbol][:4]
        return []

    def _fetch_peers(self):
        peers = self._get_peer_list()
        if not peers:
            self.peers_data = []
            return
        cache_key = f"peers_metrics:{','.join(peers)}"
        cached = cache_get(cache_key)
        if cached is not None:
            self.peers_data = cached
            return

        results = []
        hist_map = {}
        try:
            t0 = time.time()
            raw = yf.download(
                peers, period="1y", group_by="ticker",
                auto_adjust=True, progress=False, threads=True,
            )
            record_metric("yfinance_peers", (time.time() - t0) * 1000, ok=True)
            if raw is not None and not raw.empty:
                if len(peers) == 1:
                    hist_map[peers[0]] = raw
                else:
                    for p in peers:
                        try:
                            if p in raw.columns.get_level_values(0):
                                hist_map[p] = raw[p]
                        except Exception:
                            pass
        except Exception as e:
            logger.warning("Peer history batch failed: %s", e)
            record_metric("yfinance_peers", 0, ok=False)

        peer_failures = []
        for p in peers:
            info = {}
            for attempt in range(3):
                try:
                    t0 = time.time()
                    t = yf.Ticker(p)
                    info = t.info or {}
                    record_metric("yfinance_peer_info", (time.time() - t0) * 1000, ok=bool(info))
                    if info:
                        break
                except Exception as e:
                    record_metric("yfinance_peer_info", 0, ok=False)
                    logger.warning("Peer %s info attempt %s failed: %s", p, attempt + 1, e)
                    time.sleep(min(1.5 * (2 ** attempt), 6))
            if not info:
                peer_failures.append(p)
                continue
            try:
                t = yf.Ticker(p)
                price = safe_get(info, "currentPrice") or safe_get(info, "regularMarketPrice")
                hist = hist_map.get(p)
                if hist is None or (hasattr(hist, "empty") and hist.empty):
                    hist = t.history(period="6mo", auto_adjust=True)
                ret_3m = ret_1y = None
                if hist is not None and not hist.empty:
                    close = hist["Close"].dropna()
                    if len(close) > 63:
                        ret_3m = (close.iloc[-1] / close.iloc[-63] - 1) * 100
                    if len(close) > 200:
                        ret_1y = (close.iloc[-1] / close.iloc[0] - 1) * 100
                results.append(
                    {
                        "ticker": p,
                        "name": safe_get(info, "shortName") or p,
                        "price": price,
                        "forward_pe": safe_get(info, "forwardPE"),
                        "trailing_pe": safe_get(info, "trailingPE"),
                        "peg": safe_get(info, "pegRatio"),
                        "rev_growth": safe_get(info, "revenueGrowth"),
                        "profit_margin": safe_get(info, "profitMargins"),
                        "ret_3m": ret_3m,
                        "ret_1y": ret_1y,
                        "mktcap": safe_get(info, "marketCap"),
                    }
                )
            except Exception as e:
                peer_failures.append(p)
                logger.warning("Peer %s metrics failed: %s", p, e)
        self.peers_data = results
        if peer_failures:
            self.extra["peer_fetch_failures"] = peer_failures
        cache_set(cache_key, results)

    def _fetch_extra(self):
        info = self.info
        extra = {}
        target = safe_get(info, "targetMeanPrice")
        price = self.get_price()
        extra["target_mean"] = target
        extra["target_high"] = safe_get(info, "targetHighPrice")
        extra["target_low"] = safe_get(info, "targetLowPrice")
        extra["rec_mean"] = safe_get(info, "recommendationMean")
        extra["rec_key"] = safe_get(info, "recommendationKey")
        extra["n_analysts"] = safe_get(info, "numberOfAnalystOpinions")
        extra["upside_pct"] = (target / price - 1) * 100 if target and price and price > 0 else None

        extra["shares_short"] = safe_get(info, "sharesShort")
        extra["short_ratio"] = safe_get(info, "shortRatio")
        ss = safe_get(info, "sharesShort")
        fs = safe_get(info, "floatShares")
        spf_raw = safe_get(info, "shortPercentOfFloat")
        extra["short_pct_float"] = None
        extra["short_pct_float_display"] = None
        extra["short_pct_source"] = None
        if ss is not None and fs is not None and fs > 0:
            extra["short_pct_float"] = (float(ss) / float(fs)) * 100.0
            extra["short_pct_float_display"] = extra["short_pct_float"]
            extra["short_pct_source"] = "sharesShort/floatShares"
        elif spf_raw is not None:
            if 0 < float(spf_raw) <= 1.0:
                extra["short_pct_float"] = float(spf_raw) * 100.0
                extra["short_pct_source"] = "shortPercentOfFloat (fraction→%)"
            else:
                extra["short_pct_float"] = float(spf_raw)
                extra["short_pct_source"] = "shortPercentOfFloat (as-is)"
            extra["short_pct_float_display"] = extra["short_pct_float"]
        extra["float_shares"] = fs
        extra["short_date"] = safe_get(info, "dateShortInterest")

        high52 = safe_get(info, "fiftyTwoWeekHigh")
        low52 = safe_get(info, "fiftyTwoWeekLow")
        extra["high_52w"] = high52
        extra["low_52w"] = low52
        extra["pct_from_high"] = (price / high52 - 1) * 100 if price and high52 and high52 > 0 else None
        extra["pct_from_low"] = (price / low52 - 1) * 100 if price and low52 and low52 > 0 else None
        if high52 and low52 and high52 > low52 and price:
            extra["pct_in_52w_range"] = (price - low52) / (high52 - low52) * 100
        else:
            extra["pct_in_52w_range"] = None

        extra["earnings_date"] = extra["days_to_earnings"] = None
        extra["eps_avg"] = extra["eps_high"] = extra["eps_low"] = extra["rev_avg"] = None
        try:
            cal = self.stock.calendar
            cal_dict = None
            if isinstance(cal, dict):
                cal_dict = cal
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                cal_dict = cal[cal.columns[0]].to_dict()
            if cal_dict:
                ed = cal_dict.get("Earnings Date")
                if isinstance(ed, list) and ed:
                    ed = ed[0]
                if ed is not None:
                    if hasattr(ed, "date"):
                        ed = ed.date()
                    extra["earnings_date"] = str(ed)
                    try:
                        if isinstance(ed, dt_date):
                            extra["days_to_earnings"] = (ed - dt_date.today()).days
                    except Exception:
                        pass
                extra["eps_avg"] = cal_dict.get("Earnings Average")
                extra["eps_high"] = cal_dict.get("Earnings High")
                extra["eps_low"] = cal_dict.get("Earnings Low")
                extra["rev_avg"] = cal_dict.get("Revenue Average")
        except Exception:
            pass

        extra["insider_buy_shares"] = 0
        extra["insider_sell_shares"] = 0
        extra["insider_net"] = 0
        extra["insider_transactions"] = []
        try:
            ins = self.stock.insider_transactions
            if ins is not None and not ins.empty:
                cutoff = pd.Timestamp.now(tz="UTC").tz_localize(None) - pd.Timedelta(days=180)
                buy = sell = 0
                samples = []
                for _, row in ins.iterrows():
                    raw_date = row.get("Start Date")
                    try:
                        d = pd.to_datetime(raw_date)
                        d = strip_tz(d)
                        if d < cutoff:
                            continue
                    except Exception:
                        pass
                    text = str(row.get("Text", "") or "").lower()
                    shares = row.get("Shares") or 0
                    try:
                        shares = abs(float(shares))
                    except Exception:
                        shares = 0
                    tx = str(row.get("Transaction", "") or "").lower()
                    is_sale = "sale" in text or "sell" in tx
                    is_buy = any(k in text or k in tx for k in ("purchase", "buy", "bought", "acquisition"))
                    if is_sale:
                        sell += shares
                        side = "Sell"
                    elif is_buy:
                        buy += shares
                        side = "Buy"
                    else:
                        side = "Other"
                    if len(samples) < 5 and shares > 0 and side != "Other":
                        samples.append(
                            {
                                "side": side,
                                "shares": shares,
                                "insider": str(row.get("Insider", ""))[:30],
                                "date": str(raw_date)[:10],
                            }
                        )
                extra["insider_buy_shares"] = buy
                extra["insider_sell_shares"] = sell
                extra["insider_net"] = buy - sell
                extra["insider_transactions"] = samples
        except Exception as e:
            logger.warning("Insider parse failed: %s", e)

        extra["tnx"] = extra["dxy"] = None
        try:
            tnx = yf_history_retry("^TNX", period="5d", max_retries=2)
            if not tnx.empty:
                extra["tnx"] = float(tnx["Close"].iloc[-1])
        except Exception:
            pass
        try:
            dxy = yf_history_retry("DX-Y.NYB", period="5d", max_retries=2)
            if not dxy.empty:
                extra["dxy"] = float(dxy["Close"].iloc[-1])
            else:
                uup = yf_history_retry("UUP", period="5d", max_retries=2)
                if not uup.empty:
                    extra["dxy"] = float(uup["Close"].iloc[-1])
                    extra["dxy_note"] = "UUP proxy"
        except Exception:
            pass
        self.extra = extra

    def _fetch_fred_macro(self):
        key = FRED_API_KEY
        if not key:
            self.extra["fred_status"] = "no_key"
            return
        cached = cache_get("fred_macro_bundle")
        if cached is not None:
            self.extra.update(cached)
            return
        series_map = {
            "DGS10": "tnx_fred",
            "FEDFUNDS": "fed_funds",
            "CPIAUCSL": "cpi",
            "UNRATE": "unemployment",
            "T10Y2Y": "yield_curve",
            "VIXCLS": "vix_fred",
        }
        results = {}
        obs_dates = {}
        try:
            if FREDAPI_AVAILABLE:
                fred = Fred(api_key=key)
                for sid, name in series_map.items():
                    try:
                        t0 = time.time()
                        s = fred.get_series(sid)
                        record_metric("fred", (time.time() - t0) * 1000, ok=True)
                        if s is not None and len(s.dropna()) > 0:
                            clean = s.dropna()
                            results[name] = float(clean.iloc[-1])
                            obs_dates[name] = (
                                str(clean.index[-1].date())
                                if hasattr(clean.index[-1], "date")
                                else str(clean.index[-1])
                            )
                    except Exception as e:
                        logger.warning("FRED series %s failed: %s", sid, e)
                        record_metric("fred", 0, ok=False)
                        results[name] = None
            else:
                for sid, name in series_map.items():
                    data = request_with_retry(
                        "https://api.stlouisfed.org/fred/series/observations",
                        params={
                            "series_id": sid,
                            "api_key": key,
                            "file_type": "json",
                            "sort_order": "desc",
                            "limit": 5,
                        },
                        source="fred",
                    )
                    if not data or data.get("_error"):
                        results[name] = None
                        continue
                    results[name] = None
                    for o in data.get("observations", []):
                        if o.get("value") not in (".", None, ""):
                            results[name] = float(o["value"])
                            obs_dates[name] = o.get("date")
                            break
            if results.get("tnx_fred") is not None:
                self.extra["tnx"] = results["tnx_fred"]
            if results.get("vix_fred") is not None:
                self.extra["vix"] = results["vix_fred"]
            self.extra["fed_funds"] = results.get("fed_funds")
            self.extra["cpi"] = results.get("cpi")
            self.extra["unemployment"] = results.get("unemployment")
            self.extra["yield_curve"] = results.get("yield_curve")
            self.extra["fred_obs_dates"] = obs_dates
            success_count = sum(1 for v in results.values() if v is not None)
            total = len(series_map)
            if success_count == 0:
                self.extra["fred_status"] = "failed"
            elif success_count < total:
                self.extra["fred_status"] = f"partial ({success_count}/{total})"
            else:
                self.extra["fred_status"] = "ok"
            self.extra["fred_series"] = results
            bundle = {
                k: self.extra.get(k)
                for k in (
                    "tnx", "vix", "fed_funds", "cpi", "unemployment", "yield_curve",
                    "fred_status", "fred_series", "fred_obs_dates",
                )
            }
            cache_set("fred_macro_bundle", bundle)
        except Exception as e:
            logger.exception("FRED macro failed")
            self.extra["fred_status"] = f"error: {e}"

    def _fetch_finnhub(self):
        key = FINNHUB_API_KEY
        if not key:
            self.extra["finnhub_status"] = "no_key"
            return
        symbol = self.ticker_symbol
        fh = None
        if FINNHUB_LIB_AVAILABLE:
            try:
                fh = finnhub.Client(api_key=key)
            except Exception:
                fh = None

        def fh_get(path, params=None):
            params = dict(params or {})
            params["token"] = key
            data = request_with_retry(
                f"https://finnhub.io/api/v1/{path}", params=params, source="finnhub"
            )
            if not data:
                self.extra["_fh_calls"] = self.extra.get("_fh_calls", 0) + 1
                return None
            if isinstance(data, dict) and data.get("_error"):
                self.extra["_fh_calls"] = self.extra.get("_fh_calls", 0) + 1
                err = str(data.get("_error"))
                if "AUTH" in err or data.get("status") in (401, 403):
                    self.extra["_fh_auth_error"] = True
                return None
            self.extra["_fh_ok"] = self.extra.get("_fh_ok", 0) + 1
            return data

        try:
            to_d = dt_date.today()
            from_d = to_d - timedelta(days=14)
            news = None
            if fh:
                try:
                    news = fh.company_news(symbol, _from=from_d.isoformat(), to=to_d.isoformat())
                except Exception:
                    news = None
            if not news:
                news = fh_get(
                    "company-news",
                    {"symbol": symbol, "from": from_d.isoformat(), "to": to_d.isoformat()},
                )
            if news and isinstance(news, list):
                fh_items = []
                pos = neg = 0
                for n in news[:10]:
                    title = n.get("headline") or n.get("title") or ""
                    summary = n.get("summary") or ""
                    ts = n.get("datetime")
                    if ts:
                        try:
                            dt_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                        except Exception:
                            dt_str = str(ts)[:10]
                    else:
                        dt_str = "N/A"
                    label, p, ncount = headline_sentiment(title + " " + summary)
                    pos += p
                    neg += ncount
                    fh_items.append(
                        {
                            "title": title[:110],
                            "date": dt_str,
                            "provider": n.get("source") or "Finnhub",
                            "sentiment": label,
                        }
                    )
                if fh_items:
                    self.news_items = fh_items
                    self.catalyst_score = max(-3, min(3, pos - neg))

            sent = None
            if fh:
                try:
                    sent = fh.news_sentiment(symbol)
                except Exception:
                    sent = None
            if not sent:
                sent = fh_get("news-sentiment", {"symbol": symbol})
            if sent and isinstance(sent, dict):
                self.extra["fh_sentiment"] = {
                    "buzz": sent.get("buzz"),
                    "company_news_score": sent.get("companyNewsScore"),
                    "sector_avg": sent.get("sectorAverageNewsScore"),
                    "bullish_pct": (sent.get("sentiment") or {}).get("bullishPercent"),
                    "bearish_pct": (sent.get("sentiment") or {}).get("bearishPercent"),
                }

            earns = None
            if fh:
                try:
                    earns = fh.company_earnings(symbol, limit=4)
                except Exception:
                    earns = None
            if not earns:
                earns = fh_get("stock/earnings", {"symbol": symbol, "limit": 4})
            if earns and isinstance(earns, list) and len(earns) > 0:
                last = earns[0]
                self.extra["fh_last_eps_actual"] = last.get("actual")
                self.extra["fh_last_eps_estimate"] = last.get("estimate")
                self.extra["fh_last_eps_surprise"] = last.get("surprise")
                self.extra["fh_last_eps_surprise_pct"] = last.get("surprisePercent")
                self.extra["fh_last_eps_period"] = last.get("period")
                beats = 0
                for e in earns[:4]:
                    if e.get("actual") is not None and e.get("estimate") is not None:
                        if e["actual"] >= e["estimate"]:
                            beats += 1
                self.extra["fh_eps_beat_count"] = beats

            pt = None
            if fh:
                try:
                    pt = fh.price_target(symbol)
                except Exception:
                    pt = None
            if not pt:
                pt = fh_get("stock/price-target", {"symbol": symbol})
            if pt and isinstance(pt, dict):
                if pt.get("targetMean") and not self.extra.get("target_mean"):
                    self.extra["target_mean"] = pt.get("targetMean")
                if pt.get("targetHigh"):
                    self.extra["target_high"] = pt.get("targetHigh")
                if pt.get("targetLow"):
                    self.extra["target_low"] = pt.get("targetLow")
                price = self.get_price()
                tm = self.extra.get("target_mean")
                if tm and price and price > 0:
                    self.extra["upside_pct"] = (tm / price - 1) * 100
                self.extra["fh_price_target"] = pt

            peers = None
            if fh:
                try:
                    peers = fh.company_peers(symbol)
                except Exception:
                    peers = None
            if not peers:
                peers = fh_get("stock/peers", {"symbol": symbol})
            if peers and isinstance(peers, list) and len(peers) > 0:
                self.extra["fh_peers"] = [p for p in peers if p and p.upper() != symbol][:5]

            if self.extra.get("_fh_auth_error") and not self.extra.get("_fh_ok"):
                self.extra["finnhub_status"] = "auth_error"
            elif self.extra.get("_fh_ok"):
                self.extra["finnhub_status"] = "ok"
            else:
                self.extra["finnhub_status"] = "failed"
        except Exception as e:
            self.extra["finnhub_status"] = f"error: {e}"

    def last_bar_date(self) -> Optional[str]:
        if self.hist.empty:
            return None
        try:
            return str(pd.Timestamp(self.hist.index[-1]).date())
        except Exception:
            return str(self.hist.index[-1])[:10]

    def get_live_quote(self) -> Optional[float]:
        return safe_get(self.info, "currentPrice") or safe_get(self.info, "regularMarketPrice")

    def get_price(self) -> Optional[float]:
        """Use last complete bar close so price, MAs and RSI share one session."""
        if not self.hist.empty and "Close" in self.hist.columns:
            c = self.hist["Close"].dropna()
            if len(c):
                return float(c.iloc[-1])
        return self.get_live_quote()

    def compute_mas(self) -> Dict[str, Optional[float]]:
        if self.hist.empty:
            return {}
        close = self.hist["Close"]
        return {
            "MA20": close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None,
            "MA50": close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None,
            "MA100": close.rolling(100).mean().iloc[-1] if len(close) >= 100 else None,
            "MA200": close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None,
        }

    def compute_technicals(self) -> Dict[str, Any]:
        if self.hist.empty or len(self.hist) < 30:
            return {}
        close = self.hist["Close"]
        high = self.hist["High"]
        low = self.hist["Low"]
        volume = self.hist["Volume"]
        result = {}
        if TA_AVAILABLE:
            rsi = RSIIndicator(close=close, window=14)
            result["RSI"] = rsi.rsi().iloc[-1]
            macd_ind = MACD(close=close)
            result["MACD"] = macd_ind.macd().iloc[-1]
            result["MACD_signal"] = macd_ind.macd_signal().iloc[-1]
            result["MACD_hist"] = macd_ind.macd_diff().iloc[-1]
            atr = AverageTrueRange(high=high, low=low, close=close, window=14)
            result["ATR"] = atr.average_true_range().iloc[-1]
            try:
                adx = ADXIndicator(high=high, low=low, close=close, window=14)
                result["ADX"] = adx.adx().iloc[-1]
            except Exception:
                result["ADX"] = None
            bb = BollingerBands(close=close, window=20, window_dev=2)
            result["BB_upper"] = bb.bollinger_hband().iloc[-1]
            result["BB_lower"] = bb.bollinger_lband().iloc[-1]
            result["BB_mid"] = bb.bollinger_mavg().iloc[-1]
            obv = OnBalanceVolumeIndicator(close=close, volume=volume)
            result["OBV"] = obv.on_balance_volume().iloc[-1]
            adi = AccDistIndexIndicator(high=high, low=low, close=close, volume=volume)
            result["ADI"] = adi.acc_dist_index().iloc[-1]
        else:
            delta = close.diff()
            gain = delta.clip(lower=0.0)
            loss = (-delta).clip(lower=0.0)
            avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
            rs = avg_gain / avg_loss.replace(0, float("nan"))
            rsi_last = rs.iloc[-1]
            result["RSI"] = float(100 - (100 / (1 + rsi_last))) if rsi_last == rsi_last else 50
            prev_c = close.shift(1)
            tr = pd.concat([(high - low), (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
            result["ATR"] = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else None
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            result["MACD"] = float(macd.iloc[-1])
            result["MACD_signal"] = float(signal.iloc[-1])
            result["MACD_hist"] = float((macd - signal).iloc[-1])
        result["vol_avg_20"] = volume.rolling(20).mean().iloc[-1]
        result["vol_avg_50"] = volume.rolling(50).mean().iloc[-1] if len(volume) >= 50 else None
        result["last_vol"] = volume.iloc[-1]
        return result

    def relative_performance(self, periods=(21, 63, 126, 252)) -> Dict[str, Optional[float]]:
        if self.hist.empty or self.spy_hist.empty:
            return {}
        res = {}
        labels = {21: "1M", 63: "3M", 126: "6M", 252: "12M"}
        for p in periods:
            if len(self.hist) < p or len(self.spy_hist) < p:
                res[labels[p]] = None
                continue
            stock_ret = (self.hist["Close"].iloc[-1] / self.hist["Close"].iloc[-p] - 1) * 100
            spy_ret = (self.spy_hist["Close"].iloc[-1] / self.spy_hist["Close"].iloc[-p] - 1) * 100
            res[labels[p]] = stock_ret - spy_ret
        return res

    def sector_relative(self) -> Dict[str, Optional[float]]:
        if self.sector_hist is None or self.sector_hist.empty or self.hist.empty:
            return {}
        res = {}
        for p, label in [(21, "1M"), (63, "3M"), (126, "6M")]:
            if len(self.hist) < p or len(self.sector_hist) < p:
                res[label] = None
                continue
            stock_ret = (self.hist["Close"].iloc[-1] / self.hist["Close"].iloc[-p] - 1) * 100
            sec_ret = (self.sector_hist["Close"].iloc[-1] / self.sector_hist["Close"].iloc[-p] - 1) * 100
            res[label] = stock_ret - sec_ret
        return res

    def analyze_sector_etf(self) -> Dict[str, Any]:
        out = {"etf": self.sector_etf, "available": False}
        if self.sector_hist is None or self.sector_hist.empty:
            return out
        out["available"] = True
        close = self.sector_hist["Close"]
        vol = self.sector_hist["Volume"]
        price = float(close.iloc[-1])
        out["price"] = price
        out["MA20"] = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
        out["MA50"] = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
        out["MA200"] = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
        for p, label in [(21, "ret_1M"), (63, "ret_3M"), (126, "ret_6M"), (252, "ret_12M")]:
            out[label] = (close.iloc[-1] / close.iloc[-p] - 1) * 100 if len(close) >= p else None
        if not self.spy_hist.empty:
            for p, label in [(21, "vs_spy_1M"), (63, "vs_spy_3M"), (126, "vs_spy_6M")]:
                if len(close) >= p and len(self.spy_hist) >= p:
                    sec_r = (close.iloc[-1] / close.iloc[-p] - 1) * 100
                    spy_r = (self.spy_hist["Close"].iloc[-1] / self.spy_hist["Close"].iloc[-p] - 1) * 100
                    out[label] = sec_r - spy_r
                else:
                    out[label] = None
        above_ma50 = out["MA50"] is not None and price > out["MA50"]
        above_ma200 = out["MA200"] is not None and price > out["MA200"]
        ma50_above_ma200 = (
            out["MA50"] is not None and out["MA200"] is not None and out["MA50"] > out["MA200"]
        )
        if above_ma50 and above_ma200 and ma50_above_ma200:
            out["trend"] = "🟢 Strong Uptrend"
        elif above_ma50 and above_ma200:
            out["trend"] = "🟢 Uptrend"
        elif above_ma200:
            out["trend"] = "🟡 Above long-term but mixed"
        elif not above_ma200 and not above_ma50:
            out["trend"] = "🔴 Downtrend"
        else:
            out["trend"] = "🟡 Mixed / Transition"
        out["vol_vs_20d"] = float(vol.iloc[-1] / vol.rolling(20).mean().iloc[-1]) if len(vol) >= 20 else None
        sec_rs = self.sector_relative()
        out["stock_vs_sector_1M"] = sec_rs.get("1M")
        out["stock_vs_sector_3M"] = sec_rs.get("3M")
        out["stock_vs_sector_6M"] = sec_rs.get("6M")
        vs3 = out.get("vs_spy_3M")
        if vs3 is not None:
            if vs3 > 5:
                out["rotation"] = "🟢 Strong Rotation into Sector"
            elif vs3 > 0:
                out["rotation"] = "🟢 Accumulating / Mild Outperformance"
            elif vs3 > -5:
                out["rotation"] = "🟡 Neutral"
            else:
                out["rotation"] = "🔴 Capital flowing out of Sector"
        else:
            out["rotation"] = "⚪ N/A"
        return out

    def market_regime(self) -> Tuple[str, str]:
        if self.spy_hist.empty:
            return "🟡 Neutral", "Data unavailable"
        close = self.spy_hist["Close"]
        ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
        ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
        price = close.iloc[-1]
        qqq_close = self.qqq_hist["Close"] if not self.qqq_hist.empty else None
        qqq_ma50 = (
            qqq_close.rolling(50).mean().iloc[-1]
            if qqq_close is not None and len(qqq_close) >= 50
            else None
        )
        try:
            vix = yf_history_retry("^VIX", period="5d", max_retries=2)
            vix_last = float(vix["Close"].iloc[-1]) if not vix.empty else None
        except Exception:
            vix_last = None
        notes = []
        score = 0
        if ma50 and price > ma50:
            score += 1
            notes.append("SPY > MA50")
        else:
            notes.append("SPY ≤ MA50")
        if ma200 and price > ma200:
            score += 1
            notes.append("SPY > MA200")
        else:
            notes.append("SPY ≤ MA200")
        if qqq_ma50 and qqq_close is not None and qqq_close.iloc[-1] > qqq_ma50:
            score += 1
            notes.append("QQQ > MA50")
        if vix_last is not None:
            notes.append(f"VIX={vix_last:.1f}")
            if vix_last < 18:
                score += 1
            elif vix_last > 25:
                score -= 1
        if score >= 3:
            regime = "🟢 Risk-On"
        elif score <= 0:
            regime = "🔴 Risk-Off"
        else:
            regime = "🟡 Neutral"
        return regime, " | ".join(notes)

    def analyze_fundamental(self) -> Dict[str, Any]:
        info = self.info
        raw_de = safe_get(info, "debtToEquity")
        res = {
            "revenue_growth": safe_get(info, "revenueGrowth"),
            "earnings_growth": safe_get(info, "earningsGrowth"),
            "earnings_quarterly_growth": safe_get(info, "earningsQuarterlyGrowth"),
            "gross_margins": safe_get(info, "grossMargins"),
            "operating_margins": safe_get(info, "operatingMargins"),
            "profit_margins": safe_get(info, "profitMargins"),
            "roe": safe_get(info, "returnOnEquity"),
            "roa": safe_get(info, "returnOnAssets"),
            "debt_to_equity": raw_de,
            "debt_to_equity_norm": None,
            "current_ratio": safe_get(info, "currentRatio"),
            "free_cashflow": safe_get(info, "freeCashflow"),
            "operating_cashflow": safe_get(info, "operatingCashflow"),
            "total_cash": safe_get(info, "totalCash"),
            "total_debt": safe_get(info, "totalDebt"),
            "forward_eps": safe_get(info, "forwardEps"),
            "trailing_eps": safe_get(info, "trailingEps"),
            "peg": safe_get(info, "pegRatio"),
            "forward_pe": safe_get(info, "forwardPE"),
            "trailing_pe": safe_get(info, "trailingPE"),
            "ev_ebitda": safe_get(info, "enterpriseToEbitda"),
            "price_to_sales": safe_get(info, "priceToSalesTrailing12Months"),
            "price_to_book": safe_get(info, "priceToBook"),
        }
        mcap = safe_get(info, "marketCap")
        fcf = res["free_cashflow"]
        res["fcf_yield"] = (fcf / mcap) * 100 if mcap and fcf and mcap > 0 else None
        res["debt_to_equity_norm"] = normalize_debt_to_equity(
            raw_de, res.get("total_cash"), res.get("total_debt")
        )
        return res

    def _accumulation_proxy(self, price, tech) -> int:
        sm = 0
        has = False
        if tech.get("last_vol") and tech.get("vol_avg_20"):
            has = True
            vol_ratio = tech["last_vol"] / tech["vol_avg_20"]
            if (
                vol_ratio > 1.5
                and price
                and len(self.hist) > 5
                and self.hist["Close"].iloc[-1] > self.hist["Close"].iloc[-5]
            ):
                sm += 5
            elif vol_ratio < 0.6 and price and self.hist["Close"].iloc[-1] < self.hist["Close"].iloc[-5]:
                sm += 2
            else:
                sm += 2
        inst = safe_get(self.info, "heldPercentInstitutions")
        if inst is not None:
            has = True
            if inst > 0.7:
                sm += 2
            elif inst > 0.4:
                sm += 1
        if not has:
            self.verdicts["smart_money"] = "⚪ Unknown"
            return 0
        sm = score_clamp(sm, 0, 15)
        self.verdicts["smart_money"] = (
            "🟢 Volume-supported up move" if sm >= 11
            else ("🟡 Neutral / mixed proxies" if sm >= 6 else "🔴 Weak flow proxies")
        )
        return sm

    def swing_levels(self) -> Tuple[Optional[float], Optional[float]]:
        if self.hist.empty or len(self.hist) < 20:
            return None, None
        window = self.hist.iloc[-61:-1] if len(self.hist) >= 61 else self.hist.iloc[:-1]
        if window.empty:
            return None, None
        return float(window["Low"].min()), float(window["High"].max())

    def score_sections(self):
        price = self.get_price()
        mas = self.compute_mas()
        tech = self.compute_technicals()
        rs = self.relative_performance()
        sec_rs = self.sector_relative()
        fund = self.analyze_fundamental()
        regime, regime_note = self.market_regime()
        sec_detail = self.analyze_sector_etf()

        fh_sent = self.extra.get("fh_sentiment") or {}
        if fh_sent.get("bullish_pct") is not None and fh_sent.get("bearish_pct") is not None:
            bull = float(fh_sent["bullish_pct"])
            bear = float(fh_sent["bearish_pct"])
            self.catalyst_score = int(round(max(-3, min(3, (bull - bear) * 6))))
            self.news_available = True

        accum = self._accumulation_proxy(price, tech)
        self._accum = accum
        q = quality_score(fund)
        m = momentum_score(
            rs, sec_detail, price, mas, tech, self.catalyst_score, accum,
            news_available=self.news_available,
        )
        v = valuation_score(fund, self.peers_data, safe_get(self.info, "sector", "") or "")
        rk = risk_score(fund, self.info, self.extra, tech, price, mas, regime)
        dq = data_quality_score(
            price, self.hist, self.spy_hist, fund, self.news_items, self.extra, self.peers_data, tech
        )
        comp = research_composite(q["score"], m["score"], v["score"], rk["score"])
        labels = dual_labels(q["score"], m["score"])

        self.scorecards = {
            "quality": q,
            "momentum": m,
            "valuation": v,
            "risk": rk,
            "data_quality": dq,
            "composite": comp,
            "labels": labels,
            "profile": investment_profile(q["score"], m["score"], v["score"], rk["score"]),
        }
        self.extra["data_quality"] = dq["components"]
        self.extra["data_quality_overall"] = dq["overall"]
        self.extra["data_sources"] = dq["sources"]

        buckets = legacy_bucket_scores(q["score"], m["score"], v["score"])
        self.scores = {k: buckets[k] for k in self.scores}
        self.verdicts["market_interest"] = "🟢" if self.scores["market_interest"] >= 14 else (
            "🟡" if self.scores["market_interest"] >= 8 else "🔴"
        )
        self.verdicts["rs"] = m["rs_verdict"]
        self.verdicts["tech"] = m["tech_verdict"]
        self.verdicts["fundamental"] = q["verdict"]
        self.verdicts["valuation"] = v["verdict"]

        self._price = price
        self._mas = mas
        self._tech = tech
        self._rs = rs
        self._sec_rs = sec_rs
        self._fund = fund
        self._regime = regime
        self._regime_note = regime_note
        self._stage = m["stage"]
        self._sector_detail = sec_detail

    def total_score(self) -> int:
        comp = (self.scorecards or {}).get("composite") or {}
        if comp.get("score") is not None:
            return int(comp["score"])
        return int(round(sum(self.scores.values())))

    def classification(self, score: int, data_quality: float, quality: int, momentum: int) -> str:
        if data_quality < DQ_GATE:
            return "⚪ DATA INSUFFICIENT"
        if score >= 85 and quality >= 70:
            return "FILTER CANDIDATE (research only)"
        if score >= 75 and (quality >= 60 or momentum >= 70):
            return "FILTER EMERGING (research only)"
        if score >= 65:
            return "FILTER POTENTIAL (research only)"
        if score >= 55:
            return "WATCH"
        return "FILTER FAIL"

    def final_signal_bundle(self, score: int, data_quality: float) -> Dict[str, str]:
        price = self._price
        mas = self._mas
        rsi = (self._tech or {}).get("RSI")
        overextended = bool(rsi and rsi > 75)
        if price and mas.get("MA20") and price > mas["MA20"] * 1.12:
            overextended = True
        if data_quality < DQ_GATE:
            return {
                "signal": "⚪ NO SIGNAL",
                "setup": "DATA_INSUFFICIENT",
                "entry": "DO NOT TRADE — incomplete data",
                "confidence": "LOW",
                "risk": "UNKNOWN",
            }
        if score >= 75:
            signal = "🟢 BULLISH"
        elif score >= 55:
            signal = "🟡 MIXED / NEUTRAL"
        else:
            signal = "🔴 BEARISH / WEAK"
        tech_v = self.verdicts.get("tech", "")
        if overextended:
            setup = "OVEREXTENDED — wait pullback"
        elif "Uptrend" in tech_v and score >= 70:
            setup = "TREND / BREAKOUT watch"
        elif "Base" in tech_v:
            setup = "BASE / CONSOLIDATION"
        elif "Downtrend" in tech_v:
            setup = "DOWNTREND — avoid longs"
        else:
            setup = "UNDEFINED"
        if score >= 80 and not overextended and data_quality >= 70:
            entry = "RESEARCH ONLY — possible accumulate zone (not auto-buy)"
        elif score >= 70:
            entry = "WAIT — confirm breakout or pullback with volume"
        elif score >= 55:
            entry = "WATCHLIST only"
        else:
            entry = "AVOID long bias"
        conf = "HIGH" if data_quality >= 85 and score >= 75 else (
            "MEDIUM" if data_quality >= 70 else "LOW"
        )
        if overextended or score < 50:
            risk = "HIGH"
        elif score < 70 or data_quality < 70:
            risk = "MEDIUM"
        else:
            risk = "LOW"
        dte = (self.extra or {}).get("days_to_earnings")
        if dte is not None and 0 <= dte <= 10:
            risk = "HIGH"
        return {
            "signal": signal,
            "setup": setup,
            "entry": entry,
            "confidence": conf,
            "risk": risk,
        }

    def _entry_zone(self, bundle: Dict[str, str], support, resistance) -> str:
        price = self._price
        mas = self._mas
        setup = bundle.get("setup", "")
        atr = (self._tech or {}).get("ATR")
        if "BREAKOUT" in setup and resistance:
            lo = resistance
            hi = resistance + (atr or resistance * 0.01)
            return f"{fmt(lo)} – {fmt(hi)} (breakout band)"
        if "OVEREXTENDED" in setup or "PULLBACK" in setup.upper() or "TREND" in setup:
            anchors = [x for x in (mas.get("MA20"), mas.get("MA50")) if x]
            if anchors:
                lo, hi = min(anchors), max(anchors)
                return f"{fmt(lo)} – {fmt(hi)} (MA pullback zone)"
        if "BASE" in setup and resistance and support:
            return f"{fmt(support)} – {fmt(resistance)} (base range; trigger above prior swing high)"
        if mas.get("MA20") and mas.get("MA50"):
            return f"{fmt(min(mas['MA20'], mas['MA50']))} – {fmt(max(mas['MA20'], mas['MA50']))}"
        return "N/A"


    def to_payload(self) -> dict:
        from payload import build_scan_payload
        return build_scan_payload(self)

    def generate_report(self) -> str:
        payload = self.to_payload()
        return render_decision_report(payload, self)


def _f(v, d=2):
    if v is None:
        return "N/A"
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v, d=1):
    if v is None:
        return "N/A"
    try:
        return f"{float(v)*100:+.{d}f}%"
    except (TypeError, ValueError):
        return "N/A"


def render_decision_report(payload: dict, scanner=None) -> str:
    d = payload.get("decision") or {}
    sc = payload.get("scorecards") or {}
    px = payload.get("price") or {}
    lv = payload.get("levels") or {}
    fd = payload.get("fundamentals") or {}
    mk = payload.get("market") or {}
    ev = payload.get("event") or {}
    dq = payload.get("data_quality") or {}
    meta = payload.get("meta") or {}
    gates = d.get("gates") or {}
    labels = d.get("gate_labels") or {}
    lines = []
    lines.append(f"SCAN — {payload.get('ticker')}  (v{payload.get('revision')})")
    lines.append(f"{meta.get('name') or ''} | {meta.get('sector') or 'N/A'} | {meta.get('industry') or 'N/A'}")
    lines.append(f"Generated UTC: {payload.get('generated_at_utc')}")
    lines.append(f"Regime: {mk.get('regime')} ({mk.get('regime_note')})")
    lines.append("")
    lines.append("DECISION")
    lines.append(f"Stance     {d.get('stance')}")
    lines.append(f"Summary    {d.get('summary')}")
    lines.append("Buy signal NO")
    lines.append(
        "Gates      "
        f"Data Quality={labels.get('data_quality', 'Fail')} | "
        f"Business={labels.get('business', 'Fail')} | "
        f"Uptrend={labels.get('uptrend', 'No')} | "
        f"Stretched={labels.get('stretched', 'No')} | "
        f"Expensive={labels.get('expensive', 'No')} | "
        f"Earnings window={labels.get('earnings_window', 'No')}"
    )
    lines.append(
        "Gate note  Data Quality = ข้อมูลครบพอใช้ด่าน | "
        "Uptrend = ราคาเหนือค่าเฉลี่ย 50 และค่าเฉลี่ย 50 เหนือ 200 | "
        "Stretched = Yes เมื่อ RSI>=72 หรือราคาสูงกว่าค่าเฉลี่ย 20 เกิน 8% / Approaching เมื่อเข้าโซน RSI>=68 หรือ +5% | "
        "Expensive = Yes เมื่อคะแนนมูลค่า<38 / Approaching เมื่อ<45 | "
        "Earnings window = เหลือไม่เกิน 10 วันถึงวันงบ"
    )
    if d.get("flags"):
        lines.append("Flags      " + "; ".join(d["flags"]))
    lines.append("")
    lines.append("USE")
    for x in (d.get("emphasize") or [])[:6]:
        lines.append(f"• {x}")
    lines.append("DO NOT USE")
    for x in (d.get("ignore") or [])[:6]:
        lines.append(f"• {x}")
    lines.append("")
    lines.append("SCORECARDS (research labels only)")
    lines.append(f"Quality {sc.get('quality')}/100  {sc.get('quality_verdict')}")
    lines.append(f"Timing  {sc.get('momentum')}/100  {sc.get('momentum_rs')} | {sc.get('momentum_tech')} | {sc.get('stage')}")
    lines.append(f"Value   {sc.get('valuation')}/100  {sc.get('valuation_verdict')}")
    lines.append(f"Risk    {sc.get('risk')}/100  {sc.get('risk_verdict')}")
    lines.append(f"Data quality {sc.get('data_quality')}/100")
    lines.append(f"Business/Price labels: {sc.get('business_label')} | {sc.get('price_label')}")
    if sc.get("composite_research_only") is not None:
        lines.append(f"Composite research-only: {sc.get('composite_research_only')}/100 (not a trade score)")
    lines.append("")
    lines.append("PRICE / LEVELS")
    lines.append(
        f"As-of {px.get('as_of') or 'N/A'} | Last {_f(px.get('last'))} | Live {_f(px.get('live_quote'))}"
    )
    lines.append(f"MA20 {_f(px.get('ma20'))} | MA50 {_f(px.get('ma50'))} | MA200 {_f(px.get('ma200'))}")
    lines.append(f"RSI {_f(px.get('rsi14'),1)} | ATR {_f(px.get('atr14'))} | Vol {_f(px.get('volume'),0)}")
    rs = mk.get("rs_vs_spy") or {}
    lines.append(f"RS vs SPY  1M {_f(rs.get('1M'),1)} | 3M {_f(rs.get('3M'),1)} | 6M {_f(rs.get('6M'),1)} | 12M {_f(rs.get('12M'),1)}")
    lines.append(f"Swing 60  low {_f(px.get('swing_low_60'))} / high {_f(px.get('swing_high_60'))}")
    lines.append(f"Stop to use {_f(lv.get('trade_stop'))}  ({lv.get('trade_stop_reason') or lv.get('note')})")
    lines.append(f"Do not use as stop/target: swing high, swing low if tight, analyst target")
    lines.append("")
    lines.append("BUSINESS SNAPSHOT (Yahoo snapshot, not point-in-time)")
    lines.append(f"Rev growth {_pct(fd.get('revenue_growth'))} | EPS growth {_pct(fd.get('eps_growth_used'))}")
    lines.append(f"ROIC proxy {_pct(fd.get('roic_proxy'))} | ROE {_pct(fd.get('roe'))} | ROA {_pct(fd.get('roa'))}")
    lines.append(
        f"Margins G/Op/Net {_pct(fd.get('gross_margin'))} / {_pct(fd.get('operating_margin'))} / {_pct(fd.get('profit_margin'))}"
    )
    lines.append(f"FCF {_f(fd.get('fcf'),0)} | OCF {_f(fd.get('ocf'),0)} | FCF yield {_f(fd.get('fcf_yield'),1)}%")
    lines.append(f"D/E {_f(fd.get('debt_to_equity_pct'),1)}% | Cash {_f(fd.get('cash'),0)} / Debt {_f(fd.get('debt'),0)}")
    lines.append(f"Fwd P/E {_f(fd.get('forward_pe'),1)} | PEG {_f(fd.get('peg'),2)} | EV/EBITDA {_f(fd.get('ev_ebitda'),1)}")
    lines.append("")
    lines.append("EVENT")
    lines.append(f"Earnings {ev.get('earnings_date')} | days={ev.get('days_to_earnings')}")
    lines.append(f"Short % float {_f(ev.get('short_pct_float'),2)}")
    lines.append("Analyst target omitted — not used for entry or stop")
    news = payload.get("news") or {}
    lines.append(f"News available={news.get('available')} catalyst={news.get('catalyst_score')} — {news.get('note')}")
    for n in (news.get("items") or [])[:4]:
        lines.append(f"  [{n.get('date')}] {n.get('sentiment')} {n.get('title')}")
    lines.append("")
    lines.append("RESEARCH DETAIL")
    lines.append("ภาคผนวกนี้ไล่ที่มาของด่าน ไม่ใช่คำสั่งซื้อ")
    cards = getattr(scanner, "scorecards", {}) or {} if scanner is not None else {}
    q = cards.get("quality") or {}
    if q.get("detail"):
        lines.append("Quality points")
        for row in q["detail"][:12]:
            lines.append(f"  - {row}")
    mo = cards.get("momentum") or {}
    if mo.get("components"):
        c = mo["components"]
        lines.append(
            f"Timing parts  RS {c.get('rs')} | sector {c.get('sector')} | trend {c.get('trend')} | volume {c.get('volume')} | catalyst {c.get('catalyst')}"
        )
    val = cards.get("valuation") or {}
    if val.get("detail"):
        lines.append("Value points")
        for row in val["detail"][:10]:
            lines.append(f"  - {row}")
    rk = cards.get("risk") or {}
    if rk.get("flags"):
        lines.append("Risk flags  " + "; ".join(map(str, rk["flags"])))
    sec = mk.get("sector") or {}
    if sec:
        lines.append(
            f"Sector ETF {mk.get('sector_etf') or 'N/A'} | "
            f"trend {sec.get('trend') or 'N/A'} | rotation {sec.get('rotation') or 'N/A'}"
        )
        if sec.get("stock_vs_sector_3M") is not None:
            lines.append(f"Stock vs sector 3M {_f(sec.get('stock_vs_sector_3M'),1)}%")
    hi, lo, last = px.get("high_52w"), px.get("low_52w"), px.get("last")
    dd = "N/A"
    try:
        if hi and last:
            dd = f"{(float(last)/float(hi)-1)*100:.1f}%"
    except Exception:
        pass
    lines.append(f"52w high {_f(hi)} | 52w low {_f(lo)} | vs 52w high {dd}")
    lines.append("Buy signal ถูกปิดในโค้ดทั้งก้อน ดู Stance ไม่ดูคำว่าซื้อ")
    lines.append("")
    lines.append("DATA SOURCES")
    for k, val in (dq.get("sources") or {}).items():
        lines.append(f"• {k}: {val}")
    lines.append("")
    lines.append(payload.get("disclaimer") or "")
    return "\n".join(lines)



def main():
    import argparse
    import json
    from pathlib import Path
    from config import REVISION

    parser = argparse.ArgumentParser(description="Market Winner Scanner — research filter")
    parser.add_argument("ticker")
    parser.add_argument("--json", action="store_true", help="Print dashboard JSON")
    parser.add_argument("--out", type=str, default="", help="Write JSON to this path")
    args = parser.parse_args()

    print(f"Scanning {args.ticker.upper()} (v{REVISION}) ...\n")
    scanner = MarketWinnerScanner(args.ticker)
    if not scanner.fetch_data():
        sys.exit(1)
    payload = scanner.to_payload()
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"Wrote {args.out}")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(scanner.generate_report())


if __name__ == "__main__":
    main()
