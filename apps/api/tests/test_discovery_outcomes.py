from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services.discovery_outcomes import (
    build_discovery_outcomes,
    build_discovery_recommendation_accuracy,
)


def _business_dates(count: int, *, start: date = date(2026, 1, 5)) -> list[str]:
    values: list[str] = []
    cursor = start
    while len(values) < count:
        if cursor.weekday() < 5:
            values.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return values


def _nav_rows(count: int, *, start_nav: float = 1.0) -> list[dict]:
    return [
        {"date": day, "nav": round(start_nav + index * 0.01, 4)}
        for index, day in enumerate(_business_dates(count))
    ]


def _report(*recommendations: dict, created_at: str = "2026-01-05T10:00:00+08:00") -> dict:
    return {
        "id": "discovery-report-1",
        "created_at": created_at,
        "recommendations": list(recommendations),
    }


def _rec(code: str = "110011", action: str = "分批买入") -> dict:
    return {"fund_code": code, "fund_name": f"基金{code}", "action": action}


def _fetch(rows_by_code: dict[str, list[dict]]):
    def fetch(code: str, *, trading_days: int):
        _ = trading_days
        rows = rows_by_code.get(code)
        return {"data": rows} if rows is not None else None

    return fetch


def test_strict_horizon_uses_exact_target_nav_not_latest_row():
    rows = _nav_rows(5)
    rows[-1]["nav"] = 0.5

    result = build_discovery_outcomes(
        _report(_rec()),
        days=2,
        fetch_nav=_fetch({"110011": rows}),
    )

    item = result["items"][0]
    assert item["mature"] is True
    assert item["baseline_nav_date"] == rows[0]["date"]
    assert item["target_nav_date"] == rows[2]["date"]
    assert item["target_nav"] == rows[2]["nav"]
    assert item["latest_nav_date"] == rows[2]["date"]  # 旧字段映射到目标点，而非数据集末尾
    assert item["period_change_percent"] == 2.0


@pytest.mark.parametrize("target_nav", [1.0, 0.995])
def test_buy_action_is_not_a_hit_without_positive_target_return(target_nav: float):
    rows = _nav_rows(3)
    rows[1]["nav"] = target_nav

    result = build_discovery_outcomes(
        _report(_rec()),
        days=1,
        fetch_nav=_fetch({"110011": rows}),
    )

    item = result["items"][0]
    assert item["mature"] is True
    assert item["direction_aligned"] is False
    assert item["status"] == "miss"
    assert result["hit_count"] == 0
    assert result["hit_rate_percent"] == 0.0


def test_horizon_is_pending_until_n_forward_trading_days_exist():
    result = build_discovery_outcomes(
        _report(_rec()),
        days=5,
        fetch_nav=_fetch({"110011": _nav_rows(5)}),
    )

    item = result["items"][0]
    assert item["eligible"] is True
    assert item["mature"] is False
    assert item["skipped"] is False
    assert item["status"] == "pending"
    assert item["observed_forward_trading_days"] == 4
    assert item["period_change_percent"] is None
    assert item["direction_aligned"] is None
    assert result["eligible_count"] == 1
    assert result["mature_count"] == 0
    assert result["pending_count"] == 1
    assert result["coverage_percent"] == 0.0


def test_watch_conditional_and_unknown_actions_are_skipped_not_hits():
    report = _report(
        _rec("110011", "建议关注"),
        _rec("110012", "等待回调"),
        _rec("110013", "未知动作"),
    )
    fetch = _fetch({code: _nav_rows(10) for code in ("110011", "110012", "110013")})

    result = build_discovery_outcomes(report, days=5, fetch_nav=fetch)

    assert result["eligible_count"] == 0
    assert result["mature_count"] == 0
    assert result["skipped_count"] == 3
    assert result["hit_count"] == 0
    assert result["hit_rate_percent"] is None
    assert [item["action_category"] for item in result["items"]] == [
        "watch_only",
        "conditional_wait",
        "unknown",
    ]
    assert all(item["eligible"] is False for item in result["items"])
    assert all(item["skipped"] is True for item in result["items"])
    assert all(item["direction_aligned"] is None for item in result["items"])


def test_mixed_result_reports_eligible_mature_skipped_and_coverage():
    result = build_discovery_outcomes(
        _report(
            _rec("110011", "分批买入"),
            _rec("110012", "分批买入"),
            _rec("110013", "建议关注"),
        ),
        days=5,
        fetch_nav=_fetch(
            {
                "110011": _nav_rows(6),
                "110012": _nav_rows(4),
                "110013": _nav_rows(6),
            }
        ),
    )

    assert result["total_count"] == 3
    assert result["eligible_count"] == 2
    assert result["mature_count"] == 1
    assert result["pending_count"] == 1
    assert result["skipped_count"] == 1
    assert result["coverage_percent"] == 50.0
    assert result["coverage"] == {
        "total": 3,
        "eligible": 2,
        "mature": 1,
        "pending": 1,
        "skipped": 1,
        "mature_over_eligible_percent": 50.0,
    }


