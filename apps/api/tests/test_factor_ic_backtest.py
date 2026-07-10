"""因子有效性回测（Rank IC）引擎与版本化输出测试。"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.services.factor_ic_backtest import (
    NavPoint,
    _rank_ic_for_period,
    _rankdata,
    _spearman,
    compute_factor_ic,
)


def test_rankdata_handles_ties() -> None:
    assert _rankdata([10, 10, 20]) == [1.5, 1.5, 3.0]


def test_spearman_perfect_directions_and_zero_variance() -> None:
    assert _spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert _spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert _spearman([1, 2, 3, 4], [1, 4, 9, 16]) == 1.0
    assert _spearman([1, 1, 1], [1, 2, 3]) is None


def test_rank_ic_insufficient_cross_section() -> None:
    factor_values = {str(index): float(index) for index in range(5)}
    forward_returns = {str(index): float(index) for index in range(5)}
    assert (
        _rank_ic_for_period(
            factor_values,
            forward_returns,
            min_cross_section=10,
        )
        is None
    )


def test_rank_ic_aligns_on_common_codes() -> None:
    factor_values = {"a": 1.0, "b": 2.0, "c": 3.0, "d": None}
    forward_returns = {"a": 1.0, "b": 2.0, "c": 3.0}
    assert (
        _rank_ic_for_period(
            factor_values,
            forward_returns,
            min_cross_section=3,
        )
        == 1.0
    )


def test_planted_momentum_signal_detected() -> None:
    rng = random.Random(42)
    calendar = [f"D{index:04d}" for index in range(600)]
    panel: dict[str, list[NavPoint]] = {}
    for index in range(20):
        slope = 0.0005 * (index + 1)
        nav = 1.0
        points: list[NavPoint] = []
        for day in calendar:
            nav *= (1.0 + slope) * (1.0 + rng.uniform(-0.003, 0.003))
            points.append(NavPoint(day, nav))
        panel[f"{index:06d}"] = points
    result = compute_factor_ic(nav_panel=panel, calendar=calendar)
    momentum = next(row for row in result.factors if row.factor == "momentum")
    assert result.available is True
    assert momentum.mean_ic is not None and momentum.mean_ic > 0.7
    assert momentum.significant is True


def test_noise_panel_not_significant() -> None:
    rng = random.Random(1)
    calendar = [f"D{index:04d}" for index in range(600)]
    panel: dict[str, list[NavPoint]] = {}
    for index in range(20):
        nav = 1.0
        points: list[NavPoint] = []
        for day in calendar:
            nav *= 1.0 + rng.uniform(-0.01, 0.01)
            points.append(NavPoint(day, nav))
        panel[f"{index:06d}"] = points
    result = compute_factor_ic(nav_panel=panel, calendar=calendar)
    momentum = next(row for row in result.factors if row.factor == "momentum")
    assert momentum.significant is False


def test_future_mutation_does_not_change_earlier_ic_periods() -> None:
    calendar = [f"D{index:04d}" for index in range(400)]
    base: dict[str, list[tuple[str, float]]] = {}
    for index in range(15):
        slope = 0.0004 * (index + 1)
        base[f"{index:06d}"] = [
            (day, (1.0 + slope) ** offset)
            for offset, day in enumerate(calendar)
        ]
    panel_a = {
        code: [NavPoint(day, value) for day, value in series]
        for code, series in base.items()
    }
    panel_b: dict[str, list[NavPoint]] = {}
    for code, series in base.items():
        points = [NavPoint(day, value) for day, value in series]
        for offset in range(len(points) - 30, len(points)):
            points[offset] = NavPoint(points[offset].date, points[offset].nav * 1.5)
        panel_b[code] = points
    result_a = compute_factor_ic(nav_panel=panel_a, calendar=calendar)
    result_b = compute_factor_ic(nav_panel=panel_b, calendar=calendar)
    momentum_a = next(row for row in result_a.factors if row.factor == "momentum")
    momentum_b = next(row for row in result_b.factors if row.factor == "momentum")
    stable_count = min(len(momentum_a.ic_series), len(momentum_b.ic_series)) - 2
    assert stable_count > 0
    assert momentum_a.ic_series[:stable_count] == momentum_b.ic_series[:stable_count]


def test_small_universe_unavailable() -> None:
    calendar = [f"D{index:04d}" for index in range(300)]
    panel = {
        f"{index:06d}": [
            NavPoint(day, 1.0 + 0.001 * offset)
            for offset, day in enumerate(calendar)
        ]
        for index in range(5)
    }
    result = compute_factor_ic(nav_panel=panel, calendar=calendar)
    assert result.available is False
    assert result.message is not None


def test_few_periods_not_significant() -> None:
    calendar = [f"D{index:04d}" for index in range(120)]
    panel = {
        f"{index:06d}": [
            NavPoint(day, (1.0 + 0.0003 * (index + 1)) ** offset)
            for offset, day in enumerate(calendar)
        ]
        for index in range(15)
    }
    result = compute_factor_ic(nav_panel=panel, calendar=calendar)
    assert result.available is True
    assert all(not row.significant for row in result.factors)


def test_runner_writes_versioned_utc_summary(tmp_path) -> None:
    from scripts.run_factor_ic import build_ic_report

    calendar = [f"D{index:04d}" for index in range(400)]

    def fetch_rank(_limit: int) -> list[dict]:
        return [
            {"fund_code": f"{index:06d}", "fund_name": f"基金{index}"}
            for index in range(15)
        ]

    def fetch_nav(code: str, _name: str, _days: int) -> list[NavPoint]:
        index = int(code)
        return [
            NavPoint(day, (1.0 + 0.0003 * (index + 1)) ** offset)
            for offset, day in enumerate(calendar)
        ]

    build_ic_report(
        fetch_rank=fetch_rank,
        fetch_nav=fetch_nav,
        out_dir=str(tmp_path),
        universe_size=15,
        nav_days=400,
    )
    payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    generated_at = datetime.fromisoformat(payload["generated_at"])
    assert payload["schema_version"] == 1
    assert generated_at.tzinfo is not None
    assert payload["run_date"] == generated_at.date().isoformat()


def test_runner_sampled_mode_stratifies_pool(tmp_path) -> None:
    from scripts.run_factor_ic import build_ic_report

    calendar = [f"D{index:04d}" for index in range(400)]
    seen_limits: list[int] = []

    def fetch_rank(limit: int) -> list[dict]:
        seen_limits.append(limit)
        return [
            {"fund_code": f"{index:06d}", "fund_name": f"基金{index}"}
            for index in range(60)
        ]

    def fetch_nav(code: str, _name: str, _days: int) -> list[NavPoint]:
        index = int(code)
        return [
            NavPoint(day, (1.0 + 0.0003 * (index + 1)) ** offset)
            for offset, day in enumerate(calendar)
        ]

    result = build_ic_report(
        fetch_rank=fetch_rank,
        fetch_nav=fetch_nav,
        out_dir=str(tmp_path),
        universe_size=12,
        universe_mode="sampled",
        sample_pool_size=60,
        nav_days=400,
    )
    assert result["available"] is True
    assert result["params"]["universe_mode"] == "sampled"
    assert seen_limits == [60]
    assert result["universe_size"] == 12


def test_runner_fails_before_nav_fetch_when_rank_is_unavailable(tmp_path) -> None:
    from scripts.run_factor_ic import FactorIcRankUnavailable, build_ic_report

    nav_calls = 0

    def fetch_nav(*_args):
        nonlocal nav_calls
        nav_calls += 1
        return []

    with pytest.raises(
        FactorIcRankUnavailable,
        match="开放式基金排行榜获取失败",
    ):
        build_ic_report(
            fetch_rank=lambda _limit: [],
            fetch_nav=fetch_nav,
            out_dir=str(tmp_path),
            universe_mode="sampled",
            universe_size=300,
            sample_pool_size=500,
        )

    assert nav_calls == 0
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "report.txt").exists()


def test_runner_cli_reports_rank_source_failure(monkeypatch, capsys) -> None:
    from scripts import run_factor_ic as runner

    def fail(**_kwargs):
        raise runner.FactorIcRankUnavailable("开放式基金排行榜获取失败")

    monkeypatch.setattr(runner, "build_ic_report", fail)
    monkeypatch.setattr(runner.sys, "argv", ["run_factor_ic.py"])

    assert runner.main() == 2
    assert "开放式基金排行榜获取失败" in capsys.readouterr().err


def _valid_publish_payload() -> dict:
    factors = [
        {
            "factor": factor,
            "n_periods": 12,
            "mean_ic": 0.01,
            "ic_std": 0.2,
            "icir": 0.05,
            "t_stat": 0.3,
            "positive_ratio": 0.5,
            "significant": False,
        }
        for factor in ("momentum", "risk_adjusted", "drawdown", "composite")
    ]
    return {
        "summary": {
            "schema_version": 1,
            "run_date": "2026-07-10",
            "generated_at": "2026-07-10T08:00:00+00:00",
            "params": {
                "universe_size": 300,
                "universe_mode": "sampled",
                "sample_pool_size": 500,
                "nav_days": 750,
                "rebalance_step": 21,
                "forward_days": 20,
                "factor_lookback": 250,
            },
            "available": True,
            "universe_size": 300,
            "rebalance_count": 12,
            "forward_days": 20,
            "factors": factors,
        },
        "source_commit": "a" * 40,
        "source_run_id": "12345",
    }


def test_publish_contract_accepts_exact_production_shape() -> None:
    from app.services.factor_ic_snapshot import FactorIcPublishRequest

    request = FactorIcPublishRequest.model_validate(_valid_publish_payload())
    assert request.summary.schema_version == 1
    assert request.summary.universe_size == 300
    assert all(not row.significant for row in request.summary.factors)


def test_publish_contract_rejects_wrong_schema_version() -> None:
    from app.services.factor_ic_snapshot import FactorIcPublishRequest

    payload = _valid_publish_payload()
    payload["summary"]["schema_version"] = 2
    with pytest.raises(ValidationError, match="schema_version"):
        FactorIcPublishRequest.model_validate(payload)


def test_validate_publish_request_enforces_generation_window() -> None:
    from app.services.factor_ic_snapshot import validate_publish_request

    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    request = validate_publish_request(_valid_publish_payload(), now=now)
    assert request.summary.generated_at == now - timedelta(hours=1)

    future = _valid_publish_payload()
    future["summary"]["generated_at"] = "2026-07-10T09:06:00+00:00"
    with pytest.raises(ValueError, match="未来"):
        validate_publish_request(future, now=now)

    expired = _valid_publish_payload()
    expired["summary"]["generated_at"] = "2026-07-09T08:59:59+00:00"
    expired["summary"]["run_date"] = "2026-07-09"
    with pytest.raises(ValueError, match="24 小时"):
        validate_publish_request(expired, now=now)


@given(st.integers(min_value=0, max_value=10_000))
@settings(max_examples=30, deadline=None)
def test_ic_series_within_bounds(seed: int) -> None:
    rng = random.Random(seed)
    calendar = [f"D{index:04d}" for index in range(350)]
    panel: dict[str, list[NavPoint]] = {}
    for index in range(15):
        nav = 1.0
        points: list[NavPoint] = []
        for day in calendar:
            nav *= 1.0 + rng.uniform(-0.02, 0.02)
            points.append(NavPoint(day, nav))
        panel[f"{index:06d}"] = points
    result = compute_factor_ic(nav_panel=panel, calendar=calendar)
    for row in result.factors:
        assert all(-1.0 - 1e-9 <= ic <= 1.0 + 1e-9 for ic in row.ic_series)
        if row.positive_ratio is not None:
            assert 0.0 <= row.positive_ratio <= 1.0
