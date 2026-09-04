from levels import pick_levels
from decision import evaluate_decision, stance_allows_entry


def test_correction_uses_ma200_only():
    lv = pick_levels(438.0, {"MA20": 498.0, "MA50": 541.0, "MA200": 404.0}, 20.0, 436.0, 739.0)
    assert abs(lv["trade_stop"] - 404.0) < 1e-9
    assert lv["breakout"] is None


def test_uptrend_prefers_ma50_if_above_atr():
    lv = pick_levels(328.0, {"MA20": 312.0, "MA50": 313.0, "MA200": 283.0}, 6.7, 274.0, 344.0)
    assert lv["trade_stop"] == 313.0


def test_rsi_zone_is_not_candidate():
    d = evaluate_decision(
        dq=90, quality=77, momentum=60, valuation=55, risk=20,
        tech_verdict="🟢 Uptrend", stage="Stage 2 — Uptrend",
        rs_verdict="🟡 Neutral", avg_rs=2, price=100, mas={"MA20": 96, "MA50": 94, "MA200": 80},
        rsi=69, regime="🟢 Risk-On", days_to_earnings=40, fcf=1, de_norm=40,
    )
    assert d["stance"] == "WAIT_PULLBACK"
    assert d["gate_labels"]["stretched"] == "Approaching"
    assert stance_allows_entry(d["stance"]) is False


def test_value_zone_is_not_candidate():
    d = evaluate_decision(
        dq=90, quality=77, momentum=60, valuation=42, risk=20,
        tech_verdict="🟢 Uptrend", stage="Stage 2 — Uptrend",
        rs_verdict="🟡 Neutral", avg_rs=2, price=100, mas={"MA20": 96, "MA50": 94, "MA200": 80},
        rsi=55, regime="🟢 Risk-On", days_to_earnings=40, fcf=1, de_norm=40,
    )
    assert d["stance"] == "WATCH_EXPENSIVE"
    assert d["gate_labels"]["expensive"] == "Approaching"


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
