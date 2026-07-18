from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.factor_ic_backtest import NavPoint
from app.services.factor_ic_pit import compute_point_in_time_segmented_ic
from app.services.factor_ic_research import is_v3_research_model_publishable
from scripts import fetch_factor_ic_nav_observations, run_factor_ic


def _dates(count: int) -> list[str]:
    start = date(2026, 1, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


def _snapshot(dates: list[str]) -> dict:
    return {
        "snapshot_id": "snapshot-1",
        "snapshot_date": dates[0],
        "available_at": f"{dates[0]}T00:00:00+00:00",
        "members": [
            {
                "fund_code": code,
                "fund_type": "gp",
                "available_at": f"{dates[0]}T00:00:00+00:00",
            }
            for code in ("000001", "000002")
        ],
    }


def _panel(*, backfilled: bool) -> dict[str, list[NavPoint]]:
    dates = _dates(14)
    observed_day = dates[-1]
    return {
        code: [
            NavPoint(
                day,
                1.0 + index * slope,
                "first_observed_nav_ratio",
                f"{observed_day if backfilled else day}T12:00:00+00:00",
            )
            for index, day in enumerate(dates)
        ]
        for code, slope in (("000001", 0.01), ("000002", 0.02))
    }


def test_nav_observation_pit_does_not_relabel_backfilled_history() -> None:
    dates = _dates(14)
    common = {
        "snapshots": [_snapshot(dates)],
        "rebalance_step": 1,
        "forward_horizons": (1,),
        "factor_lookback": 3,
        "min_cross_section": 2,
        "max_snapshot_age_days": 30,
        "walk_forward_folds": 5,
        "embargo_days": 1,
        "nav_observation_pit": True,
    }

    _segments, backfilled = compute_point_in_time_segmented_ic(
        nav_panel=_panel(backfilled=True),
        **common,
    )
    _segments, truthful = compute_point_in_time_segmented_ic(
        nav_panel=_panel(backfilled=False),
        **common,
    )

    assert backfilled["point_in_time_scope"] == "nav_observation_pit"
    assert backfilled["nav_revision_pit"] is True
    assert backfilled["nav_covered_membership_count"] == 0
    assert truthful["nav_covered_membership_count"] > 0
    assert truthful["observation_timestamp_coverage_rate"] == 1.0


def test_runner_loader_binds_first_observed_at_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    observations = [
        {
            "fund_code": "000001",
            "nav_date": f"2026-01-0{index}",
            "unit_nav": 1.0 + index / 100,
            "cumulative_nav": None,
            "daily_growth_percent": 1.0 if index > 1 else None,
            "first_observed_at": f"2026-01-0{index}T12:00:00+00:00",
            "available_at": f"2026-01-0{index}T12:00:00+00:00",
            "source": "test",
            "observation_id": f"fnav_{index}",
        }
        for index in range(1, 4)
    ]
    payload = {
        "schema_version": "factor_ic_nav_observation_history.v1",
        "point_in_time_scope": "nav_observation_pit",
        "nav_revision_pit": True,
        "availability_basis": "collector_first_observed_at",
        "revision_policy": "first_observed_value",
        "as_of": "2026-01-04T00:00:00+00:00",
        "fund_code_count": 1,
        "observation_count": 3,
        "content_hash": run_factor_ic._canonical_hash(observations),
        "observations": observations,
    }
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    panel, metadata = run_factor_ic._load_nav_observation_file(str(path))

    assert metadata["observation_count"] == 3
    assert len(panel["000001"]) == 3
    assert panel["000001"][0].observed_at == "2026-01-01T12:00:00+00:00"

    payload["observations"][0]["unit_nav"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="contract"):
        run_factor_ic._load_nav_observation_file(str(path))


def test_fetcher_verifies_each_chunk_hash_and_combines_rows() -> None:
    rows = [
        {
            "fund_code": "000001",
            "nav_date": "2026-01-02",
            "unit_nav": 1.0,
            "first_observed_at": "2026-01-02T12:00:00+00:00",
        }
    ]

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "schema_version": "factor_ic_nav_observation_history.v1",
                "point_in_time_scope": "nav_observation_pit",
                "nav_revision_pit": True,
                "availability_basis": "collector_first_observed_at",
                "revision_policy": "first_observed_value",
                "content_hash": fetch_factor_ic_nav_observations._hash(rows),
                "revision_rows_excluded": 0,
                "observations": rows,
            }

    class Client:
        def post(self, *_args, **_kwargs) -> Response:
            return Response()

    result = fetch_factor_ic_nav_observations.fetch_nav_observation_history(
        url="https://example.invalid/internal",
        token="token",
        fund_codes=["000001"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        as_of=datetime(2026, 1, 4, tzinfo=timezone.utc),
        client=Client(),
    )

    assert result["observation_count"] == 1
    assert result["content_hash"] == fetch_factor_ic_nav_observations._hash(rows)


def test_publishability_requires_exact_nav_observation_provenance() -> None:
    point_in_time = {
        "ready": True,
        "walk_forward_folds": 5,
        "embargo_trading_days": 20,
        "multiple_testing": "benjamini_hochberg",
        "point_in_time_scope": "nav_observation_pit",
        "nav_revision_pit": True,
        "nav_publication_lag_trading_days": {"default": 0, "qdii": 0},
        "availability_basis": "collector_first_observed_at",
        "revision_policy": "first_observed_value",
        "observation_timestamp_coverage_rate": 1.0,
        "execution_entry_offset_trading_days": 1,
    }
    qualified_row = {
        "factor": "momentum",
        "economic_significance": {"qualified": True},
    }
    model = {
        "primary_horizon": 20,
        "point_in_time": point_in_time,
        "segments": {
            key: {
                "horizons": {
                    "20": {
                        "qualified": {"momentum": True},
                        "factors": [qualified_row],
                    }
                }
            }
            for key in ("gp", "hh", "zq", "zs")
        },
        "peer_distributions": {
            key: {"eligible_count": 20} for key in ("gp", "hh", "zq", "zs")
        },
        "fund_classifications": {str(index): "gp" for index in range(5_000)},
    }

    assert is_v3_research_model_publishable(model) is True
    point_in_time["availability_basis"] = "nav_date_backfill"
    assert is_v3_research_model_publishable(model) is False
