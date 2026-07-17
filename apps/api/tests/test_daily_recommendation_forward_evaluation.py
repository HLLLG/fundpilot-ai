from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from app.services import recommendation_accuracy
from app.services.recommendation_outcomes import build_recommendation_outcomes


_TRADE_DATES = frozenset(f"2026-01-{day:02d}" for day in range(2, 31))


def _report(
    report_id: str,
    created_at: str,
    recommendations: list[dict[str, Any]],
    *,
    style: str = "tactical",
    session_kind: str = "trading_day_pre_close",
) -> dict[str, Any]:
    return {
        "id": report_id,
        "created_at": created_at,
        "fund_recommendations": recommendations,
        "analysis_facts": {
            "portfolio": {"decision_style": style},
            "session": {
                "calendar_date": created_at[:10],
                "session_kind": session_kind,
            },
        },
    }


def _nav_payload(values: list[float], *, start_day: int = 2) -> dict[str, Any]:
    return {
        "data": [
            {"date": f"2026-01-{start_day + index:02d}", "nav": value}
            for index, value in enumerate(values)
        ]
    }


def test_forward_outcome_uses_exact_trading_horizons_and_excludes_observation_from_hits():
    report = _report(
        "r1",
        "2026-01-02T14:40:00+08:00",
        [
            {"fund_code": "000001", "fund_name": "上涨基金", "action": "分批加仓"},
            {"fund_code": "000002", "fund_name": "下跌基金", "action": "减仓评估"},
            {"fund_code": "000003", "fund_name": "观察基金", "action": "观察"},
        ],
    )
    payloads = {
        "000001": _nav_payload([1.00, 1.01, 1.02, 1.03, 1.04, 1.05]),
        "000002": _nav_payload([1.00, 0.99, 0.98, 0.97, 0.96, 0.95]),
        "000003": _nav_payload([1.00, 1.50, 1.60, 1.70, 1.80, 1.90]),
    }

    result = build_recommendation_outcomes(
        report,
        None,
        horizons=(1, 5, 20),
        fetch_nav=lambda code, **_kwargs: payloads[code],
        trade_dates=_TRADE_DATES,
    )

    assert result["metric_status"] == "forward_total_return_v2"
    assert result["event_contract"]["persistence"] == "dynamic_not_persisted"
    assert result["recommendation_count"] == 3
    assert result["eligible_count"] == 2
    assert result["observation_count"] == 1

    t1 = result["by_horizon"]["T+1"]
    assert t1["horizon_trading_days"] == 1
    assert t1["eligible_count"] == 2
    assert t1["mature_count"] == 2
    assert t1["skipped_count"] == 0
    assert t1["immature_count"] == 0
    assert t1["data_unavailable_count"] == 0
    assert t1["hit_count"] == 2
    assert t1["miss_count"] == 0
    assert t1["hit_rate_percent"] == 100.0
    assert t1["coverage_percent"] == 100.0
    assert result["by_horizon"]["T+5"]["mature_count"] == 2
    assert result["by_horizon"]["T+20"]["mature_count"] == 0
    assert result["by_horizon"]["T+20"]["coverage_percent"] == 0.0

    observed = next(item for item in result["items"] if item["fund_code"] == "000003")
    assert observed["decision_event"]["schema_version"] == "decision_event.v1"
    assert observed["decision_event"]["event_id"] == "daily:r1:2:000003"
    assert observed["decision_event"]["decision_at"] == "2026-01-02T14:40:00+08:00"
    assert observed["evaluation_class"] == "observation"
    assert observed["by_horizon"]["T+1"]["direction_hit"] is None
    assert observed["by_horizon"]["T+1"]["status"] == "observation"
    observation = observed["by_horizon"]["T+1"]["outcome_observation"]
    assert observation["schema_version"] == "outcome_observation.v1"
    assert observation["observation_id"] == "daily:r1:2:000003:T+1"
    assert observation["observation_at"] == "2026-01-03"


def test_forward_outcome_uses_total_return_path_and_no_action_counterfactual() -> None:
    report = _report(
        "total-return",
        "2026-01-02T14:40:00+08:00",
        [
            {
                "fund_code": "000001",
                "fund_name": "分红样本",
                "action": "分批加仓",
                "suggested_position_change_percent": 20,
                "suggested_position_change_basis": "相对当前持仓",
            }
        ],
    )
    report["analysis_facts"]["portfolio"]["round_trip_fee_percent"] = 1.0
    payload = {
        "data": [
            {"date": "2026-01-02", "nav": 1.0},
            # 单位净值除息 10%，官方日增长率为 0%，不能当成 -10% 损失。
            {"date": "2026-01-03", "nav": 0.9, "daily_growth": 0.0},
            {"date": "2026-01-04", "nav": 0.909, "daily_growth": 1.0},
            {"date": "2026-01-05", "nav": 0.91809, "daily_growth": 1.0},
            {"date": "2026-01-06", "nav": 0.9272709, "daily_growth": 1.0},
            {"date": "2026-01-07", "nav": 0.936543609, "daily_growth": 1.0},
        ]
    }

    result = build_recommendation_outcomes(
        report,
        None,
        horizons=(5,),
        fetch_nav=lambda *_args, **_kwargs: payload,
        trade_dates=_TRADE_DATES,
    )

    outcome = result["items"][0]["by_horizon"]["T+5"]
    assert outcome["return_percent"] == pytest.approx(4.0604)
    assert outcome["path_metrics"]["max_adverse_excursion_percent"] == 0.0
    assert outcome["no_action_counterfactual"]["available"] is True
    assert outcome["no_action_counterfactual"]["incremental_value_add_percent"] == pytest.approx(
        3.0604
    )
    observation = outcome["outcome_observation"]
    assert observation["path_metrics"] == outcome["path_metrics"]
    assert observation["no_action_counterfactual"] == outcome["no_action_counterfactual"]


