from __future__ import annotations

from datetime import datetime, timezone

from app.database import (
    get_fund_sector_resolution_stats,
    get_fund_sector_current,
    list_fund_primary_sectors_by_sector_names,
    list_fund_sector_exposure_snapshots,
    list_fund_sector_resolution_statuses,
    save_fund_sector_resolution_statuses,
)
from app.services.fund_primary_sector_types import PrimarySectorRecord
from app.services.fund_sector_identity import (
    current_identity_rows_for_api,
    is_current_identity_row_executable,
    is_current_identity_row_reproducibly_verified,
    materialize_holdings_sector_assessment,
    materialize_primary_sector_record,
)


def _verified_holdings_record() -> PrimarySectorRecord:
    return PrimarySectorRecord(
        fund_code="000711",
        sector_name="医药",
        intraday_index_name="中证医药卫生指数",
        source="precompute_holdings",
        confidence=0.9,
        detail={
            "scores": {"医药": 35.0, "消费": 8.0},
            "coverage": {
                "classified_mass_percent": 43.0,
                "dominant_theme_ratio": 35.0 / 43.0,
            },
            "qualification": {
                "sector_inference_eligible": True,
                "research_only": False,
            },
            "snapshot_hash": "holdings-snapshot-1",
            "report_period": "2026Q2",
            "as_of_date": "2026-06-30",
            "available_at": "2026-07-21T09:00:00+08:00",
            "evidence": [
                {
                    "stock": "甲公司",
                    "theme": "医药",
                    "weight": 12.0,
                    "snapshot_hash": "holdings-snapshot-1",
                    "industry_ref_id": "industry-ref-1",
                },
                {
                    "stock": "乙公司",
                    "theme": "消费",
                    "weight": 8.0,
                    "snapshot_hash": "holdings-snapshot-1",
                    "industry_ref_id": "industry-ref-2",
                },
            ],
        },
    )


def test_materializes_multi_sector_snapshot_and_fast_verified_lookup() -> None:
    result = materialize_primary_sector_record(
        _verified_holdings_record(),
        evaluated_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )

    assert result["identity_status"] == "verified"
    assert result["current_replaced"] is True
    current = get_fund_sector_current("000711")
    assert [(row["sector_name"], row["is_primary"]) for row in current] == [
        ("医药", 1),
        ("消费", 0),
    ]
    assert current[0]["exposure_percent"] == 35.0
    assert current[0]["report_period"] == "2026Q2"
    assert current[0]["as_of_date"] == "2026-06-30"
    assert current[0]["evidence_snapshot_id"] == result["snapshot_id"]

    snapshots = list_fund_sector_exposure_snapshots("000711")
    assert len(snapshots) == 2
    assert {row["sector_name"] for row in snapshots} == {"医药", "消费"}

    discovery_rows = list_fund_primary_sectors_by_sector_names(["医药", "消费"])
    assert [row["sector_name"] for row in discovery_rows] == ["医药"]
    assert discovery_rows[0]["identity_status"] == "verified"
    assert discovery_rows[0]["exposure_percent"] == 35.0


def test_research_only_holdings_are_persisted_but_never_executable() -> None:
    result = materialize_holdings_sector_assessment(
        fund_code="000712",
        source="precompute_holdings",
        evaluated_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        evidence_payload={
            "snapshot_hash": "holdings-snapshot-2",
            "report_period": "2026Q2",
            "as_of": "2026-06-30",
            "available_at": "2026-07-21T09:00:00+08:00",
        },
        sector_clue={
            "sector_name": "医药",
            "scores": {"医药": 10.0, "消费": 9.0},
            "evidence": [],
            "coverage": {"dominant_theme_ratio": 10.0 / 19.0},
            "qualification": {
                "sector_inference_eligible": False,
                "research_only": True,
                "reason_codes": ["industry_theme_dominance_insufficient"],
            },
        },
    )

    assert result is not None
    assert result["identity_status"] == "pending"
    current = get_fund_sector_current("000712")
    assert current[0]["identity_status"] == "pending"
    assert list_fund_primary_sectors_by_sector_names(["医药"]) == []
    assert len(list_fund_sector_exposure_snapshots("000712")) == 2


