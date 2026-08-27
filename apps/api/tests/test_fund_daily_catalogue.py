from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.database import (
    get_fund_daily_catalogue_meta,
    list_fund_daily_catalogue,
    list_fund_daily_catalogue_by_codes,
    list_fund_daily_catalogue_by_verified_sectors,
    replace_fund_daily_catalogue,
    replace_fund_research_profiles,
    replace_fund_sector_current,
    upsert_fund_risk_metrics,
)
from app.services.discovery_candidate_pool import _overlay_catalogue_on_identity_rows
from app.services.fund_research_profile_store import _reset_profile_memory_for_tests


@pytest.fixture(autouse=True)
def _reset_research_memory() -> None:
    _reset_profile_memory_for_tests()
    yield
    _reset_profile_memory_for_tests()


def _catalogue_row(code: str, *, name: str = "测试基金", **extra: object) -> dict:
    row = {
        "fund_code": code,
        "fund_name": name,
        "fund_type": "gp",
        "source_fund_type": "股票型",
        "nav_date": "2026-08-25",
        "latest_nav": 1.23,
        "daily_growth_percent": 0.4,
        "established_date": "2018-01-01",
        "return_3m_percent": 3.1,
        "return_6m_percent": 6.2,
        "return_1y_percent": 12.3,
        "return_3y_percent": 28.4,
        "rank_enriched": True,
    }
    row.update(extra)
    return row


def _verified_identity(
    code: str,
    sector_name: str,
    *,
    identity_status: str = "verified",
    expires_at: str | None = None,
) -> None:
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    replace_fund_sector_current(
        fund_code=code,
        rows=[
            {
                "fund_code": code,
                "sector_name": sector_name,
                "exposure_percent": 80.0,
                "is_primary": True,
                "identity_status": identity_status,
                "source": "precompute_holdings",
                "confidence": 0.9,
                "evidence_snapshot_id": f"snap-{code}",
                "source_ref": f"snap-{code}",
                "report_period": "2026Q2",
                "as_of_date": "2026-06-30",
                "available_at": "2026-07-21T09:00:00+08:00",
                "resolved_at": now.isoformat(),
                "expires_at": expires_at or (now + timedelta(days=30)).isoformat(),
                "mapping_version": "fund_sector_identity.2026-08.v1",
                "detail": {},
            }
        ],
    )


def test_replace_fund_daily_catalogue_is_atomic_snapshot() -> None:
    first = replace_fund_daily_catalogue(
        [
            _catalogue_row("000001", name="旧基金甲"),
            _catalogue_row("000002", name="旧基金乙"),
        ],
        snapshot_available_at="2026-08-25T16:00:00+00:00",
        source="eastmoney_test",
    )
    second = replace_fund_daily_catalogue(
        [_catalogue_row("000003", name="新基金")],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="eastmoney_test",
    )

    assert first == 2
    assert second == 1
    rows = list_fund_daily_catalogue()
    assert [row["fund_code"] for row in rows] == ["000003"]
    assert rows[0]["fund_name"] == "新基金"
    assert rows[0]["return_3y_percent"] == 28.4
    assert rows[0]["rank_enriched"] is True
    meta = get_fund_daily_catalogue_meta()
    assert meta == {
        "snapshot_available_at": "2026-08-26T16:00:00+00:00",
        "source": "eastmoney_test",
        "row_count": 1,
    }


def test_list_fund_daily_catalogue_by_codes_skips_invalid() -> None:
    replace_fund_daily_catalogue(
        [_catalogue_row("000011"), _catalogue_row("000012")],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="eastmoney_test",
    )

    found = list_fund_daily_catalogue_by_codes(["11", "000012", "not-a-code", "000000"])

    assert set(found) == {"000011", "000012"}
    assert found["000011"]["fund_name"] == "测试基金"


def test_join_returns_only_fresh_verified_identities() -> None:
    replace_fund_daily_catalogue(
        [
            _catalogue_row("000021", name="半导体甲"),
            _catalogue_row("000022", name="半导体乙"),
            _catalogue_row("000023", name="医药丙"),
        ],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="eastmoney_test",
    )
    _verified_identity("000021", "半导体")
    _verified_identity(
        "000022",
        "半导体",
        expires_at=(datetime(2026, 8, 1, tzinfo=timezone.utc)).isoformat(),
    )
    _verified_identity("000023", "医药")

    rows = list_fund_daily_catalogue_by_verified_sectors(["半导体"])

    assert [row["fund_code"] for row in rows] == ["000021"]
    assert rows[0]["sector_name"] == "半导体"
    assert rows[0]["identity_status"] == "verified"
    assert rows[0]["fund_name"] == "半导体甲"
    assert rows[0]["return_1y_percent"] == 12.3
    assert rows[0]["catalogue_source"] == "eastmoney_test"
    assert "sector_name" not in list_fund_daily_catalogue()[0]


def test_overlay_keeps_identity_and_fills_catalogue_metrics() -> None:
    replace_fund_daily_catalogue(
        [_catalogue_row("000021", name="半导体甲")],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="eastmoney_test",
    )

    rows = _overlay_catalogue_on_identity_rows(
        [
            {
                "fund_code": "000021",
                "sector_name": "半导体",
                "source": "precompute_holdings",
                "identity_status": "verified",
            },
            {
                "fund_code": "000099",
                "sector_name": "半导体",
                "source": "precompute_holdings",
                "identity_status": "verified",
            },
        ]
    )

    assert rows[0]["fund_name"] == "半导体甲"
    assert rows[0]["return_1y_percent"] == 12.3
    assert rows[0]["sector_name"] == "半导体"
    assert rows[1]["fund_code"] == "000099"
    assert rows[1].get("return_1y_percent") is None


def test_overlay_fills_research_profile_and_risk_without_writing_catalogue() -> None:
    replace_fund_daily_catalogue(
        [_catalogue_row("000021", name="半导体甲")],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="eastmoney_test",
    )
    replace_fund_research_profiles(
        [
            {
                "fund_code": "000021",
                "fund_name": "半导体甲",
                "fund_scale_yi": 12.5,
                "fund_scale_basis": "nav_times_latest_shares",
                "fund_manager": "测试经理",
                "established_date": "2018-01-01",
            }
        ],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
        source="sina.fund_scale_open_sina",
    )
    upsert_fund_risk_metrics(
        [
            {
                "fund_code": "000021",
                "sharpe_1y": 0.74,
                "max_drawdown_1y_percent": -18.5,
                "nav_as_of": "2026-08-25",
                "nav_point_count": 243,
            }
        ],
        snapshot_available_at="2026-08-26T16:00:00+00:00",
    )

    rows = _overlay_catalogue_on_identity_rows(
        [
            {
                "fund_code": "000021",
                "sector_name": "半导体",
                "source": "precompute_holdings",
                "identity_status": "verified",
            }
        ]
    )

    assert rows[0]["fund_scale_yi"] == 12.5
    assert rows[0]["fund_manager"] == "测试经理"
    assert rows[0]["max_drawdown_1y_percent"] == -18.5
    assert rows[0]["sharpe_1y"] == 0.74
    catalogue = list_fund_daily_catalogue()[0]
    assert "fund_scale_yi" not in catalogue or catalogue.get("fund_scale_yi") is None
    assert "max_drawdown_1y_percent" not in catalogue or catalogue.get(
        "max_drawdown_1y_percent"
    ) is None
