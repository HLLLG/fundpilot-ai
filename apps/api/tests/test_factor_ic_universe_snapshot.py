from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import refresh_settings
from app.services.factor_ic_universe_snapshot import (
    FACTOR_IC_UNIVERSE_SAMPLE_TARGET,
    FactorIcUniverseStorageUnavailable,
    build_factor_ic_universe_payload,
    publish_factor_ic_universe_snapshot,
    read_factor_ic_universe_history,
    validate_factor_ic_universe_publish_request,
)


def _member(index: int, available_at: datetime) -> dict:
    code = f"{index:06d}"
    fund_type = ("gp", "hh", "zq", "zs")[index % 4]
    return {
        "fund_code": code,
        "fund_name": f"测试组合{index}A",
        "fund_type": fund_type,
        "share_class": "A",
        "canonical_fund_code": code,
        "canonical_portfolio_key": hashlib.sha256(
            f"{fund_type}\n测试组合{index}".encode()
        ).hexdigest(),
        "inception_date": "2020-01-01",
        "available_at": available_at.isoformat(),
        "source_rank": index,
        "metadata": {
            "nav_date": available_at.date().isoformat(),
            "latest_nav": 1.0 + index / 10_000,
            "daily_growth_percent": float((index % 9) - 4),
            "snapshot_available_at": available_at.isoformat(),
        },
    }


def valid_universe_payload(available_at: datetime | None = None) -> dict:
    instant = available_at or datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    members = [_member(index, instant) for index in range(1, 1_201)]
    return {
        "snapshot": {
            "schema_version": 1,
            "snapshot_date": instant.date().isoformat(),
            "available_at": instant.isoformat(),
            "captured_at": instant.isoformat(),
            "source": "eastmoney_open_fund_universe",
            "source_share_count": 5_000,
            "deduped_fund_count": 1_500,
            "sampled_fund_count": 1_200,
            "sample_target": 1_500,
            "fund_type_count": 4,
            "source_by_type": {"hh": 1_250, "zq": 1_250, "zs": 1_250, "gp": 1_250},
            "deduped_by_type": {"hh": 375, "zq": 375, "zs": 375, "gp": 375},
            "sampled_by_type": {"hh": 300, "zq": 300, "zs": 300, "gp": 300},
        },
        "members": members,
        "source_commit": "a" * 40,
        "source_run_id": "12345",
    }


def _use_sqlite(monkeypatch, tmp_path, name: str = "pit.db") -> None:
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / name))
    refresh_settings()


def test_builder_captures_full_catalogue_and_stratified_target() -> None:
    instant = datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    rows = [
        {
            "fund_code": f"{index:06d}",
            "fund_name": f"组合{index}A",
            "fund_type": ("gp", "hh", "zq", "zs", "qdii", "fof")[index % 6],
            "established_date": "2020-01-01",
            "nav_date": instant.date().isoformat(),
            "latest_nav": 1.0 + index / 10_000,
            "daily_growth_percent": float((index % 9) - 4),
            "return_1y_percent": float(index % 100),
        }
        for index in range(1, 5_101)
    ]

    payload = build_factor_ic_universe_payload(
        rows,
        source_commit="a" * 40,
        source_run_id="run-1",
        captured_at=instant,
    )

    assert payload["snapshot"]["source_share_count"] == 5_100
    assert payload["snapshot"]["deduped_fund_count"] == 5_100
    assert payload["snapshot"]["sampled_fund_count"] == 1_500
    assert payload["snapshot"]["sample_target"] == FACTOR_IC_UNIVERSE_SAMPLE_TARGET
    assert payload["snapshot"]["fund_type_count"] == 6
    assert len({member["fund_code"] for member in payload["members"]}) == 1_500
    assert len({member["canonical_portfolio_key"] for member in payload["members"]}) == 1_500
    observed = payload["members"][0]
    observed_index = int(observed["fund_code"])
    assert observed["metadata"] == {
        "nav_date": instant.date().isoformat(),
        "latest_nav": 1.0 + observed_index / 10_000,
        "daily_growth_percent": float((observed_index % 9) - 4),
        "return_1y_percent": float(observed_index % 100),
        "snapshot_available_at": instant.isoformat(),
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_share_count", 4_999, "源份额覆盖不足"),
        ("deduped_fund_count", 1_499, "去重基金组合不足"),
        ("sampled_fund_count", 1_199, "抽样基金数"),
        ("fund_type_count", 3, "基金类型不足"),
    ],
)
def test_quality_gates_are_strict(field: str, value: int, message: str) -> None:
    payload = valid_universe_payload()
    payload["snapshot"][field] = value
    with pytest.raises(ValueError, match=message):
        validate_factor_ic_universe_publish_request(
            payload,
            now=datetime(2026, 7, 13, 9, tzinfo=timezone.utc),
        )


