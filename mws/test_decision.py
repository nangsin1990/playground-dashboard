# -*- coding: utf-8 -*-
try:
    from .decision import evaluate_decision
except ImportError:
    from decision import evaluate_decision


def _base(**kw):
    args = dict(
        dq=80,
        quality=75,
        momentum=70,
        valuation=50,
        risk=30,
        tech_verdict="🟢 Uptrend",
        stage="Stage 2 — Uptrend",
        rs_verdict="🟢 Leader",
        avg_rs=6.0,
        price=100.0,
        mas={"MA20": 98.0, "MA50": 94.0, "MA200": 80.0},
        rsi=55.0,
        regime="🟢 Risk-On",
        days_to_earnings=40,
        fcf=1e9,
        de_norm=60.0,
    )
    args.update(kw)
    return evaluate_decision(**args)


def test_never_buy_signal():
    assert _base()["is_buy_signal"] is False
    assert _base(quality=90, momentum=90)["is_buy_signal"] is False


def test_dq_gate():
    d = _base(dq=40)
    assert d["gates"]["data_quality_pass"] is False
    assert d["stance"] == "INSUFFICIENT"
    assert d["stance"] != "CANDIDATE"


def test_missing_fundamentals_price_only():
    d = _base(quality=0, valuation=0, quality_available=False, valuation_available=False)
    assert d["stance"] == "PRICE_ONLY"
    assert d["gates"]["business_skip"] is False
    assert d["gates"]["valuation_extreme"] is False


def test_downtrend_avoid():
    d = _base(tech_verdict="🔴 Downtrend", stage="Stage 4 — Downtrend")
    assert d["stance"] == "AVOID_LONG"


def test_price_only_when_quality_weak():
    d = _base(quality=40, momentum=80)
    assert d["stance"] == "PRICE_ONLY"


def test_stretched_waits():
    d = _base(price=120, mas={"MA20": 100.0, "MA50": 94.0, "MA200": 80.0}, rsi=60)
    assert d["stance"] == "WAIT_PULLBACK"
    assert d["gates"]["timing_stretched"] is True


def test_rsi_73_waits():
    d = _base(rsi=73.3, valuation=50)
    assert d["stance"] == "WAIT_PULLBACK"


def test_candidate_when_both_pass():
    d = _base()
    assert d["stance"] == "CANDIDATE"


def test_extreme_value_blocks_candidate():
    d = _base(valuation=26, rsi=55)
    assert d["stance"] == "WATCH_EXPENSIVE"
    assert d["gates"]["valuation_extreme"] is True



def test_correction_not_called_base():
    d = _base(tech_verdict="🟡 Correction", stage="Correction — holding 200-day", rsi=35, valuation=55)
    assert d["stance"] == "WATCH_CORRECTION"
    assert d["gates"]["timing_correction"] is True
    assert d["gates"]["timing_uptrend"] is False

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

