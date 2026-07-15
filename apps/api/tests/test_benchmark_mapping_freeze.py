from __future__ import annotations

import json
from datetime import datetime, timezone

from app.database import _connect, save_report
from app.models import FundRecommendation, Holding, Report, RiskAssessment
from app.services.benchmark_mapping_service import (
    freeze_fund_benchmark_spec,
    parse_fund_contract_components,
)


def test_complete_composite_contract_keeps_every_leg_and_weight() -> None:
    components, complete = parse_fund_contract_components(
        "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%",
        fallback_index_code="931743",
        fallback_index_name="中证半导体材料设备主题指数",
    )

    assert complete is True
    assert [item["component_type"] for item in components] == ["index", "cash_rate"]
    assert [item["weight_percent"] for item in components] == [95.0, 5.0]
    assert components[0]["benchmark_code"] == "931743"


def test_unknown_contract_leg_is_frozen_but_not_formal_complete() -> None:
    components, complete = parse_fund_contract_components(
        "中证人工智能主题指数收益率×80%+未识别策略组合×20%",
        fallback_index_code="930713",
        fallback_index_name="中证人工智能主题指数",
    )

    assert complete is False
    assert len(components) == 2
    assert components[1]["component_type"] == "unknown"


def test_cash_flow_index_is_not_misclassified_as_cash_rate() -> None:
    components, complete = parse_fund_contract_components(
        "中证现金流量指数收益率×100%",
        fallback_index_code="000914",
        fallback_index_name="中证现金流量指数",
    )

    assert complete is True
    assert components[0]["component_type"] == "index"
    assert components[0]["benchmark_code"] == "000914"


def test_fallback_index_identity_is_not_copied_into_an_unknown_leg() -> None:
    components, complete = parse_fund_contract_components(
        "未知策略指数×50%+中证半导体材料设备主题指数×50%",
        fallback_index_code="931743",
        fallback_index_name="中证半导体材料设备主题指数",
    )

    assert complete is False
    assert components[0]["component_type"] == "unknown"
    assert components[1]["benchmark_code"] == "931743"


def _insert_cached_benchmark(
    *, available_at: str, detail_override: dict | None = None
) -> None:
    detail = detail_override or {
        "index_code": "931743",
        "index_name": "中证半导体材料设备主题指数",
        "benchmark_text": (
            "中证半导体材料设备主题指数收益率×95%+"
            "银行活期存款利率（税后）×5%"
        ),
        "benchmark_text_kind": "performance_benchmark",
        "benchmark_text_source_kind": "verified_fund_contract",
        "benchmark_text_truncated": False,
    }
    with _connect() as connection:
        connection.execute(
            "INSERT INTO fund_primary_sectors "
            "(userId, fund_code, sector_name, intraday_index_name, source, confidence, detail, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                "021533",
                "半导体材料",
                "中证半导体材料设备主题指数",
                "benchmark_index",
                0.92,
                json.dumps(detail, ensure_ascii=False),
                available_at,
            ),
        )


def test_freeze_rejects_benchmark_evidence_that_arrived_after_decision() -> None:
    _insert_cached_benchmark(available_at="2026-07-11T00:00:00+00:00")
    with _connect() as connection:
        spec, mapping = freeze_fund_benchmark_spec(
            fund_code="021533",
            decision_at="2026-07-10T06:30:00+00:00",
            user_id=1,
            connection=connection,
        )

    assert mapping is None
    assert spec["tier"] == "unavailable"
    assert spec["reason"] == "point_in_time_benchmark_mapping_unavailable"


def test_tracking_target_is_reference_only_even_when_text_is_complete() -> None:
    _insert_cached_benchmark(
        available_at="2026-07-09T00:00:00+00:00",
        detail_override={
            "index_code": "000300",
            "index_name": "沪深300指数",
            "benchmark_text": "沪深300指数",
            "benchmark_text_kind": "tracking_target",
            "benchmark_text_source_kind": "xq_akshare_aggregator",
            "benchmark_text_truncated": False,
        },
    )
    with _connect() as connection:
        spec, mapping = freeze_fund_benchmark_spec(
            fund_code="021533",
            decision_at="2026-07-10T06:30:00+00:00",
            user_id=1,
            connection=connection,
        )

    assert mapping is not None
    assert spec["tier"] == "tracked_index_exact"
    assert spec["formal_excess_eligible"] is False
    assert spec["contract_verification_kind"] == "xq_akshare_aggregator"