def test_member_code_portfolio_and_time_travel_are_rejected() -> None:
    now = datetime(2026, 7, 13, 9, tzinfo=timezone.utc)
    duplicate_code = valid_universe_payload()
    duplicate_code["members"][1]["fund_code"] = duplicate_code["members"][0]["fund_code"]
    duplicate_code["members"][1]["canonical_fund_code"] = duplicate_code["members"][0]["fund_code"]
    with pytest.raises(ValueError, match="fund_code 必须唯一"):
        validate_factor_ic_universe_publish_request(duplicate_code, now=now)

    future_inception = valid_universe_payload()
    future_inception["members"][0]["inception_date"] = "2026-07-14"
    with pytest.raises(ValueError, match="成立日晚于"):
        validate_factor_ic_universe_publish_request(future_inception, now=now)

    future_available = valid_universe_payload()
    future_available["members"][0]["available_at"] = "2026-07-13T09:01:00+00:00"
    with pytest.raises(ValueError, match="穿越快照时点"):
        validate_factor_ic_universe_publish_request(future_available, now=now)


def test_nav_observation_is_optional_but_pairing_and_values_fail_closed() -> None:
    now = datetime(2026, 7, 13, 9, tzinfo=timezone.utc)

    optional = valid_universe_payload()
    for key in ("nav_date", "latest_nav", "daily_growth_percent"):
        optional["members"][0]["metadata"].pop(key)
    validated = validate_factor_ic_universe_publish_request(optional, now=now)
    assert validated.members[0].metadata == {
        "snapshot_available_at": "2026-07-13T08:00:00+00:00"
    }

    missing_nav = valid_universe_payload()
    missing_nav["members"][0]["metadata"].pop("latest_nav")
    with pytest.raises(ValueError, match="nav_date/latest_nav 必须成对出现"):
        validate_factor_ic_universe_publish_request(missing_nav, now=now)

    missing_date = valid_universe_payload()
    missing_date["members"][0]["metadata"].pop("nav_date")
    with pytest.raises(ValueError, match="nav_date/latest_nav 必须成对出现"):
        validate_factor_ic_universe_publish_request(missing_date, now=now)

    for invalid_nav in (0, -1, float("nan"), float("inf"), True):
        invalid = valid_universe_payload()
        invalid["members"][0]["metadata"]["latest_nav"] = invalid_nav
        with pytest.raises(ValueError, match="latest_nav 必须是有限正数"):
            validate_factor_ic_universe_publish_request(invalid, now=now)

    invalid_growth = valid_universe_payload()
    invalid_growth["members"][0]["metadata"]["daily_growth_percent"] = float("inf")
    with pytest.raises(ValueError, match="daily_growth_percent 必须是有限数字"):
        validate_factor_ic_universe_publish_request(invalid_growth, now=now)


def test_nav_observation_cannot_cross_snapshot_and_observation_timestamp_is_exact() -> None:
    now = datetime(2026, 7, 13, 9, tzinfo=timezone.utc)
    future_nav = valid_universe_payload()
    future_nav["members"][0]["metadata"]["nav_date"] = "2026-07-14"
    with pytest.raises(ValueError, match="nav_date 穿越"):
        validate_factor_ic_universe_publish_request(future_nav, now=now)

    wrong_observation_time = valid_universe_payload()
    wrong_observation_time["members"][0]["metadata"][
        "snapshot_available_at"
    ] = "2026-07-13T07:59:59+00:00"
    with pytest.raises(ValueError, match="与快照时点不一致"):
        validate_factor_ic_universe_publish_request(wrong_observation_time, now=now)


