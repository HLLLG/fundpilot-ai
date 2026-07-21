from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.fund_holdings_snapshot import (
    build_fund_holdings_snapshot,
    compute_fund_holdings_snapshot_hash,
    materialize_fund_holdings_snapshot_for_decision,
    validate_fund_holdings_snapshot_hash,
)
from app.services.fund_holdings_sector_infer import (
    fetch_portfolio_stocks_with_industry,
    fetch_portfolio_stocks_with_industry_evidence,
    infer_sector_from_portfolio_stocks,
)
from app.services.fund_lookthrough_research import build_fund_lookthrough_research
from app.services.us_qdii_valuation_service import (
    build_disclosed_holdings_contribution_map,
    build_holdings_reference_map,
    compute_holdings_reference,
)

CN = ZoneInfo("Asia/Shanghai")


def _row(code: str, weight: float, period: str, *, rank: int = 1) -> dict:
    return {
        "股票代码": code,
        "股票名称": f"股票{code}",
        "占净值比例": weight,
        "季度": period,
        "序号": rank,
    }


def _announcement(period: str, published_at: str) -> dict:
    return {
        "公告标题": f"测试基金{period}报告",
        "公告日期": published_at,
    }


def _built_snapshot(*, decision_at: datetime) -> dict:
    rows = [
        _row(
            f"{index:06d}",
            3.0,
            "2026年2季度股票投资明细",
            rank=index,
        )
        for index in range(1, 11)
    ]
    return build_fund_holdings_snapshot(
        rows,
        [_announcement("2026年第2季度", "2026-07-20")],
        fund_code="000001",
        decision_at=decision_at,
    )


def _client_snapshot(*, freshness: str = "fresh") -> dict:
    return {
        "schema_version": "fund_holdings_snapshot.v1",
        "fund_code": "000001",
        "decision_at": datetime.now(CN).isoformat(),
        "report_period": "2026-Q2",
        "as_of_date": "2026-06-30",
        "available_at": "2026-07-10T00:00:00+08:00",
        "status": "qualified",
        "qualified": True,
        "reason_codes": [],
        "snapshot_hash": "a" * 64,
        "freshness": {"label": freshness},
        "coverage": {
            "disclosed_holding_count": 10,
            "portfolio_weight_coverage_percent": 30.0,
            "coverage_ratio": 0.3,
        },
        "qualification": {
            "qualified": True,
            "pit_eligible": True,
            "nowcast_eligible": False,
        },
        "holdings": [
            {
                "security_code": f"600{index:03d}",
                "security_name": f"股票{index}",
                "weight_percent": 10.0 - index / 2,
                "rank": index,
            }
            for index in range(1, 11)
        ],
    }


def test_materialized_view_recomputes_staleness_without_changing_identity() -> None:
    snapshot = _built_snapshot(
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN)
    )
    stale = materialize_fund_holdings_snapshot_for_decision(
        snapshot,
        decision_at=datetime(2027, 2, 1, 12, 0, tzinfo=CN),
    )

    assert stale["snapshot_hash"] == snapshot["snapshot_hash"]
    assert stale["freshness"]["label"] == "stale"
    assert stale["qualification"]["disclosed_overlap_lower_bound_eligible"] is False
    assert stale["qualification"]["nowcast_eligible"] is False
    assert validate_fund_holdings_snapshot_hash(stale) is True


def test_materialized_view_before_publication_keeps_canonical_identity() -> None:
    snapshot = _built_snapshot(
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN)
    )

    unavailable = materialize_fund_holdings_snapshot_for_decision(
        snapshot,
        decision_at=datetime(2026, 7, 19, 12, 0, tzinfo=CN),
    )

    assert unavailable["status"] == "unavailable"
    assert unavailable["qualified"] is False
    assert "snapshot_after_decision" in unavailable["reason_codes"]
    assert unavailable["snapshot_hash"] == snapshot["snapshot_hash"]
    assert validate_fund_holdings_snapshot_hash(unavailable) is True


