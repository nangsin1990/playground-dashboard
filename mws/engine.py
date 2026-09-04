# -*- coding: utf-8 -*-
"""
Stable entry for dashboard / notebooks / CLI.

    from engine import run_scan, run_radar, dumps
    payload = run_scan("AAPL")
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from accumulation_radar import AccumulationRadar
from market_winner_scanner import MarketWinnerScanner
from payload import build_radar_payload, build_scan_payload, json_safe


def run_scan(ticker: str) -> Dict[str, Any]:
    scanner = MarketWinnerScanner(ticker)
    if not scanner.fetch_data():
        return json_safe(
            {
                "schema": "mws.scan.v1",
                "ticker": ticker.upper().strip(),
                "ok": False,
                "error": "fetch_failed",
                "decision": {"stance": "INSUFFICIENT", "is_buy_signal": False},
            }
        )
    payload = build_scan_payload(scanner)
    payload["ok"] = True
    return payload


def run_radar(ticker: str, universe: Optional[List[str]] = None) -> Dict[str, Any]:
    radar = AccumulationRadar(ticker, universe=universe or [])
    if not radar.fetch():
        return json_safe(
            {
                "schema": "mws.radar.v1",
                "ticker": ticker.upper().strip(),
                "ok": False,
                "error": "fetch_failed",
            }
        )
    payload = build_radar_payload(radar)
    payload["ok"] = True
    return payload


def dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
