from app.services.sector_signal_rules import (
    classify_direction,
    is_intraday_pullback_proxy,
    is_reversal_down,
    is_sector_weak,
    predict_for_rule,
    prediction_matches,
)


def test_reversal_down_detection():
    assert is_reversal_down(1.5, -1.0) is True
    assert is_reversal_down(0.5, -1.0) is False
    assert is_reversal_down(1.5, -0.5) is False


def test_sector_weak_detection():
    assert is_sector_weak(-2.1) is True
    assert is_sector_weak(-1.5) is False


def test_intraday_pullback_proxy():
    assert is_intraday_pullback_proxy(1.0, 3.0) is True
    assert is_intraday_pullback_proxy(2.5, 2.8) is False


def test_prediction_matches_down_or_flat():
    assert prediction_matches("down_or_flat", -0.1) is True
    assert prediction_matches("down_or_flat", 0.5) is False
    assert prediction_matches("down_or_flat", 0.0) is True


def test_predict_for_rule_baseline_momentum():
    assert predict_for_rule("baseline_momentum", prev_change=0, cur_change=1.2, high_change=None) == "up"
    assert predict_for_rule("baseline_momentum", prev_change=0, cur_change=0.1, high_change=None) is None


def test_classify_direction_flat_band():
    assert classify_direction(0.2) == "flat"
    assert classify_direction(0.4) == "up"