def test_dynamic_view_fields_are_rebuilt_from_hashed_source_validation() -> None:
    snapshot = _built_snapshot(
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN)
    )
    changed_view = deepcopy(snapshot)
    changed_view["status"] = "invalid"
    changed_view["qualified"] = False
    changed_view["reason_codes"] = ["view_tampered"]

    assert validate_fund_holdings_snapshot_hash(changed_view) is True
    rebuilt = materialize_fund_holdings_snapshot_for_decision(
        changed_view,
        decision_at=datetime(2026, 8, 31, 13, 0, tzinfo=CN),
    )
    assert rebuilt["status"] == "qualified"
    assert rebuilt["qualified"] is True
    assert rebuilt["reason_codes"] == []

    source_tampered = deepcopy(snapshot)
    source_tampered["source_validation"]["qualified"] = False
    assert validate_fund_holdings_snapshot_hash(source_tampered) is False


def test_snapshot_hash_helper_rejects_tampered_static_payload() -> None:
    snapshot = _built_snapshot(
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN)
    )
    assert compute_fund_holdings_snapshot_hash(snapshot) == snapshot["snapshot_hash"]
    assert validate_fund_holdings_snapshot_hash(snapshot) is True

    tampered = deepcopy(snapshot)
    tampered["holdings"][0]["weight_percent"] += 1
    assert validate_fund_holdings_snapshot_hash(tampered) is False
    materialized = materialize_fund_holdings_snapshot_for_decision(
        tampered,
        decision_at=datetime(2026, 9, 1, 12, 0, tzinfo=CN),
    )
    assert materialized["status"] == "invalid"
    assert materialized["reason_codes"] == ["stored_snapshot_hash_invalid"]

    non_json = deepcopy(snapshot)
    non_json["holdings"][0]["weight_percent"] = float("nan")
    assert validate_fund_holdings_snapshot_hash(non_json) is False


def test_missing_or_zero_coverage_cannot_become_qualified() -> None:
    zero = build_fund_holdings_snapshot(
        [_row("600001", 0.0, "2026年2季度股票投资明细")],
        [_announcement("2026年第2季度", "2026-07-20")],
        fund_code="000001",
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN),
    )
    assert zero["status"] == "invalid"
    assert zero["reason_codes"] == ["holding_weight_coverage_missing"]

    missing = _built_snapshot(
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN)
    )
    missing.pop("coverage")
    missing["snapshot_hash"] = compute_fund_holdings_snapshot_hash(missing)
    materialized = materialize_fund_holdings_snapshot_for_decision(
        missing,
        decision_at=datetime(2026, 9, 1, 12, 0, tzinfo=CN),
    )
    assert materialized["status"] == "invalid"
    assert materialized["reason_codes"] == ["stored_snapshot_coverage_invalid"]


def test_all_quarters_are_ordered_chronologically_q1_to_q4() -> None:
    rows: list[dict] = []
    announcements: list[dict] = []
    for quarter, published in (
        (1, "2026-04-20"),
        (2, "2026-07-20"),
        (3, "2026-10-20"),
        (4, "2027-01-20"),
    ):
        rows.extend(
            _row(
                f"{quarter}{index:05d}",
                2.0,
                f"2026年{quarter}季度股票投资明细",
                rank=index,
            )
            for index in range(1, 11)
        )
        announcements.append(
            _announcement(f"2026年第{quarter}季度", published)
        )

    result = build_fund_holdings_snapshot(
        rows,
        announcements,
        fund_code="000001",
        decision_at=datetime(2027, 1, 25, 12, 0, tzinfo=CN),
    )

    assert result["status"] == "qualified"
    assert result["report_period"] == "2026-Q4"
    assert {row["security_code"] for row in result["holdings"]} == {
        f"4{index:05d}" for index in range(1, 11)
    }


def test_static_security_identity_uses_only_unambiguous_code_namespace() -> None:
    snapshot = build_fund_holdings_snapshot(
        [
            _row("060000", 10, "2026年2季度股票投资明细", rank=1),
            _row("60000", 8, "2026年2季度股票投资明细", rank=2),
            _row("NVDA", 5, "2026年2季度股票投资明细", rank=3),
        ],
        [_announcement("2026年第2季度", "2026-07-20")],
        fund_code="000001",
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN),
    )
    by_code = {row["security_code"]: row for row in snapshot["holdings"]}

    assert by_code["060000"]["security_id"] == "CN:060000"
    assert (
        by_code["060000"]["security_identity_basis"]
        == "disclosed_code_format_cn_6_digit"
    )
    assert by_code["60000"]["security_id"] == "HK:60000"
    assert by_code["NVDA"]["security_id"] is None
    assert "listing_market" not in by_code["060000"]