def test_after_close_report_uses_next_valuation_date_as_baseline():
    rows = _nav_rows(4)
    rows[0]["nav"] = 1.0
    rows[1]["nav"] = 2.0
    rows[2]["nav"] = 3.0

    result = build_discovery_outcomes(
        _report(_rec(), created_at="2026-01-05T16:00:00+08:00"),
        days=1,
        fetch_nav=_fetch({"110011": rows}),
    )

    item = result["items"][0]
    assert item["baseline_nav_date"] == rows[1]["date"]
    assert item["target_nav_date"] == rows[2]["date"]
    assert item["period_change_percent"] == 50.0
    assert item["baseline_policy"] == "first_valuation_on_or_after_executable_date"


@pytest.mark.parametrize("days", [5, 20, 60])
def test_supported_research_horizons(days: int):
    captured: list[int] = []

    def fetch(_code: str, *, trading_days: int):
        captured.append(trading_days)
        return {"data": _nav_rows(days + 1)}

    result = build_discovery_outcomes(_report(_rec()), days=days, fetch_nav=fetch)

    assert result["days"] == days
    assert result["horizon"] == f"T+{days}"
    assert result["supported_horizons"] == [5, 20, 60]
    assert result["mature_count"] == 1
    assert captured[0] >= days + 20


def test_legacy_seven_day_call_remains_supported():
    result = build_discovery_outcomes(
        _report(_rec()),
        days=7,
        fetch_nav=_fetch({"110011": _nav_rows(8)}),
    )

    assert result["days"] == 7
    assert result["horizon"] == "T+7"
    assert result["mature_count"] == 1


def test_benchmark_is_explicitly_unavailable_instead_of_fabricated():
    result = build_discovery_outcomes(
        _report(_rec()),
        days=5,
        fetch_nav=_fetch({"110011": _nav_rows(6)}),
    )

    assert result["benchmark"] == {
        "available": False,
        "reason": "point_in_time_fund_benchmark_mapping_unavailable",
        "benchmark_code": None,
        "period_change_percent": None,
    }
    item = result["items"][0]
    assert item["benchmark_available"] is False
    assert item["benchmark_change_percent"] is None
    assert item["excess_return_percent"] is None


def test_take_profit_is_unknown_until_its_own_horizon_matures():
    report = _report(_rec())
    report["discovery_facts"] = {"profile": {"take_profit_threshold_percent": 1.0}}

    result = build_discovery_outcomes(
        report,
        days=1,
        fetch_nav=_fetch({"110011": _nav_rows(2)}),
    )

    assert result["items"][0]["mature"] is True
    assert result["items"][0]["hit_take_profit_within_days"] is None


def test_accuracy_counts_only_eligible_mature_items():
    reports = [
        _report(_rec("110011", "分批买入")),
        _report(_rec("110012", "分批买入")),
        _report(_rec("110013", "建议关注")),
    ]
    result = build_discovery_recommendation_accuracy(
        reports,
        days=5,
        fetch_nav=_fetch(
            {
                "110011": _nav_rows(6),
                "110012": _nav_rows(4),
                "110013": _nav_rows(6),
            }
        ),
    )

    assert result["eligible_count"] == 0
    assert result["mature_count"] == 0
    assert result["sample_count"] == 0
    legacy = result["legacy_reference"]
    assert legacy["eligible_count"] == 2
    assert legacy["mature_count"] == 1
    assert legacy["skipped_count"] == 1
    assert legacy["coverage_percent"] == 50.0
    assert legacy["hit_rate_percent"] == 100.0


@pytest.mark.parametrize("days", [5, 7, 20, 60])
def test_accuracy_api_compatibility_forwards_supported_and_legacy_horizons(
    monkeypatch,
    days: int,
):
    from app import main

    captured: dict[str, int | bool] = {}
    monkeypatch.setattr(main, "list_discovery_reports", lambda **_kwargs: [])

    def build(_reports, *, days: int, persist_outcomes: bool = False):
        captured["days"] = days
        captured["persist_outcomes"] = persist_outcomes
        return {"days": days}

    monkeypatch.setattr(main, "build_discovery_recommendation_accuracy", build)

    assert main.fund_discovery_recommendation_accuracy(days=days) == {"days": days}
    assert captured["days"] == days
    assert captured["persist_outcomes"] is True