def test_current_catalogue_cannot_be_relabelled_or_replayed_as_historical() -> None:
    payload = valid_universe_payload(datetime(2026, 7, 10, 8, tzinfo=timezone.utc))
    payload["snapshot"]["snapshot_date"] = "2026-07-09"
    with pytest.raises(ValueError, match="available_at"):
        validate_factor_ic_universe_publish_request(
            payload,
            now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
        )

    stale = valid_universe_payload(datetime(2026, 7, 10, 8, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="超过 24 小时"):
        validate_factor_ic_universe_publish_request(
            stale,
            now=datetime(2026, 7, 11, 8, 1, tzinfo=timezone.utc),
        )


def test_publish_is_atomic_idempotent_and_history_is_bounded(monkeypatch, tmp_path) -> None:
    _use_sqlite(monkeypatch, tmp_path)
    instants = [
        datetime(2026, 7, day, 8, tzinfo=timezone.utc) for day in (10, 11, 12)
    ]
    ids: list[str] = []
    for index, instant in enumerate(instants):
        payload = valid_universe_payload(instant)
        payload["source_commit"] = f"{index + 1:040x}"
        request = validate_factor_ic_universe_publish_request(
            payload,
            now=instant + timedelta(hours=1),
        )
        result = publish_factor_ic_universe_snapshot(request, now=instant + timedelta(hours=1))
        ids.append(result["snapshot_id"])
    duplicate = publish_factor_ic_universe_snapshot(
        validate_factor_ic_universe_publish_request(
            valid_universe_payload(instants[0]),
            now=instants[0] + timedelta(hours=1),
        ),
        now=instants[0] + timedelta(hours=1),
    )
    assert duplicate == {"created": False, "snapshot_id": ids[0]}

    history = read_factor_ic_universe_history(
        days=5,
        max_snapshots=2,
        stride_days=2,
        now=datetime(2026, 7, 12, 10, tzinfo=timezone.utc),
    )
    assert [row["snapshot_date"] for row in history["snapshots"]] == [
        "2026-07-10",
        "2026-07-12",
    ]
    assert all(len(row["members"]) == 1_200 for row in history["snapshots"])
    assert history["snapshot_count"] == 2

    with pytest.raises(ValueError, match="max_snapshots"):
        read_factor_ic_universe_history(max_snapshots=261)


def test_published_snapshot_keeps_first_nav_observation_immutable(monkeypatch, tmp_path) -> None:
    _use_sqlite(monkeypatch, tmp_path, "immutable-nav.db")
    instant = datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    request = validate_factor_ic_universe_publish_request(
        valid_universe_payload(instant),
        now=instant + timedelta(hours=1),
    )
    code = request.members[0].fund_code
    first_nav = request.members[0].metadata["latest_nav"]

    published = publish_factor_ic_universe_snapshot(
        request,
        now=instant + timedelta(hours=1),
    )
    request.members[0].metadata["latest_nav"] = 99.0

    history = read_factor_ic_universe_history(
        days=1,
        max_snapshots=1,
        stride_days=1,
        now=instant + timedelta(hours=2),
    )
    members = {
        member["fund_code"]: member
        for member in history["snapshots"][0]["members"]
    }
    assert history["snapshots"][0]["snapshot_id"] == published["snapshot_id"]
    assert members[code]["metadata"]["latest_nav"] == first_nav
    assert members[code]["metadata"]["latest_nav"] != 99.0


def test_mysql_configuration_rejects_sqlite_fallback(monkeypatch) -> None:
    class FallbackConnection:
        dialect = "sqlite"

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return None

        def execute(self, *_args, **_kwargs):
            raise AssertionError("fallback storage must not be queried")

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://user:password@db.example.test:3306/fundpilot",
    )
    refresh_settings()
    instant = datetime(2026, 7, 13, 8, tzinfo=timezone.utc)
    request = validate_factor_ic_universe_publish_request(
        valid_universe_payload(instant),
        now=instant + timedelta(hours=1),
    )
    with pytest.raises(FactorIcUniverseStorageUnavailable, match="拒绝回落"):
        publish_factor_ic_universe_snapshot(
            request,
            connection_factory=FallbackConnection,
            now=instant + timedelta(hours=1),
        )
    with pytest.raises(FactorIcUniverseStorageUnavailable, match="拒绝回落"):
        read_factor_ic_universe_history(
            connection_factory=FallbackConnection,
            now=instant,
        )


def test_tampered_sample_distribution_is_rejected() -> None:
    payload = deepcopy(valid_universe_payload())
    payload["snapshot"]["sampled_by_type"]["gp"] = 299
    with pytest.raises(ValueError, match="sampled_by_type"):
        validate_factor_ic_universe_publish_request(
            payload,
            now=datetime(2026, 7, 13, 9, tzinfo=timezone.utc),
        )