@pytest.mark.parametrize("raw_code", [700, 700.0, "700.0"])
def test_lost_leading_zero_never_creates_a_cn_security_identity(
    raw_code: object,
) -> None:
    snapshot = build_fund_holdings_snapshot(
        [_row(raw_code, 10.0, "2026年2季度股票投资明细")],
        [_announcement("2026年第2季度", "2026-07-20")],
        fund_code="000001",
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN),
    )

    assert snapshot["status"] == "qualified"
    holding = snapshot["holdings"][0]
    assert holding["security_code"] == "700"
    assert holding["security_id"] is None
    assert holding["security_identity_basis"].startswith("unresolved_")
    assert "CN:000700" not in str(snapshot)


def test_explicit_portfolio_row_fund_code_mismatch_fails_closed() -> None:
    row = _row("600000", 10.0, "2026年2季度股票投资明细")
    row["基金代码"] = "999999"

    snapshot = build_fund_holdings_snapshot(
        [row],
        [_announcement("2026年第2季度", "2026-07-20")],
        fund_code="000001",
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN),
    )

    assert snapshot["status"] == "invalid"
    assert snapshot["qualified"] is False
    assert snapshot["holdings"] == []
    assert "portfolio_row_fund_code_mismatch" in snapshot["reason_codes"]


def test_static_namespaced_identity_joins_same_cn_code_but_not_hk_code() -> None:
    decision = datetime(2026, 8, 31, 12, 0, tzinfo=CN)

    def snapshot(fund_code: str, security_code: str) -> dict:
        built = build_fund_holdings_snapshot(
            [_row(security_code, 10, "2026年2季度股票投资明细")],
            [_announcement("2026年第2季度", "2026-07-20")],
            fund_code=fund_code,
            decision_at=decision,
        )
        built["audit"] = {
            **dict(built.get("audit") or {}),
            "snapshot_repository": {
                "source": "append_only_store",
                "first_observed_at": "2026-07-20T09:05:00+08:00",
            },
        }
        return built

    research = build_fund_lookthrough_research(
        [snapshot("000001", "060000")],
        [{"fund_code": "000001", "holding_amount": 100}],
        [snapshot("000002", "060000"), snapshot("000003", "60000")],
        decision_at=decision,
        portfolio_positions_complete=True,
        portfolio_denominator_yuan=100,
        portfolio_denominator_source={
            "available_at": "2026-08-31T10:00:00+08:00",
            "first_observed_at": "2026-08-31T10:00:00+08:00",
            "source": "portfolio.ledger",
            "ref_id": "account-1",
        },
    )
    candidates = {row["fund_code"]: row for row in research["candidates"]}

    assert candidates["000002"][
        "portfolio_security_overlap_lower_bound_percent"
    ] == 10
    assert candidates["000003"][
        "portfolio_security_overlap_lower_bound_percent"
    ] is None
    assert candidates["000003"]["common_disclosed_weight_percent"] == 0


def test_historical_repository_miss_never_calls_live_resolver(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "resolve_fund_holdings_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("historical client must be store-only")
        ),
    )

    result = repository.resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=datetime(2025, 7, 1, 12, 0, tzinfo=CN),
        force_refresh=True,
    )
    assert result["status"] == "unavailable"
    assert result["reason_codes"] == ["historical_snapshot_not_observed"]


def test_repository_rejects_tampered_stored_payload_without_live_fallback(
    monkeypatch,
) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    snapshot = _built_snapshot(
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN)
    )
    snapshot["holdings"][0]["weight_percent"] += 1
    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: {
            "payload": snapshot,
            "first_observed_at": "2026-08-31T03:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        repository,
        "resolve_fund_holdings_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("tampered historical evidence must not trigger live")
        ),
    )

    result = repository.resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=datetime(2026, 9, 1, 12, 0, tzinfo=CN),
    )
    assert result["status"] == "invalid"
    assert result["qualified"] is False
    assert result["reason_codes"] == ["stored_snapshot_hash_invalid"]


