from __future__ import annotations

from typing import Literal

Direction = Literal["up", "down", "flat"]
Prediction = Literal["down_or_flat", "up", "down"]

REVERSAL_DOWN_PREV_MIN = 1.0
REVERSAL_DOWN_CUR_MAX = -0.8
SECTOR_WEAK_MAX = -2.0
INTRADAY_PULLBACK_MIN_HIGH = 2.0
INTRADAY_PULLBACK_MIN_PULLBACK = 1.0
FLAT_THRESHOLD = 0.3

SIGNAL_RULE_IDS = (
    "reversal_down",
    "sector_weak",
    "intraday_pullback",
    "baseline_momentum",
)


def is_reversal_down(prev_change: float, cur_change: float) -> bool:
    return prev_change >= REVERSAL_DOWN_PREV_MIN and cur_change <= REVERSAL_DOWN_CUR_MAX


def is_sector_weak(cur_change: float) -> bool:
    return cur_change <= SECTOR_WEAK_MAX


def is_intraday_pullback_proxy(
    cur_change: float,
    high_change: float | None,
) -> bool:
    if high_change is None:
        return False
    if high_change < INTRADAY_PULLBACK_MIN_HIGH:
        return False
    return (high_change - cur_change) >= INTRADAY_PULLBACK_MIN_PULLBACK


def classify_direction(change: float) -> Direction:
    if abs(change) < FLAT_THRESHOLD:
        return "flat"
    return "up" if change > 0 else "down"


def predict_for_rule(rule_id: str, *, prev_change: float, cur_change: float, high_change: float | None) -> Prediction | None:
    if rule_id == "reversal_down":
        return "down_or_flat" if is_reversal_down(prev_change, cur_change) else None
    if rule_id == "sector_weak":
        return "down_or_flat" if is_sector_weak(cur_change) else None
    if rule_id == "intraday_pullback":
        return "down_or_flat" if is_intraday_pullback_proxy(cur_change, high_change) else None
    if rule_id == "baseline_momentum":
        direction = classify_direction(cur_change)
        if direction == "flat":
            return None
        return "up" if direction == "up" else "down"
    return None


def prediction_matches(prediction: Prediction, actual_change: float) -> bool:
    actual = classify_direction(actual_change)
    if prediction == "down_or_flat":
        return actual in {"down", "flat"}
    if prediction == "up":
        return actual == "up"
    if prediction == "down":
        return actual == "down"
    return False


def rule_label(rule_id: str) -> str:
    labels = {
        "reversal_down": "涨后回吐（T-1涨≥1%且T跌≤-0.8%）",
        "sector_weak": "板块弱势（T日跌≤-2%）",
        "intraday_pullback": "冲高回落代理（日高≥2%且回落≥1%）",
        "baseline_momentum": "基准：延续T日方向",
    }
    return labels.get(rule_id, rule_id)
