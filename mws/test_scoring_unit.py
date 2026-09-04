# -*- coding: utf-8 -*-
"""Local unit checks that do not need market data."""

from scoring import (
    catalyst_to_points,
    estimate_roic_proxy,
    quality_score,
    research_composite,
    dual_labels,
    data_quality_score,
)
from utils import normalize_debt_to_equity, strip_tz
import pandas as pd


def test_catalyst_no_news_is_zero():
    assert catalyst_to_points(0, news_available=False) == 0
    assert catalyst_to_points(3, news_available=False) == 0
    assert catalyst_to_points(0, news_available=True) == 5
    assert catalyst_to_points(3, news_available=True) == 10
    assert catalyst_to_points(-3, news_available=True) == 0


def test_de_normalize():
    assert normalize_debt_to_equity(1.47) == 147.0
    assert abs(normalize_debt_to_equity(147.3) - 147.3) < 1e-9
    assert normalize_debt_to_equity(None) is None


def test_composite_penalizes_risk():
    low_risk = research_composite(80, 80, 60, 20)["score"]
    high_risk = research_composite(80, 80, 60, 80)["score"]
    assert low_risk > high_risk


def test_dual_labels_split():
    lab = dual_labels(40, 90)
    assert lab["price"] == "PRICE LEADER+"
    assert lab["business"] == "WEAK BUSINESS"


def test_roic_is_proxy_blend():
    v = estimate_roic_proxy({"roe": 0.18, "roa": 0.10, "debt_to_equity": 50})
    assert abs(v - (0.6 * 0.18 + 0.4 * 0.10)) < 1e-9


def test_roic_rejects_buyback_roe():
    v = estimate_roic_proxy({"roe": 1.48, "roa": 0.27, "debt_to_equity": 78})
    assert abs(v - 0.27) < 1e-9


def test_quality_uses_zero_growth():
    q = quality_score({
        "revenue_growth": 0.0,
        "earnings_growth": 0.0,
        "roe": 0.15,
        "roa": 0.08,
        "operating_margins": 0.12,
        "gross_margins": 0.40,
        "free_cashflow": 1e9,
        "operating_cashflow": 1.2e9,
        "debt_to_equity": 60,
        "current_ratio": 1.4,
        "total_cash": 5e9,
        "total_debt": 4e9,
    })
    assert q["available"] is True
    assert q["score"] > 0


def test_dq_technical_partial_without_atr():
    dq = data_quality_score(
        price=10,
        hist=pd.DataFrame({"Close": range(60)}),
        spy_hist=pd.DataFrame({"Close": range(60)}),
        fund={},
        news_items=[],
        extra={},
        peers=[],
        tech={"RSI": 55},
    )
    assert dq["components"]["technical"] == 0.4


def test_strip_tz_naive_and_aware():
    naive = pd.DatetimeIndex(["2026-01-02", "2026-01-03"])
    assert strip_tz(naive).tz is None
    aware = pd.DatetimeIndex(["2026-01-02", "2026-01-03"], tz="America/New_York")
    out = strip_tz(aware)
    assert out.tz is None
    assert len(out) == 2



def test_recent_rs_laggard_not_leader():
    from scoring import momentum_score
    out = momentum_score(
        {"1M": -17.0, "3M": -14.0, "6M": 14.0, "12M": 160.0},
        {},
        438.0,
        {"MA20": 498.0, "MA50": 541.0, "MA200": 404.0},
        {"RSI": 33.0},
        0,
        5,
        False,
    )
    assert out["rs_verdict"] == "🔴 Laggard"
    assert "Correction" in out["stage"]


def test_net_cash_de_not_ratio():
    from utils import normalize_debt_to_equity
    v = normalize_debt_to_equity(6.33, cash=26e9, debt=6.4e9)
    assert abs(v - 6.33) < 1e-9


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", fn.__name__, e)
    if failed:
        raise SystemExit(1)
    print(f"OK {len(tests)} tests")

