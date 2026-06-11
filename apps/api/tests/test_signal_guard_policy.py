from app.models import Holding
from app.services.signal_guard_policy import resolve_signal_guard_policy


def _fake_backtest(*_args, **_kwargs):
    return {
        "has_data": True,
        "summary_lines": ["测试摘要"],
        "by_rule": {
            "reversal_down": {
                "trigger_count": 20,
                "hit_rate_percent": 60.0,
                "hit_count": 12,
            },
            "intraday_pullback": {
                "trigger_count": 15,
                "hit_rate_percent": 45.0,
                "hit_count": 7,
            },
        },
    }


def test_guard_policy_tightens_on_high_reversal_hit_rate(monkeypatch):
    monkeypatch.setattr(
        "app.services.signal_guard_policy.resolve_accuracy_tuning",
        lambda **kwargs: {"tighten_tactical": False, "hints": [], "reason": None, "stats": {}},
    )
    monkeypatch.setattr(
        "app.services.signal_guard_policy.build_signal_backtest_context",
        _fake_backtest,
    )
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="测试",
            holding_amount=5000,
            sector_name="半导体",
        )
    ]
    policy = resolve_signal_guard_policy(holdings, backtest_days=120)
    assert policy["enforce_reversal_block"] is True
    assert policy["tighten_tactical"] is True
    assert policy["enforce_pullback_block"] is False


def test_guard_policy_loosens_low_reversal_hit_rate(monkeypatch):
    def low_hit_backtest(*_args, **_kwargs):
        return {
            "has_data": True,
            "summary_lines": [],
            "by_rule": {
                "reversal_down": {
                    "trigger_count": 20,
                    "hit_rate_percent": 48.0,
                    "hit_count": 10,
                }
            },
        }

    monkeypatch.setattr(
        "app.services.signal_guard_policy.resolve_accuracy_tuning",
        lambda **kwargs: {"tighten_tactical": False, "hints": [], "reason": None, "stats": {}},
    )
    monkeypatch.setattr(
        "app.services.signal_guard_policy.build_signal_backtest_context",
        low_hit_backtest,
    )
    policy = resolve_signal_guard_policy([], backtest_days=120)
    assert policy["enforce_reversal_block"] is False
