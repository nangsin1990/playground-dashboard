# -*- coding: utf-8 -*-
"""One structural stop. MWS v3.8.0"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _num(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def pick_levels(
    price: Optional[float],
    mas: Dict[str, Any],
    atr: Optional[float],
    swing_low: Optional[float] = None,
    swing_high: Optional[float] = None,
) -> Dict[str, Any]:
    """
    One stop to use.
    Below MA50 but above MA200 -> use MA200.
    Above MA50 -> use the higher of MA50 and ATR(2x) that is still below price.
    Swing high after a deep drop is not a target.
    """
    px = _num(price)
    ma20 = _num(mas.get("MA20"))
    ma50 = _num(mas.get("MA50"))
    ma200 = _num(mas.get("MA200"))
    sl = _num(swing_low)
    sh = _num(swing_high)
    atr_n = _num(atr)
    atr_stop = (px - 2.0 * atr_n) if (px and atr_n) else None

    reason = "insufficient"
    trade_stop = None
    if px and ma200 and px > ma200 and ma50 and px < ma50:
        trade_stop = ma200
        reason = "below MA50, use MA200 as the only structural stop"
    elif px and ma50 and px > ma50:
        trade_stop = ma50
        reason = "above MA50, use MA50 as the only structural stop"
    elif px and ma200 and px <= ma200:
        trade_stop = sl if (sl and sl < px) else (atr_stop if atr_stop and atr_stop < px else ma200)
        reason = "at or below MA200, structure already broken"
    else:
        trade_stop = atr_stop
        reason = "ATR only"

    ignore = [
        "อย่าใช้สวิงสูงเป็นเป้าหลังย่อแรง",
        "อย่าใช้เป้านักวิเคราะห์เป็นจุดเข้าหรือจุดตัด",
        "อย่าวางจุดตัดชิดสวิงต่ำถ้าห่างจากราคาไม่ถึง 1 ATR",
    ]
    near_swing = sl is not None and px is not None and atr_n is not None and (px - sl) < atr_n
    if near_swing:
        ignore.append("สวิงต่ำชิดราคาเกินไป ไม่ใช้เป็นจุดตัด")

    target = None
    target_note = "ไม่มีเป้าจากระบบนี้"
    if px and sh and ma50 and px > ma50 and sh > px * 1.04:
        target = sh
        target_note = "สวิงสูงใช้ได้เฉพาะตอนราคาอยู่เหนือค่าเฉลี่ย 50"
    return {
        "trade_stop": trade_stop,
        "trade_stop_reason": reason,
        "thesis_invalidation": ma200,
        "atr_stop": atr_stop,
        "swing_stop": sl,
        "swing_high": sh,
        "breakout": target,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "use": "trade_stop",
        "target_note": target_note,
        "ignore": ignore,
        "note": f"ใช้จุดเดียว: {reason}",
    }