def test_old_frozen_event_never_gains_an_unregistered_t60_outcome() -> None:
    report = _report(
        "legacy-v4",
        "2026-01-02T14:40:00+08:00",
        [{"fund_code": "000001", "fund_name": "旧事件", "action": "分批加仓"}],
    )
    report["decision_contract"] = {
        "persistence": "persisted",
        "audit_eligible": True,
    }
    report["decision_events"] = [
        {
            "schema_version": "decision_event.v2",
            "event_id": "daily:legacy-v4:0:000001",
            "recommendation_index": 0,
            "fund_code": "000001",
            "evaluation_class": "bullish",
            "metric_eligible": True,
            "horizons": [1, 5, 20],
            "executable_calendar_date": "2026-01-02",
            "fee_policy": {},
            "benchmark": {"tier": "unavailable", "status": "unavailable"},
        }
    ]
    payload = {
        "data": [
            {
                "date": (date(2026, 3, 1) + timedelta(days=index)).isoformat(),
                "nav": 1.0 + index / 100.0,
            }
            for index in range(61)
        ]
    }
    report["decision_events"][0]["executable_calendar_date"] = "2026-03-01"

    result = build_recommendation_outcomes(
        report,
        None,
        horizons=(5, 20, 60),
        fetch_nav=lambda *_args, **_kwargs: payload,
        trade_dates=_TRADE_DATES,
        formal_v2_only=True,
    )

    item = result["items"][0]
    assert item["by_horizon"]["T+5"]["status"] == "mature"
    assert item["by_horizon"]["T+60"]["status"] == "not_registered"
    assert result["by_horizon"]["T+60"]["eligible_count"] == 0
    assert result["by_horizon"]["T+60"]["mature_count"] == 0


def test_forward_outcome_does_not_substitute_latest_nav_for_immature_horizon():
    report = _report(
        "r1",
        "2026-01-02T14:00:00+08:00",
        [{"fund_code": "000001", "fund_name": "样本", "action": "分批加仓"}],
    )

    result = build_recommendation_outcomes(
        report,
        None,
        horizons=(1, 5),
        fetch_nav=lambda *_args, **_kwargs: _nav_payload([1.0, 1.1, 1.2, 1.3, 1.4]),
        trade_dates=_TRADE_DATES,
    )

    item = result["items"][0]
    assert item["by_horizon"]["T+1"]["status"] == "mature"
    assert item["by_horizon"]["T+1"]["target_nav_date"] == "2026-01-03"
    t5 = item["by_horizon"]["T+5"]
    assert t5["status"] == "immature"
    assert t5["maturity_status"] == "immature"
    assert t5["horizon_trading_days"] == 5
    # Fund-specific valuation calendars (especially QDII) cannot safely predict
    # the future T+N date from the A-share calendar.
    assert t5["target_nav_date"] is None
    assert t5["available_forward_trading_days"] == 4
    assert t5["direction_hit"] is None


def test_after_close_report_uses_strictly_later_nav_as_execution_baseline():
    report = _report(
        "r1",
        "2026-01-02T15:30:00+08:00",
        [{"fund_code": "000001", "fund_name": "样本", "action": "分批加仓"}],
        session_kind="trading_day_after_close",
    )

    result = build_recommendation_outcomes(
        report,
        None,
        horizons=(1,),
        fetch_nav=lambda *_args, **_kwargs: _nav_payload([1.0, 2.0, 3.0]),
        trade_dates=_TRADE_DATES,
    )

    item = result["items"][0]
    assert item["baseline_nav_date"] == "2026-01-03"
    assert item["by_horizon"]["T+1"]["target_nav_date"] == "2026-01-04"
    assert item["by_horizon"]["T+1"]["return_percent"] == 50.0