def test_legacy_live_disclosure_label_is_not_trusted_as_formal_contract() -> None:
    _insert_cached_benchmark(
        available_at="2026-07-09T00:00:00+00:00",
        detail_override={
            "index_code": "931743",
            "index_name": "reference index",
            "benchmark_text": "reference index 931743 x 100%",
            "benchmark_text_kind": "performance_benchmark",
            # Historical Xueqiu/AkShare rows used this over-strong label.
            "benchmark_text_source_kind": "live_fund_disclosure",
            "benchmark_text_truncated": False,
        },
    )
    with _connect() as connection:
        spec, mapping = freeze_fund_benchmark_spec(
            fund_code="021533",
            decision_at="2026-07-10T06:30:00+00:00",
            user_id=1,
            connection=connection,
        )

    assert mapping is not None
    assert spec["tier"] == "tracked_index_exact"
    assert spec["formal_excess_eligible"] is False
    assert spec["contract_verification_kind"] == "live_fund_disclosure"
    assert spec["reason"] == "tracking_index_is_reference_only"


def test_truncated_or_static_fallback_text_cannot_become_formal_contract() -> None:
    _insert_cached_benchmark(
        available_at="2026-07-09T00:00:00+00:00",
        detail_override={
            "index_code": "931743",
            "index_name": "中证半导体材料设备主题指数",
            "benchmark_text": "中证半导体材料设备主题指数收益率×100%",
            "benchmark_text_kind": "performance_benchmark",
            "benchmark_text_source_kind": "static_fallback",
            "benchmark_text_truncated": False,
        },
    )
    with _connect() as connection:
        static_spec, _ = freeze_fund_benchmark_spec(
            fund_code="021533",
            decision_at="2026-07-10T06:30:00+00:00",
            user_id=1,
            connection=connection,
        )

    assert static_spec["tier"] == "tracked_index_exact"
    assert static_spec["formal_excess_eligible"] is False


def test_save_report_freezes_and_persists_complete_contract_mapping() -> None:
    _insert_cached_benchmark(available_at="2026-07-09T00:00:00+00:00")
    report = Report(
        id="benchmark-report",
        created_at=datetime(2026, 7, 10, 6, 30, tzinfo=timezone.utc),
        title="日报",
        risk=RiskAssessment(
            level="low",
            suggested_action="watch",
            weighted_return_percent=0,
            alerts=[],
        ),
        holdings=[
            Holding(
                fund_code="021533",
                fund_name="天弘中证半导体材料设备主题指数C",
                holding_amount=1_000,
            )
        ],
        fund_recommendations=[
            FundRecommendation(
                fund_code="021533",
                fund_name="天弘中证半导体材料设备主题指数C",
                action="分批加仓",
            )
        ],
        summary="摘要",
        recommendations=["分批加仓"],
        caveats=[],
        analysis_facts={"portfolio": {"round_trip_fee_percent": 1.5}},
    )

    saved = save_report(report)

    event = saved.decision_events[0]
    assert event["benchmark"]["tier"] == "fund_contract_exact"
    assert event["benchmark"]["status"] == "complete"
    assert event["benchmark"]["formal_excess_eligible"] is True
    assert event["benchmark"]["contract_verification_kind"] == (
        "verified_fund_contract"
    )
    assert event["benchmark_mapping_id"]
    with _connect() as connection:
        mapping = connection.execute(
            "SELECT mapping_id, benchmark_kind, completeness, payload "
            "FROM fund_benchmark_mappings WHERE userId = ? AND fund_code = ?",
            (1, "021533"),
        ).fetchone()
    assert mapping is not None
    assert mapping["mapping_id"] == event["benchmark_mapping_id"]
    assert mapping["benchmark_kind"] == "official_contract"
    assert mapping["completeness"] == "complete"
    stored_payload = json.loads(mapping["payload"])
    assert len(stored_payload["components"]) == 2
    assert stored_payload["contract_verification_kind"] == "verified_fund_contract"