def test_dynamic_event_contract_links_decision_and_mature_observation(monkeypatch):
    from datetime import datetime, timezone

    from app.services import discovery_outcomes as service

    observed_at = datetime(2026, 2, 1, 8, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "_observation_now", lambda: observed_at)

    result = build_discovery_outcomes(
        _report(_rec()),
        days=5,
        fetch_nav=_fetch({"110011": _nav_rows(6)}),
    )

    assert result["schema_version"] == "1.0"
    assert len(result["decision_events"]) == 1
    assert len(result["outcome_observations"]) == 1

    event = result["decision_events"][0]
    assert event == {
        "schema_version": "1.0",
        "event_id": "discovery:discovery-report-1:110011",
        "event_type": "fund_discovery_decision",
        "report_id": "discovery-report-1",
        "decision_at": "2026-01-05T02:00:00+00:00",
        "fund_code": "110011",
        "fund_name": "基金110011",
        "action": "分批买入",
        "action_category": "buy",
        "eligible": True,
    }

    observation = result["outcome_observations"][0]
    assert observation["schema_version"] == "1.0"
    assert observation["observation_id"] == "discovery:discovery-report-1:110011:T+5"
    assert observation["event_id"] == event["event_id"]
    assert observation["horizon_trading_days"] == 5
    assert observation["target_date"] == _nav_rows(6)[5]["date"]
    assert observation["observation_at"] == "2026-02-01T08:30:00+00:00"
    assert observation["status"] == "hit"
    assert observation["source"] == "akshare.fund_open_fund_info_em"
    assert observation["baseline"] == {
        "date": _nav_rows(6)[0]["date"],
        "nav": 1.0,
    }
    assert observation["target"] == {
        "date": _nav_rows(6)[5]["date"],
        "nav": 1.05,
    }
    assert observation["return_percent"] == 5.0
    assert observation["direction_hit"] is True
    assert result["items"][0]["event_id"] == event["event_id"]
    assert result["items"][0]["observation_id"] == observation["observation_id"]


def test_pending_item_still_emits_outcome_observation(monkeypatch):
    from datetime import datetime, timezone

    from app.services import discovery_outcomes as service

    monkeypatch.setattr(
        service,
        "_observation_now",
        lambda: datetime(2026, 1, 9, 9, 0, tzinfo=timezone.utc),
    )
    result = build_discovery_outcomes(
        _report(_rec()),
        days=5,
        fetch_nav=_fetch({"110011": _nav_rows(4)}),
    )

    observation = result["outcome_observations"][0]
    assert observation["status"] == "pending"
    assert observation["target_date"] is None
    assert observation["target"] == {"date": None, "nav": None}
    assert observation["return_percent"] is None
    assert observation["direction_hit"] is None
    assert observation["mature"] is False
    assert observation["skipped"] is False


def test_duplicate_fund_decisions_receive_stable_unique_event_ids():
    report = _report(
        _rec("110011", "分批买入"),
        _rec("110011", "分批买入"),
        _rec("110011", "建议关注"),
    )
    fetch = _fetch({"110011": _nav_rows(6)})

    first = build_discovery_outcomes(report, days=5, fetch_nav=fetch)
    second = build_discovery_outcomes(report, days=5, fetch_nav=fetch)
    first_ids = [event["event_id"] for event in first["decision_events"]]
    second_ids = [event["event_id"] for event in second["decision_events"]]

    assert first_ids == second_ids
    assert len(set(first_ids)) == 3
    assert all(value.startswith("discovery:discovery-report-1:110011:") for value in first_ids)
    assert len(first["outcome_observations"]) == 3
    assert first["outcome_observations"][2]["status"] == "skipped"
    assert first["outcome_observations"][2]["source"] == "not_applicable"


def test_duplicate_buy_recommendations_fetch_nav_once_per_request():
    calls: list[tuple[str, int]] = []

    def fetch(code: str, *, trading_days: int):
        calls.append((code, trading_days))
        return {"data": _nav_rows(8)}

    result = build_discovery_outcomes(
        _report(
            _rec("110011", "分批买入"),
            _rec("110011", "少量买入"),
        ),
        days=5,
        fetch_nav=fetch,
    )

    assert len(calls) == 1
    assert calls[0][0] == "110011"
    assert [item["mature"] for item in result["items"]] == [True, True]


def test_accuracy_fetches_repeated_fund_once_at_largest_required_window(monkeypatch):
    from app.services import discovery_outcomes as service

    pull_days_by_date = {
        "2026-01-05": 90,
        "2026-02-02": 120,
    }
    monkeypatch.setattr(
        service,
        "_nav_pull_days",
        lambda executable_date, _horizon: pull_days_by_date[executable_date],
    )
    calls: list[tuple[str, int]] = []

    def fetch(code: str, *, trading_days: int):
        calls.append((code, trading_days))
        return {"data": _nav_rows(60)}

    reports = [
        _report(_rec("110011"), created_at="2026-01-05T10:00:00+08:00"),
        _report(_rec("110011"), created_at="2026-02-02T10:00:00+08:00"),
    ]

    build_discovery_recommendation_accuracy(reports, days=5, fetch_nav=fetch)

    assert calls == [("110011", 120)]