def test_horizon_uses_next_fund_valuation_row_instead_of_a_share_calendar_date():
    report = _report(
        "r1",
        "2026-01-02T14:00:00+08:00",
        [{"fund_code": "000001", "fund_name": "样本", "action": "分批加仓"}],
    )
    payload = {
        "data": [
            {"date": "2026-01-02", "nav": 1.0},
            # T+1 (2026-01-03 in the injected calendar) is deliberately absent.
            {"date": "2026-01-04", "nav": 2.0},
        ]
    }

    result = build_recommendation_outcomes(
        report,
        None,
        horizons=(1,),
        fetch_nav=lambda *_args, **_kwargs: payload,
        trade_dates=_TRADE_DATES,
    )

    outcome = result["items"][0]["by_horizon"]["T+1"]
    assert outcome["target_nav_date"] == "2026-01-04"
    assert outcome["status"] == "mature"
    assert outcome["direction_hit"] is True
    assert result["by_horizon"]["T+1"]["mature_count"] == 1
    assert result["by_horizon"]["T+1"]["skipped_count"] == 0
    assert result["by_horizon"]["T+1"]["data_unavailable_count"] == 0


def test_accuracy_deduplicates_same_day_to_latest_report_and_counts_recommendations(monkeypatch):
    reports = [
        _report(
            "newer",
            "2026-01-02T14:50:00+08:00",
            [
                {"fund_code": "000001", "fund_name": "A", "action": "分批加仓"},
                {"fund_code": "000002", "fund_name": "B", "action": "分批加仓"},
                {"fund_code": "000003", "fund_name": "C", "action": "分批加仓"},
            ],
        ),
        _report(
            "older-same-day",
            "2026-01-02T10:00:00+08:00",
            [{"fund_code": "999999", "fund_name": "应去重", "action": "分批加仓"}],
        ),
        _report(
            "next-day",
            "2026-01-03T14:00:00+08:00",
            [{"fund_code": "000004", "fund_name": "D", "action": "观察"}],
        ),
    ]
    monkeypatch.setattr(recommendation_accuracy, "list_reports", lambda: reports)

    navs = {
        "000001": _nav_payload([1.0, 1.1, 1.2, 1.3, 1.4, 1.5]),
        "000002": _nav_payload([1.0, 1.1, 1.2, 1.3, 1.4, 1.5]),
        "000003": _nav_payload([1.0, 1.1, 1.2, 1.3, 1.4, 1.5]),
        "000004": _nav_payload([1.0, 1.1, 1.2, 1.3, 1.4, 1.5]),
    }
    result = recommendation_accuracy.build_recommendation_accuracy(
        limit_reports=30,
        horizons=(1, 5, 20),
        fetch_nav=lambda code, **_kwargs: navs[code],
        trade_dates=_TRADE_DATES,
    )

    assert result["deduplication"] == {
        "key": "report_calendar_date",
        "strategy": "latest_created_at_then_id",
        "input_report_count": 3,
        "selected_report_count": 2,
        "duplicate_report_count": 1,
    }
    assert result["by_style"] == {}
    bucket = result["legacy_reference"]["by_style"]["tactical"]
    assert bucket["recommendation_count"] == 4
    assert bucket["eligible_count"] == 3
    assert bucket["observation_count"] == 1
    assert bucket["by_horizon"]["T+1"]["mature_count"] == 3
    assert bucket["by_horizon"]["T+1"]["hit_count"] == 3
    assert bucket["by_horizon"]["T+1"]["hit_rate_percent"] == 100.0
    assert bucket["hit_rate_percent"] == 100.0


def test_accuracy_reports_missing_data_as_skipped_and_coverage(monkeypatch):
    reports = [
        _report(
            "r1",
            "2026-01-02T14:00:00+08:00",
            [
                {"fund_code": "000001", "fund_name": "有数据", "action": "分批加仓"},
                {"fund_code": "000002", "fund_name": "无数据", "action": "减仓评估"},
            ],
        )
    ]
    monkeypatch.setattr(recommendation_accuracy, "list_reports", lambda: reports)

    result = recommendation_accuracy.build_recommendation_accuracy(
        limit_reports=30,
        horizons=(1,),
        fetch_nav=lambda code, **_kwargs: (
            _nav_payload([1.0, 1.1]) if code == "000001" else None
        ),
        trade_dates=_TRADE_DATES,
    )

    assert result["eligible_count"] == 0
    stats = result["legacy_reference"]["by_style"]["tactical"]["by_horizon"]["T+1"]
    assert stats["eligible_count"] == 2
    assert stats["mature_count"] == 1
    assert stats["skipped_count"] == 1
    assert stats["coverage_percent"] == 50.0
    assert stats["hit_rate_percent"] == 100.0


@pytest.mark.parametrize("horizon", [0, -1, 1.5, True])
def test_forward_outcome_rejects_invalid_horizon(horizon):
    with pytest.raises(ValueError):
        build_recommendation_outcomes(
            _report("r1", "2026-01-02T14:00:00+08:00", []),
            None,
            horizons=(horizon,),
            fetch_nav=lambda *_args, **_kwargs: None,
            trade_dates=_TRADE_DATES,
        )