def test_repository_rebases_stored_freshness_and_disables_stale_use(
    monkeypatch,
) -> None:
    from app.services import fund_holdings_snapshot_repository as repository

    snapshot = _built_snapshot(
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN)
    )
    monkeypatch.setattr(
        repository.database,
        "get_latest_fund_holdings_snapshot",
        lambda **_kwargs: {
            "payload": snapshot,
            "first_observed_at": "2026-08-31T03:00:00+00:00",
        },
    )

    result = repository.resolve_fund_holdings_snapshot_at_decision(
        "000001",
        decision_at=datetime(2027, 2, 1, 12, 0, tzinfo=CN),
    )
    assert result["status"] == "qualified"
    assert result["qualified"] is True
    materialized = result["snapshot"]
    assert materialized["freshness"]["label"] == "stale"
    assert (
        materialized["qualification"]["disclosed_overlap_lower_bound_eligible"]
        is False
    )
    assert materialized["qualification"]["nowcast_eligible"] is False
    repository_audit = materialized["audit"]["snapshot_repository"]
    assert repository_audit["source"] == "append_only_store"
    assert repository_audit["live_attempted"] is False


def test_sector_history_does_not_attach_current_industry(monkeypatch) -> None:
    from app.services import fund_holdings_sector_infer as service

    snapshot = _client_snapshot()
    monkeypatch.setattr(
        service,
        "resolve_fund_holdings_snapshot_at_decision",
        lambda *_args, **_kwargs: {
            "status": "qualified",
            "reason_codes": [],
            "decision_at": "2025-07-01T12:00:00+08:00",
            "source": "append_only_store",
            "snapshot": snapshot,
        },
    )
    monkeypatch.setattr(
        service,
        "_fetch_current_industries",
        lambda _rows: (_ for _ in ()).throw(
            AssertionError("historical industry enrichment is forbidden")
        ),
    )

    result = fetch_portfolio_stocks_with_industry_evidence(
        "000001",
        decision_at="2025-07-01T12:00:00+08:00",
    )
    assert result["status"] == "unavailable"
    assert result["stocks"] == []
    assert result["reason_codes"] == ["historical_industry_enrichment_disallowed"]


def test_sector_current_path_uses_all_ten_disclosed_stocks_and_keeps_lineage(
    monkeypatch,
) -> None:
    from app.services import fund_holdings_sector_infer as service

    now = datetime.now(CN)
    snapshot = _client_snapshot()
    snapshot["decision_at"] = now.isoformat()
    monkeypatch.setattr(
        service,
        "resolve_fund_holdings_snapshot_at_decision",
        lambda *_args, **_kwargs: {
            "status": "qualified",
            "reason_codes": [],
            "decision_at": now.isoformat(),
            "source": "append_only_store",
            "snapshot": snapshot,
        },
    )
    monkeypatch.setattr(
        service,
        "_fetch_current_industries",
        lambda rows, **_kwargs: {
            str(row["security_code"]): "半导体" for row in rows
        },
    )

    detailed = fetch_portfolio_stocks_with_industry_evidence("000001")
    legacy = fetch_portfolio_stocks_with_industry("000001")
    assert detailed["status"] == "qualified"
    assert len(detailed["stocks"]) == 10
    assert len(legacy) == 10
    inferred = infer_sector_from_portfolio_stocks("000001", detailed["stocks"])
    assert inferred is not None
    _sector, _scores, evidence = inferred
    assert all(row["snapshot_hash"] == "a" * 64 for row in evidence)
    assert all(row["report_period"] == "2026-Q2" for row in evidence)
    assert all(row["coverage"]["coverage_ratio"] == 0.3 for row in evidence)
    assert all(row["industry_pit_qualified"] is False for row in evidence)
    assert detailed["sector_clue"]["sector_name"] == "半导体"
    assert detailed["qualification"]["research_clue_available"] is True
    assert detailed["qualification"]["sector_inference_eligible"] is False
    assert detailed["qualification"]["research_only"] is True
    assert "industry_evidence_not_pit_qualified" in detailed["qualification"][
        "reason_codes"
    ]


