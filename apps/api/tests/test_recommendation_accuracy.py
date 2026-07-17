from types import SimpleNamespace

from app.config import get_settings, refresh_settings
from app.services import prompt_tuning, recommendation_accuracy, signal_guard_policy


def test_forward_accuracy_is_experimental_and_ineligible_for_tuning(monkeypatch):
    reports = [
        {
            "id": "report-new",
            "created_at": "2026-01-02T14:30:00+08:00",
            "analysis_facts": {
                "portfolio": {"decision_style": "tactical"},
                "session": {
                    "calendar_date": "2026-01-02",
                    "session_kind": "trading_day_pre_close",
                },
            },
            "fund_recommendations": [
                {"fund_code": "000001", "fund_name": "样本", "action": "分批加仓"}
            ],
        },
    ]
    monkeypatch.setattr(recommendation_accuracy, "list_reports", lambda: reports)

    result = recommendation_accuracy.build_recommendation_accuracy(
        limit_reports=30,
        fetch_nav=lambda *_args, **_kwargs: {
                "data": [
                    {"date": "2026-01-02", "nav": 1.0},
                    {"date": "2026-01-03", "nav": 1.1},
                    {"date": "2026-01-04", "nav": 1.1},
                    {"date": "2026-01-05", "nav": 1.1},
                    {"date": "2026-01-06", "nav": 1.1},
                    {"date": "2026-01-07", "nav": 1.1},
                ]
            },
        trade_dates=frozenset({"2026-01-02", "2026-01-03"}),
    )

    assert result["metric_status"] == "forward_total_return_v2"
    assert result["is_experimental"] is True
    assert result["auto_tuning_eligible"] is False
    assert "不进入自动调参" in result["warning"]
    assert result["eligible_count"] == 0
    assert result["mature_count"] == 0
    assert result["formal_v2_report_count"] == 0
    assert result["legacy_reference"]["eligible_count"] == 1
    assert result["legacy_reference"]["mature_count"] == 1
    assert (
        result["legacy_reference"]["by_horizon"]["T+5"]["hit_rate_percent"]
        == 100.0
    )


def test_only_audited_persisted_v2_events_enter_formal_accuracy(monkeypatch):
    report = {
        "id": "formal-report",
        "created_at": "2026-01-02T14:30:00+08:00",
        "decision_contract": {
            "persistence": "persisted",
            "audit_eligible": True,
            "store_authority": "primary",
        },
        "decision_events": [
            {
                "schema_version": "decision_event.v2",
                "event_id": "daily:formal-report:0:000001",
                "recommendation_index": 0,
                "fund_code": "000001",
                    "evaluation_class": "bullish",
                    "metric_eligible": True,
                    "horizons": [1, 5, 20],
                "executable_calendar_date": "2026-01-02",
                "fee_policy": {
                    "status": "available",
                    "fee_source": "user_assumption",
                    "round_trip_fee_percent": 1.0,
                    "fee_calculation": "initial_principal_haircut",
                },
                "benchmark": {"tier": "unavailable", "status": "unavailable"},
            }
        ],
        "analysis_facts": {
            "portfolio": {"decision_style": "tactical"},
            "session": {
                "calendar_date": "2026-01-02",
                "session_kind": "trading_day_pre_close",
            },
        },
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "样本", "action": "分批加仓"}
        ],
    }
    monkeypatch.setattr(recommendation_accuracy, "list_reports", lambda: [report])

    result = recommendation_accuracy.build_recommendation_accuracy(
        limit_reports=30,
        horizons=(1,),
        fetch_nav=lambda *_args, **_kwargs: {
            "data": [
                {"date": "2026-01-02", "nav": 1.0},
                {"date": "2026-01-03", "nav": 1.02},
            ]
        },
        trade_dates=frozenset({"2026-01-02", "2026-01-03"}),
        fetch_benchmark=None,
    )

    assert result["formal_v2_report_count"] == 1
    assert result["eligible_count"] == 1
    assert result["mature_count"] == 1
    assert result["metrics"]["gross_direction"]["hit_rate_percent"] == 100.0
    assert result["metrics"]["positive_net_return"]["coverage_percent"] == 100.0
    assert result["legacy_reference"]["recommendation_count"] == 0


def test_disabled_prompt_tuning_does_not_feed_experimental_accuracy_into_guard(monkeypatch):
    settings = SimpleNamespace(
        tactical_prompt_tuning_enabled=False,
        tactical_prompt_tuning_lookback_reports=30,
        sector_signal_backtest_days=120,
        sector_signal_backtest_min_triggers=10,
    )
    monkeypatch.setattr(signal_guard_policy, "get_settings", lambda: settings)
    monkeypatch.setattr(
        signal_guard_policy,
        "resolve_accuracy_tuning",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled experimental accuracy tuning must not be loaded")
        ),
    )
    monkeypatch.setattr(
        signal_guard_policy,
        "build_signal_backtest_context",
        lambda *_args, **_kwargs: {"by_rule": {}, "has_data": False},
    )

    policy = signal_guard_policy.resolve_signal_guard_policy(sector_labels=[])

    assert policy["tighten_tactical"] is False
    assert policy["hints"] == []
    assert policy["stats"]["accuracy"] == {"disabled": True}


def test_explicitly_enabled_prompt_tuning_still_rejects_ineligible_accuracy(monkeypatch):
    """即使显式打开环境开关，legacy 指标也不能绕过评价器 eligibility 硬门。"""
    monkeypatch.setenv("FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED", "true")
    refresh_settings()
    assert get_settings().tactical_prompt_tuning_enabled is True
    monkeypatch.setattr(
        prompt_tuning,
        "build_recommendation_accuracy",
        lambda **_kwargs: {
            "auto_tuning_eligible": False,
            "metric_status": "legacy_experimental",
            "warning": "旧口径不可用于自动调参。",
            "paired_days": 12,
            "by_style": {
                "tactical": {
                    "reversal": {
                        "up_then_down_count": 8,
                        "up_then_down_aggressive_miss": 8,
                    }
                }
            },
        },
    )
    monkeypatch.setattr(
        signal_guard_policy,
        "build_signal_backtest_context",
        lambda *_args, **_kwargs: {"by_rule": {}, "has_data": False},
    )

    policy = signal_guard_policy.resolve_signal_guard_policy(sector_labels=[])

    assert policy["tighten_tactical"] is False
    assert policy["hints"] == []
    assert policy["stats"]["accuracy"]["disabled"] is True
    assert policy["stats"]["accuracy"]["disabled_reason"] == "accuracy_not_eligible"

    # 避免 get_settings 的进程缓存把显式 true 泄漏到同一 pytest 进程的后续用例。
    monkeypatch.setenv("FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED", "false")
    refresh_settings()