def test_pending_name_or_llm_mapping_cannot_replace_verified_holdings() -> None:
    materialize_primary_sector_record(
        _verified_holdings_record(),
        evaluated_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    pending = materialize_primary_sector_record(
        PrimarySectorRecord(
            fund_code="000711",
            sector_name="金融科技",
            intraday_index_name=None,
            source="precompute_llm",
            confidence=0.99,
            detail={"fund_name": "某主题基金"},
        ),
        evaluated_at=datetime(2026, 8, 5, 1, tzinfo=timezone.utc),
    )

    assert pending["identity_status"] == "pending"
    assert pending["current_replaced"] is False
    current = get_fund_sector_current("000711")
    assert current[0]["sector_name"] == "医药"
    assert current[0]["source"] == "precompute_holdings"
    assert len(list_fund_sector_exposure_snapshots("000711")) == 3


def test_api_projection_marks_expired_rows_stale() -> None:
    rows = [
        {
            "fund_code": "000711",
            "sector_name": "医药",
            "identity_status": "verified",
            "expires_at": "2020-01-01T00:00:00+00:00",
            "detail": '{"coverage":{"classified_mass_percent":35}}',
        }
    ]

    projected = current_identity_rows_for_api(rows)

    assert projected[0]["fresh"] is False
    assert projected[0]["effective_identity_status"] == "stale"
    assert projected[0]["detail"]["coverage"]["classified_mass_percent"] == 35


def test_repair_gate_rechecks_legacy_active_benchmark_verification() -> None:
    base = {
        "fund_code": "001195",
        "sector_name": "农业",
        "source": "precompute_benchmark",
        "identity_status": "verified",
        "is_primary": 1,
        "expires_at": "2099-01-01T00:00:00+00:00",
    }
    legacy_active = {
        **base,
        "detail": {
            "benchmark_text": "中信农林牧渔一级行业指数收益率×80%+债券指数×20%",
        },
    }
    exact_passive = {
        **base,
        "sector_name": "黄金股",
        "detail": {
            "price_proxy_eligible": True,
            "index_code": "931238",
            "benchmark_text": "中证沪深港黄金产业股票指数收益率×95%+存款×5%",
        },
    }

    assert is_current_identity_row_executable(legacy_active) is True
    assert is_current_identity_row_reproducibly_verified(legacy_active) is False
    assert is_current_identity_row_reproducibly_verified(exact_passive) is True


def test_resolution_status_batch_distinguishes_unmapped_from_unavailable() -> None:
    saved = save_fund_sector_resolution_statuses(
        [
            {
                "fund_code": "000001",
                "resolution_status": "unmapped",
                "stage": "bulk_benchmark_profile",
                "reason_code": "non_sector_fund_category",
                "fund_name": "纯债基金A",
                "checked_at": "2026-08-05T00:00:00+00:00",
                "next_retry_at": "2026-09-04T00:00:00+00:00",
                "attempt_count": 1,
                "mapping_version": "fund_sector_identity.2026-08.v1",
                "detail": {"fund_category": "债券型"},
            },
            {
                "fund_code": "000002",
                "resolution_status": "unavailable",
                "stage": "bulk_benchmark_profile",
                "reason_code": "profile_row_unavailable",
                "fund_name": "待补基金A",
                "checked_at": "2026-08-05T00:00:00+00:00",
                "next_retry_at": "2026-08-05T06:00:00+00:00",
                "attempt_count": 2,
                "mapping_version": "fund_sector_identity.2026-08.v1",
                "detail": {"provider_failed": False},
            },
        ]
    )

    assert saved == 2
    rows = list_fund_sector_resolution_statuses()
    assert rows["000001"]["resolution_status"] == "unmapped"
    assert rows["000002"]["attempt_count"] == 2
    assert get_fund_sector_resolution_stats() == {
        "unavailable": 1,
        "unmapped": 1,
        "total": 2,
    }