def test_sector_current_first_observed_industry_can_drive_primary_sector(
    monkeypatch,
) -> None:
    from app.services import fund_holdings_sector_infer as service

    now = datetime.now(CN)
    snapshot = _client_snapshot()
    monkeypatch.setattr(
        service,
        "resolve_fund_holdings_snapshot_at_decision",
        lambda *_args, **_kwargs: {
            "status": "qualified",
            "reason_codes": [],
            "decision_at": now.isoformat(),
            "source": "append_only_store",
            "snapshot": snapshot,
        },
    )
    observed_at = now.isoformat()
    monkeypatch.setattr(
        service,
        "_fetch_current_industries",
        lambda rows, **_kwargs: {
            str(row["security_code"]): {
                "value": "半导体",
                "available_at": observed_at,
                "source": "eastmoney_push2_stock_get_f127",
                "ref_id": f"ref-{row['security_code']}",
                "pit_qualified": True,
            }
            for row in rows
        },
    )

    result = fetch_portfolio_stocks_with_industry_evidence("000001")
    assert result["status"] == "qualified"
    assert result["sector_clue"]["sector_name"] == "半导体"
    assert result["qualification"]["sector_inference_eligible"] is True
    assert result["qualification"]["classification_pit_qualified"] is True
    assert result["qualification"]["research_only"] is False
    assert result["association_evaluated_at"] is not None


def test_sector_refines_semiconductor_equipment_portfolio_to_materials_equipment(
    monkeypatch,
) -> None:
    from app.services import fund_holdings_sector_infer as service

    observed_at = datetime.now(CN).isoformat()
    rows = [
        {"security_code": "688409", "security_name": "富创精密", "weight_percent": 9.34},
        {"security_code": "688120", "security_name": "华海清科", "weight_percent": 9.22},
        {"security_code": "688012", "security_name": "中微公司", "weight_percent": 9.06},
        {"security_code": "002371", "security_name": "北方华创", "weight_percent": 9.03},
    ]
    broad = {
        row["security_code"]: {
            "value": "半导体",
            "available_at": observed_at,
            "source": "eastmoney_push2_stock_get_f127",
            "ref_id": f"industry-{row['security_code']}",
            "pit_qualified": True,
        }
        for row in rows
    }
    board_rows = {
        "BK1325": {
            "codes": ["300666"],
            "available_at": observed_at,
            "source": "eastmoney_push2_clist_board_members",
            "ref_id": "material-members",
            "pit_qualified": True,
        },
        "BK1326": {
            "codes": [row["security_code"] for row in rows],
            "available_at": observed_at,
            "source": "eastmoney_push2_clist_board_members",
            "ref_id": "equipment-members",
            "pit_qualified": True,
        },
    }
    monkeypatch.setattr(
        service,
        "fetch_current_board_constituent_evidence",
        lambda _codes, **_kwargs: board_rows,
    )

    refined = service._refine_current_portfolio_themes(
        rows,
        broad,
        force_refresh=False,
    )
    assert {row["theme"] for row in refined.values()} == {"半导体材料"}
    detail = refined["688409"]["theme_detail"]
    assert detail["matched_stock_count"] == 4
    assert detail["matched_weight_ratio"] == 1.0
    assert refined["688409"]["theme_pit_qualified"] is True


def test_qdii_adapter_marks_stale_snapshot_unqualified_and_fetched_at_aware(
    monkeypatch,
) -> None:
    from app.services import us_qdii_holdings_client as client

    snapshot = _client_snapshot(freshness="stale")
    snapshot["holdings"] = [
        {
            "security_code": "NVDA",
            "security_name": "NVIDIA",
            "weight_percent": 8.0,
            "rank": 1,
        },
        {
            "security_code": "00700",
            "security_name": "Tencent",
            "weight_percent": 5.0,
            "rank": 2,
        },
    ]
    monkeypatch.setattr(
        client,
        "resolve_fund_holdings_snapshot_at_decision",
        lambda *_args, **_kwargs: {
            "status": "qualified",
            "reason_codes": [],
            "source": "append_only_store",
            "snapshot": snapshot,
            "record": {"first_observed_at": "2026-07-14T03:00:00+00:00"},
        },
    )

    payload = client.get_fund_holdings("000001")
    assert payload is not None
    assert payload["status"] == "stale"
    assert payload["qualified"] is False
    assert payload["qualification"]["nowcast_eligible"] is False
    assert {row["market"] for row in payload["holdings"]} == {"us", "hk"}
    fetched_at = datetime.fromisoformat(payload["fetched_at"])
    assert fetched_at.tzinfo is not None and fetched_at.utcoffset() is not None


def test_qdii_missing_snapshot_is_none_not_zero(monkeypatch) -> None:
    from app.services import us_qdii_holdings_client as client

    monkeypatch.setattr(
        client,
        "resolve_fund_holdings_snapshot_at_decision",
        lambda *_args, **_kwargs: {
            "status": "unavailable",
            "qualified": False,
            "reason_codes": ["announcement_records_missing"],
            "snapshot": None,
        },
    )
    assert client.get_fund_holdings("000001") is None


def test_qdii_missing_coverage_is_explicitly_unqualified(monkeypatch) -> None:
    from app.services import us_qdii_holdings_client as client

    snapshot = _client_snapshot()
    snapshot.pop("coverage")
    monkeypatch.setattr(
        client,
        "resolve_fund_holdings_snapshot_at_decision",
        lambda *_args, **_kwargs: {
            "status": "qualified",
            "reason_codes": [],
            "source": "append_only_store",
            "snapshot": snapshot,
            "record": {"first_observed_at": "2026-07-14T03:00:00+00:00"},
        },
    )
    payload = client.get_fund_holdings("000001")
    assert payload is not None
    assert payload["status"] == "unavailable"
    assert payload["qualified"] is False
    assert payload["coverage"] is None
    assert payload["reason_codes"] == ["holdings_coverage_unknown"]


def test_small_quoted_sleeve_is_contribution_not_normalized_fund_return() -> None:
    holdings = [
        {"market": "us", "code": "AAA", "weight": 10.0},
        {"market": "us", "code": "BBB", "weight": 5.0},
    ]
    quotes = {"us:AAA": 10.0, "us:BBB": 10.0}

    assert compute_holdings_reference(holdings, quotes) == 1.5

    payload = {
        "holdings": holdings,
        "coverage": {"portfolio_weight_coverage_percent": 15.0},
        "qualification": {
            "nowcast_eligible": False,
            "disclosed_contribution_research_eligible": True,
        },
    }
    assert build_holdings_reference_map({"000001": payload}, quotes) == {}
    assert build_disclosed_holdings_contribution_map(
        {"000001": payload}, quotes
    ) == {"000001": 1.5}

    payload["qualification"]["nowcast_eligible"] = True
    assert build_holdings_reference_map({"000001": payload}, quotes) == {
        "000001": 1.5
    }


def test_invalid_quote_or_missing_coverage_never_becomes_zero() -> None:
    holdings = [{"market": "us", "code": "AAA", "weight": 10.0}]
    assert compute_holdings_reference(holdings, {}) is None
    assert compute_holdings_reference(holdings, {"us:AAA": float("nan")}) is None
    assert compute_holdings_reference([], {"us:AAA": 1.0}) is None

    missing_coverage = {
        "holdings": holdings,
        "qualification": {"nowcast_eligible": True},
    }
    assert build_holdings_reference_map(
        {"000001": missing_coverage}, {"us:AAA": 1.0}
    ) == {}


def test_adapter_observation_fallback_timestamp_is_aware(monkeypatch) -> None:
    from app.services import us_qdii_holdings_client as client

    snapshot = _client_snapshot()
    snapshot["holdings"] = [
        {
            "security_code": "NVDA",
            "security_name": "NVIDIA",
            "weight_percent": 8.0,
        }
    ]
    monkeypatch.setattr(
        client,
        "resolve_fund_holdings_snapshot_at_decision",
        lambda *_args, **_kwargs: {
            "status": "qualified",
            "reason_codes": [],
            "source": "live_resolver_unpersisted",
            "snapshot": snapshot,
            "record": None,
        },
    )
    payload = client.get_fund_holdings("000001")
    assert payload is not None
    assert datetime.fromisoformat(payload["fetched_at"]).tzinfo is timezone.utc
