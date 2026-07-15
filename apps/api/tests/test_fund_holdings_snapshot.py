from __future__ import annotations

import base64
import hashlib
import json
import math
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import database
from app.db_migrations import run_migrations
from app.services.decision_clock import DecisionClock
from app.services.fund_holdings_snapshot import (
    HOLDINGS_SNAPSHOT_SCHEMA_VERSION,
    build_fund_holdings_snapshot,
    materialize_fund_holdings_snapshot_for_decision,
    resolve_fund_holdings_snapshot,
    validate_fund_holdings_snapshot_hash,
)
from app.services.trading_session import build_trading_session


CN = ZoneInfo("Asia/Shanghai")
DECISION_AT = datetime(2026, 8, 31, 12, 0, tzinfo=CN)


def _row(
    code: str,
    weight: object,
    *,
    period: str = "2026年2季度股票投资明细",
    name: str | None = None,
    rank: int | None = None,
    **extra: object,
) -> dict:
    row = {
        "股票代码": code,
        "股票名称": name or f"股票{code}",
        "占净值比例": weight,
        "季度": period,
    }
    if rank is not None:
        row["序号"] = rank
    row.update(extra)
    return row


def _announcement(
    title: str = "测试基金2026年第2季度报告",
    published_at: object = "2026-07-20",
    **extra: object,
) -> dict:
    row = {"公告标题": title, "公告日期": published_at}
    row.update(extra)
    return row


def _top10_rows(*, period: str = "2026年2季度股票投资明细") -> list[dict]:
    return [
        _row(f"{index:06d}", 3.0 - index / 10, period=period, rank=index)
        for index in range(1, 11)
    ]


def _full_rows() -> list[dict]:
    return [
        _row(f"{index:06d}", 2.0, rank=index)
        for index in range(1, 13)
    ]


def _build(rows: object, announcements: object, **kwargs: object) -> dict:
    return build_fund_holdings_snapshot(
        rows,
        announcements,
        fund_code="000001",
        decision_at=kwargs.pop("decision_at", DECISION_AT),
        **kwargs,
    )


def _eastmoney_raw_response(
    *,
    year: int,
    quarter: int,
    row_count: int,
    starred_ranks: set[int] | None = None,
    include_footnote: bool = True,
    include_rank_header: bool = True,
) -> bytes:
    starred = starred_ranks or set()
    rank_header = "<th>序号</th>" if include_rank_header else "<th>编号</th>"
    body_rows = []
    for rank in range(1, row_count + 1):
        raw_rank = f"{rank}{'*' if rank in starred else ''}"
        code = f"{600000 + rank:06d}"
        body_rows.append(
            "<tr>"
            f"<td>{raw_rank}</td><td>{code}</td><td>股票{rank}</td>"
            "<td>1.00%</td><td>10.00</td><td>100.00</td>"
            "</tr>"
        )
    footnote = (
        "<div>注：加*号代表进入上市公司的十大流通股东却没有进入单只基金前十大重仓股的个股。</div>"
        if include_footnote
        else ""
    )
    month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[quarter]
    content = (
        "<div class='box'><div class='boxitem'>"
        f"<h4 class='t'>测试基金 {year}年{quarter}季度股票投资明细 "
        f"截止至：{year}-{month_day}</h4>"
        "<table><thead><tr>"
        f"{rank_header}<th>股票代码</th><th>股票名称</th>"
        "<th>占净值比例</th><th>持股数（万股）</th>"
        "<th>持仓市值（万元）</th>"
        "</tr></thead><tbody>"
        f"{''.join(body_rows)}"
        "</tbody></table></div>"
        f"{footnote}</div>"
    )
    payload = (
        "var apidata={ content:"
        + json.dumps(content, ensure_ascii=False)
        + f",arryear:[{year}],curyear:{year}}};"
    )
    return payload.encode("utf-8")


def _transport_response(raw: bytes, *, year: str) -> dict:
    return {
        "year": year,
        "content_type": "text/html; charset=utf-8",
        "raw_response_bytes": len(raw),
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "body_base64": base64.b64encode(raw).decode("ascii"),
    }


def test_contract_accepts_timezone_aware_decision_clock() -> None:
    clock = DecisionClock(
        decision_at=DECISION_AT,
        session=build_trading_session(DECISION_AT),
    )
    result = build_fund_holdings_snapshot(
        _top10_rows(),
        [_announcement()],
        fund_code="1",
        decision_clock=clock,
    )

    assert result["schema_version"] == HOLDINGS_SNAPSHOT_SCHEMA_VERSION
    assert result["status"] == "qualified"
    assert result["fund_code"] == "000001"
    assert result["fund_master_key"] == "000001"
    assert result["decision_at"] == "2026-08-31T12:00:00+08:00"
    assert result["report_period"] == "2026-Q2"
    assert result["as_of_date"] == "2026-06-30"
    assert result["available_at"] == "2026-07-21T00:00:00+08:00"
    assert result["qualification"] == {
        "status": "qualified",
        "qualified": True,
        "valid_snapshot": True,
        "pit_eligible": True,
        "disclosure_scope_identified": True,
        "weight_validation_passed": True,
        "disclosed_overlap_lower_bound_eligible": True,
        "exact_full_portfolio_overlap_eligible": False,
        "current_holdings_inference_eligible": False,
        "nowcast_eligible": False,
        "reason_codes": [],
    }
    assert result["freshness"] == {
        "report_age_days": 62,
        "available_age_days": 41,
        "label": "fresh",
        "fresh_report_max_age_days": 120,
        "aging_report_max_age_days": 210,
        "stale_blocks_valid_snapshot": False,
        "stale_blocks_disclosed_overlap_use": True,
        "decision_relative_not_snapshot_identity": True,
    }


def test_naive_decision_time_and_clock_mismatch_fail_closed() -> None:
    naive = _build(
        _top10_rows(),
        [_announcement()],
        decision_at=datetime(2026, 8, 31, 12, 0),
    )
    assert naive["status"] == "invalid"
    assert "decision_at_timezone_required" in naive["reason_codes"]

    clock = DecisionClock(
        decision_at=DECISION_AT,
        session=build_trading_session(DECISION_AT),
    )
    mismatch = build_fund_holdings_snapshot(
        _top10_rows(),
        [_announcement()],
        fund_code="000001",
        decision_at=datetime(2026, 8, 31, 12, 1, tzinfo=CN),
        decision_clock=clock,
    )
    assert mismatch["status"] == "invalid"
    assert "decision_clock_mismatch" in mismatch["reason_codes"]


def test_all_year_rows_are_grouped_by_period_instead_of_head_across_year() -> None:
    q1_rows = [
        _row(
            f"600{index:03d}",
            1.0,
            period="2026年1季度股票投资明细",
            rank=index,
        )
        for index in range(1, 11)
    ]
    q2_rows = _top10_rows()
    result = _build(
        [*q1_rows, *q2_rows],
        [
            _announcement("测试基金2026年第1季度报告", "2026-04-20"),
            _announcement(),
        ],
    )

    assert result["status"] == "qualified"
    assert result["report_period"] == "2026-Q2"
    assert {row["security_code"] for row in result["holdings"]} == {
        f"{index:06d}" for index in range(1, 11)
    }


def test_date_only_publication_is_available_only_next_shanghai_midnight() -> None:
    before = _build(
        _top10_rows(),
        [_announcement(published_at="2026-07-20")],
        decision_at=datetime(2026, 7, 20, 23, 59, 59, tzinfo=CN),
    )
    at_boundary = _build(
        _top10_rows(),
        [_announcement(published_at="2026-07-20")],
        decision_at=datetime(2026, 7, 21, 0, 0, tzinfo=CN),
    )

    assert before["status"] == "unavailable"
    assert "announcement_after_decision" in before["reason_codes"]
    assert at_boundary["status"] == "qualified"
    assert at_boundary["available_at"] == "2026-07-21T00:00:00+08:00"


def test_timezone_timestamp_is_normalized_and_not_delayed() -> None:
    result = _build(
        _top10_rows(),
        [_announcement(published_at="2026-07-20T16:30:00Z")],
        decision_at=datetime(2026, 7, 21, 0, 31, tzinfo=CN),
    )

    assert result["status"] == "qualified"
    assert result["available_at"] == "2026-07-21T00:30:00+08:00"


def test_future_period_and_announcement_are_filtered_before_selection() -> None:
    q3_rows = _top10_rows(period="2026年3季度股票投资明细")
    q1_rows = _top10_rows(period="2026年1季度股票投资明细")
    decision = datetime(2026, 7, 15, 12, 0, tzinfo=CN)
    result = _build(
        [*q3_rows, *q1_rows],
        [
            _announcement("测试基金2026年第3季度报告", "2026-10-20"),
            _announcement("测试基金2026年第1季度报告", "2026-04-20"),
        ],
        decision_at=decision,
    )

    assert result["status"] == "qualified"
    assert result["report_period"] == "2026-Q1"
    assert result["audit"]["future_rows_dropped"] == 10


def test_newer_period_with_future_announcement_falls_back_to_older_pit_snapshot() -> None:
    result = _build(
        [
            *_top10_rows(),
            *_top10_rows(period="2026年1季度股票投资明细"),
        ],
        [
            _announcement(published_at="2026-07-20"),
            _announcement("测试基金2026年第1季度报告", "2026-04-20"),
        ],
        decision_at=datetime(2026, 7, 20, 10, 0, tzinfo=CN),
    )

    assert result["status"] == "qualified"
    assert result["report_period"] == "2026-Q1"
    assert result["audit"]["future_announcements_dropped"] == 1
    assert result["audit"]["newer_periods_rejected"] == [
        {
            "report_period": "2026-Q2",
            "reason_codes": ["announcement_after_decision"],
        }
    ]


def test_q1_q3_are_explicit_top10_and_cannot_contain_more_than_ten_rows() -> None:
    q3 = _top10_rows(period="2025年3季度") + [
        _row("601111", 1, period="2025年3季度", rank=11)
    ]
    result = _build(
        q3,
        [_announcement("测试基金2025年第3季度报告", "2025-10-20")],
    )

    assert result["status"] == "invalid"
    assert "quarterly_top10_row_count_exceeded" in result["reason_codes"]


def test_raw_eastmoney_parser_excludes_starred_issuer_inference_before_build() -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(
        year=2026,
        quarter=1,
        row_count=12,
        starred_ranks={11, 12},
    )
    parsed = module._parse_eastmoney_portfolio_response(
        raw,
        fund_code="000001",
        expected_year="2026",
        content_type="text/html; charset=utf-8",
    )
    provider_payload = module._eastmoney_provider_payload(
        years=["2026"],
        parsed_responses=[parsed],
    )
    result = resolve_fund_holdings_snapshot(
        "000001",
        decision_at=DECISION_AT,
        portfolio_rows=provider_payload,
        announcement_records=[
            _announcement("测试基金2026年第1季度报告", "2026-04-20")
        ],
    )

    assert result["status"] == "qualified"
    assert result["source"] == {
        "provider": "akshare.fund_portfolio_hold_em",
        "dataset": "fund_portfolio_hold_em",
        "availability_basis": "matched_report_announcement_only",
    }
    assert result["report_period"] == "2026-Q1"
    assert result["coverage"]["disclosed_holding_count"] == 10
    assert {item["security_code"] for item in result["holdings"]} == {
        f"{600000 + rank:06d}" for rank in range(1, 11)
    }
    provider_audit = result["audit"]["portfolio_provider_raw_validation"]
    assert provider_audit["issuer_shareholder_inference"] == {
        "source_kind": "issuer_shareholder_inference",
        "excluded_row_count": 2,
        "reason_codes": ["eastmoney_issuer_shareholder_inference_excluded"],
        "inherits_fund_report_available_at": False,
        "participates_in_fund_disclosure": False,
        "participates_in_coverage_overlap_or_sector_inference": False,
    }
    raw_ref = next(
        item
        for item in result["source_refs"]
        if item["kind"] == "fund_holdings_raw_response_validation"
    )
    assert raw_ref["fund_disclosure_row_count"] == 10
    assert raw_ref["issuer_shareholder_inference_excluded_count"] == 2
    assert raw_ref["inherits_fund_report_available_at"] is False
    assert raw_ref["participates_in_fund_disclosure"] is False
    assert "available_at" not in raw_ref
    serialized_evidence = json.dumps(
        {"audit": provider_audit, "source_ref": raw_ref},
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "600011" not in serialized_evidence
    assert "600012" not in serialized_evidence
    assert "股票11" not in serialized_evidence
    assert "股票12" not in serialized_evidence


def test_raw_eastmoney_parser_accepts_unstarred_response_without_footnote() -> None:
    """Eastmoney omits the note when a response has no starred inference rows."""

    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(
        year=2026,
        quarter=1,
        row_count=10,
        starred_ranks=set(),
        include_footnote=False,
    )
    parsed = module._parse_eastmoney_portfolio_response(
        raw,
        fund_code="110020",
        expected_year="2026",
        content_type="text/html; charset=utf-8",
    )
    provider_payload = module._eastmoney_provider_payload(
        years=["2026"],
        parsed_responses=[parsed],
    )
    result = build_fund_holdings_snapshot(
        provider_payload,
        [_announcement("测试基金2026年第1季度报告", "2026-04-20")],
        fund_code="110020",
        decision_at=DECISION_AT,
        source="eastmoney.FundArchivesDatas.jjcc_raw",
    )

    assert len(parsed["rows"]) == 10
    assert len(parsed["response_evidence"]["fund_disclosure_sha256"]) == 64
    assert provider_payload["provider_audit"]["status"] == "qualified"
    assert result["status"] == "qualified"
    assert result["coverage"]["disclosed_holding_count"] == 10


def test_raw_eastmoney_parser_rejects_semantically_changed_star_footnote() -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(
        year=2026,
        quarter=1,
        row_count=11,
        starred_ranks={11},
    ).replace(
        "却没有进入单只基金前十大重仓股".encode(),
        "并且进入单只基金前十大重仓股".encode(),
    )

    with pytest.raises(module._EastmoneyPortfolioParseError) as captured:
        module._parse_eastmoney_portfolio_response(
            raw,
            fund_code="000001",
            expected_year="2026",
            content_type="text/html; charset=utf-8",
        )

    assert captured.value.reason_code == "eastmoney_holdings_star_footnote_missing"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("股票代码", "699999"),
        ("占净值比例", "9.99%"),
    ],
)
def test_provider_audit_commitment_rejects_same_count_row_tampering(
    field: str,
    replacement: str,
) -> None:
    from app.services import fund_holdings_snapshot as module

    parsed = module._parse_eastmoney_portfolio_response(
        _eastmoney_raw_response(year=2026, quarter=1, row_count=10),
        fund_code="000001",
        expected_year="2026",
        content_type="text/html; charset=utf-8",
    )
    provider_payload = module._eastmoney_provider_payload(
        years=["2026"],
        parsed_responses=[parsed],
    )
    tampered = deepcopy(provider_payload)
    tampered["rows"][0][field] = replacement

    original = _build(
        provider_payload,
        [_announcement("测试基金2026年第1季度报告", "2026-04-20")],
    )
    rejected = _build(
        tampered,
        [_announcement("测试基金2026年第1季度报告", "2026-04-20")],
    )

    assert original["status"] == "qualified"
    assert rejected["status"] == "unavailable"
    assert rejected["reason_codes"] == [
        "eastmoney_holdings_provider_audit_invalid"
    ]
    assert rejected["holdings"] == []


def test_default_live_and_custom_portfolio_providers_keep_distinct_provenance(
    monkeypatch,
) -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(year=2026, quarter=1, row_count=10)
    parsed = module._parse_eastmoney_portfolio_response(
        raw,
        fund_code="000001",
        expected_year="2026",
        content_type="text/html; charset=utf-8",
    )
    provider_payload = module._eastmoney_provider_payload(
        years=["2026"],
        parsed_responses=[parsed],
    )
    announcements = [
        _announcement("测试基金2026年第1季度报告", "2026-04-20")
    ]
    decision = datetime.now(CN)

    monkeypatch.setattr(
        module,
        "_default_portfolio_rows_fetcher",
        lambda *_args, **_kwargs: provider_payload,
    )
    default_live = resolve_fund_holdings_snapshot(
        "000001",
        decision_at=decision,
        announcement_records=announcements,
    )

    def custom_provider(*_args: object, **_kwargs: object) -> dict:
        return provider_payload

    injected = resolve_fund_holdings_snapshot(
        "000001",
        decision_at=DECISION_AT,
        fetch_portfolio_rows=custom_provider,
        announcement_records=announcements,
    )

    assert default_live["status"] == "qualified"
    assert default_live["source"]["provider"] == (
        "eastmoney.FundArchivesDatas.jjcc_raw"
    )
    assert default_live["source"]["dataset"] == (
        "FundArchivesDatas.aspx:type=jjcc_raw"
    )
    assert injected["status"] == "qualified"
    assert injected["source"]["provider"] == "akshare.fund_portfolio_hold_em"
    assert injected["source"]["dataset"] == "fund_portfolio_hold_em"


def test_raw_eastmoney_source_label_requires_bound_provider_audit() -> None:
    result = _build(
        _top10_rows(period="2026年1季度股票投资明细"),
        [_announcement("测试基金2026年第1季度报告", "2026-04-20")],
        source="eastmoney.FundArchivesDatas.jjcc_raw",
    )

    assert result["status"] == "unavailable"
    assert result["reason_codes"] == [
        "eastmoney_holdings_provider_audit_missing"
    ]
    assert result["holdings"] == []


@pytest.mark.parametrize(
    ("fixture_kwargs", "reason_code"),
    [
        (
            {
                "year": 2026,
                "quarter": 1,
                "row_count": 11,
                "starred_ranks": {11},
                "include_footnote": False,
            },
            "eastmoney_holdings_star_footnote_missing",
        ),
        (
            {
                "year": 2026,
                "quarter": 1,
                "row_count": 10,
                "include_rank_header": False,
            },
            "eastmoney_holdings_html_header_invalid",
        ),
    ],
)
def test_raw_eastmoney_parser_fails_closed_on_marker_contract_conflict(
    fixture_kwargs: dict,
    reason_code: str,
) -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(**fixture_kwargs)
    with pytest.raises(module._EastmoneyPortfolioParseError) as captured:
        module._parse_eastmoney_portfolio_response(
            raw,
            fund_code="000001",
            expected_year="2026",
            content_type="text/html; charset=utf-8",
        )

    assert captured.value.reason_code == reason_code
    assert captured.value.raw_response_sha256 == hashlib.sha256(raw).hexdigest()


def test_unmarked_q1_extra_rows_are_rejected_instead_of_guessed() -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(
        year=2026,
        quarter=1,
        row_count=12,
        starred_ranks=set(),
    )
    parsed = module._parse_eastmoney_portfolio_response(
        raw,
        fund_code="000001",
        expected_year="2026",
        content_type="text/html; charset=utf-8",
    )
    provider_payload = module._eastmoney_provider_payload(
        years=["2026"],
        parsed_responses=[parsed],
    )
    result = _build(
        provider_payload,
        [_announcement("测试基金2026年第1季度报告", "2026-04-20")],
    )

    assert result["status"] == "invalid"
    assert "quarterly_top10_row_count_exceeded" in result["reason_codes"]
    assert (
        result["audit"]["portfolio_provider_raw_validation"]
        ["issuer_shareholder_inference"]["excluded_row_count"]
        == 0
    )


def test_issuer_shareholder_inference_row_cannot_enter_disclosure_builder() -> None:
    result = _build(
        [
            _row(
                "600001",
                1,
                period="2026年1季度股票投资明细",
                disclosure_source_kind="issuer_shareholder_inference",
            )
        ],
        [_announcement("测试基金2026年第1季度报告", "2026-04-20")],
    )

    assert result["status"] == "invalid"
    assert result["reason_codes"] == [
        "portfolio_row_source_kind_not_fund_disclosure"
    ]
    assert result["holdings"] == []


def test_raw_q4_full_portfolio_is_not_truncated_to_ten() -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(
        year=2025,
        quarter=4,
        row_count=12,
        starred_ranks=set(),
    )
    parsed = module._parse_eastmoney_portfolio_response(
        raw,
        fund_code="000001",
        expected_year="2025",
        content_type="text/html; charset=utf-8",
    )
    provider_payload = module._eastmoney_provider_payload(
        years=["2025"],
        parsed_responses=[parsed],
    )
    result = _build(
        provider_payload,
        [_announcement("测试基金2025年年度报告", "2026-03-28")],
    )

    assert result["status"] == "qualified"
    assert result["scope"]["kind"] == "full_portfolio"
    assert result["coverage"]["disclosed_holding_count"] == 12
    raw_ref = next(
        item
        for item in result["source_refs"]
        if item["kind"] == "fund_holdings_raw_response_validation"
    )
    assert raw_ref["fund_disclosure_row_count"] == 12
    assert raw_ref["issuer_shareholder_inference_excluded_count"] == 0


def test_raw_parser_rejects_abnormal_content_type_and_charset() -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(year=2026, quarter=1, row_count=10)
    for content_type, expected in (
        ("application/json; charset=utf-8", "eastmoney_holdings_content_type_invalid"),
        ("text/html; charset=gbk", "eastmoney_holdings_charset_invalid"),
    ):
        with pytest.raises(module._EastmoneyPortfolioParseError) as captured:
            module._parse_eastmoney_portfolio_response(
                raw,
                fund_code="000001",
                expected_year="2026",
                content_type=content_type,
            )
        assert captured.value.reason_code == expected


def test_raw_parser_rejects_oversized_response_before_html_parsing(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(year=2026, quarter=1, row_count=10)
    monkeypatch.setattr(module, "_EASTMONEY_MAX_RESPONSE_BYTES", len(raw) - 1)

    with pytest.raises(module._EastmoneyPortfolioParseError) as captured:
        module._parse_eastmoney_portfolio_response(
            raw,
            fund_code="000001",
            expected_year="2026",
            content_type="text/html; charset=utf-8",
        )

    assert captured.value.reason_code == "eastmoney_holdings_response_too_large"
    assert captured.value.raw_response_sha256 == hashlib.sha256(raw).hexdigest()


def test_normalized_input_hash_commits_to_the_original_star_semantics() -> None:
    from app.services import fund_holdings_snapshot as module

    starred = module._parse_eastmoney_portfolio_response(
        _eastmoney_raw_response(
            year=2026,
            quarter=1,
            row_count=11,
            starred_ranks={11},
        ),
        fund_code="000001",
        expected_year="2026",
        content_type="text/html; charset=utf-8",
    )
    unstarred = module._parse_eastmoney_portfolio_response(
        _eastmoney_raw_response(
            year=2026,
            quarter=1,
            row_count=11,
            starred_ranks=set(),
        ),
        fund_code="000001",
        expected_year="2026",
        content_type="text/html; charset=utf-8",
    )

    starred_evidence = starred["response_evidence"]
    unstarred_evidence = unstarred["response_evidence"]
    assert starred_evidence["normalized_input_sha256"] != (
        unstarred_evidence["normalized_input_sha256"]
    )
    assert starred_evidence["periods"][0]["normalized_input_sha256"] != (
        unstarred_evidence["periods"][0]["normalized_input_sha256"]
    )
    assert starred_evidence["issuer_shareholder_inference_excluded_count"] == 1
    assert unstarred_evidence["issuer_shareholder_inference_excluded_count"] == 0


def test_q2_later_expanded_rows_are_not_backfilled_before_full_report() -> None:
    result = _build(
        _full_rows(),
        [
            _announcement("测试基金2026年第2季度报告", "2026-07-20"),
            _announcement("测试基金2026年中期报告", "2026-08-28"),
        ],
        decision_at=datetime(2026, 7, 25, 12, 0, tzinfo=CN),
    )

    assert result["status"] == "invalid"
    assert (
        "later_expanded_rows_without_full_report_availability"
        in result["reason_codes"]
    )
    assert result["holdings"] == []


def test_q2_full_rows_qualify_only_after_primary_semiannual_report() -> None:
    result = _build(
        _full_rows(),
        [
            _announcement("测试基金2026年第2季度报告", "2026-07-20"),
            _announcement(
                "测试基金2026年中期报告",
                "2026-08-28T18:00:00+08:00",
                report_id="full-2026-h1",
                url="https://example.test/full",
            ),
        ],
    )

    assert result["status"] == "qualified"
    assert result["scope"]["kind"] == "full_portfolio"
    assert result["scope"]["completeness"] == "full"
    assert result["coverage"]["disclosed_holding_count"] == 12
    assert result["coverage"]["is_complete_security_list"] is True
    assert result["coverage"]["is_complete_fund_portfolio"] is False
    assert result["available_at"] == "2026-08-28T18:00:00+08:00"


def test_q2_q4_ten_rows_with_both_report_scopes_is_ambiguous() -> None:
    result = _build(
        _top10_rows(),
        [
            _announcement("测试基金2026年第2季度报告", "2026-07-20"),
            _announcement("测试基金2026年中期报告", "2026-08-28"),
        ],
    )

    assert result["status"] == "invalid"
    assert "q2_q4_disclosure_scope_ambiguous" in result["reason_codes"]


def test_q4_annual_full_scope_and_explicit_top10_limit_are_distinct() -> None:
    annual_rows = [
        _row(
            f"{index:06d}",
            2,
            period="2025年4季度股票投资明细",
            rank=index,
        )
        for index in range(1, 13)
    ]
    annual = _build(
        annual_rows,
        [_announcement("测试基金2025年年度报告", "2026-03-28")],
    )
    assert annual["status"] == "qualified"
    assert annual["report_period"] == "2025-Q4"
    assert annual["scope"]["kind"] == "full_portfolio"

    explicit_top10 = [
        {**row, "disclosure_scope": "top10"} for row in annual_rows
    ]
    invalid = _build(
        explicit_top10,
        [_announcement("测试基金2025年第4季度报告", "2026-01-20")],
    )
    assert invalid["status"] == "invalid"
    assert "top10_disclosure_row_count_exceeded" in invalid["reason_codes"]


def test_primary_and_summary_same_release_keep_all_source_identifiers() -> None:
    announcements = [
        _announcement(
            "测试基金2026年中期报告",
            "2026-08-28T18:00:00+08:00",
            report_id="full-id",
            url="https://example.test/full",
            vendor_trace="trace-full",
        ),
        _announcement(
            "测试基金2026年中期报告摘要",
            "2026-08-28T18:00:00+08:00",
            report_id="summary-id",
            url="https://example.test/summary",
            vendor_trace="trace-summary",
        ),
    ]
    result = _build(_full_rows(), announcements)

    assert result["status"] == "qualified"
    assert {ref["announcement_id"] for ref in result["source_refs"]} == {
        "full-id",
        "summary-id",
    }
    assert {
        ref["raw_fields"]["vendor_trace"] for ref in result["source_refs"]
    } == {"trace-full", "trace-summary"}


def test_missing_or_ambiguous_announcement_fails_closed() -> None:
    missing = _build(_top10_rows(), [])
    assert missing["status"] == "unavailable"
    assert missing["reason_codes"] == ["announcement_records_missing"]

    ambiguous = _build(
        _top10_rows(),
        [
            _announcement(published_at="2026-07-20T10:00:00+08:00", id="a"),
            _announcement(published_at="2026-07-20T11:00:00+08:00", id="b"),
        ],
    )
    assert ambiguous["status"] == "invalid"
    assert "supporting_report_announcement_ambiguous" in ambiguous["reason_codes"]


@pytest.mark.parametrize(
    ("weight", "reason"),
    [
        (math.nan, "holding_weight_invalid"),
        (math.inf, "holding_weight_invalid"),
        (-0.1, "holding_weight_negative"),
        (100.1, "holding_weight_above_100"),
        (True, "holding_weight_invalid"),
    ],
)
def test_invalid_weights_fail_closed(weight: object, reason: str) -> None:
    result = _build([_row("000001", weight)], [_announcement()])

    assert result["status"] == "invalid"
    assert reason in result["reason_codes"]


def test_weight_total_uses_explicit_tolerance() -> None:
    accepted = _build(
        [_row("000001", 50), _row("000002", 50.01)],
        [_announcement()],
        weight_tolerance=0.01,
    )
    rejected = _build(
        [_row("000001", 50), _row("000002", 50.02)],
        [_announcement()],
        weight_tolerance=0.01,
    )

    assert accepted["status"] == "qualified"
    assert rejected["status"] == "invalid"
    assert "holding_weight_sum_above_100" in rejected["reason_codes"]


def test_conflicting_duplicate_security_is_invalid_but_exact_row_is_collapsed() -> None:
    conflict = _build(
        [_row("000001", 5), _row("000001", 6)],
        [_announcement()],
    )
    assert conflict["status"] == "invalid"
    assert "holding_duplicate_conflict" in conflict["reason_codes"]

    repeated = _row("000001", 5, rank=1)
    exact = _build([repeated, dict(repeated)], [_announcement()])
    assert exact["status"] == "qualified"
    assert len(exact["holdings"]) == 1
    assert exact["audit"]["exact_duplicate_rows_collapsed"] == 1


def test_hashes_ignore_input_order_fetch_time_and_unverified_family_merge() -> None:
    rows = _top10_rows()
    announcements = [
        _announcement(report_id="q2", vendor_column="preserved")
    ]
    first = _build(
        rows,
        announcements,
        fetched_at="2026-08-31T12:01:00+08:00",
        family_hint={
            "fund_master_key": "family-000001",
            "related_codes": ["000002", "000001"],
            "verified": True,
            "basis": "name suffix guess",
        },
    )
    second = _build(
        list(reversed(rows)),
        list(reversed(announcements)),
        fetched_at="2026-09-01T09:00:00+08:00",
        family_hint={
            "fund_master_key": "family-000001",
            "related_codes": ["000001", "000002"],
            "verified": True,
            "basis": "name suffix guess",
        },
    )

    assert first["source_hash"] == second["source_hash"]
    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert len(first["snapshot_hash"]) == 64
    assert first["snapshot_hash"] == first["snapshot_hash"].lower()
    assert first["fund_master_key"] == "000001"
    assert first["family_hint"]["status"] == "unverified_hint"
    assert first["family_hint"]["verified"] is False
    assert first["family_hint"]["hard_merge_applied"] is False
    assert first["audit"]["fetched_at"] != second["audit"]["fetched_at"]


def test_stale_is_explicit_but_does_not_change_validity_or_snapshot_identity() -> None:
    fresh = _build(
        _top10_rows(),
        [_announcement()],
        decision_at=datetime(2026, 8, 31, 12, 0, tzinfo=CN),
    )
    stale = _build(
        _top10_rows(),
        [_announcement()],
        decision_at=datetime(2027, 2, 1, 12, 0, tzinfo=CN),
    )

    assert fresh["freshness"]["label"] == "fresh"
    assert stale["freshness"]["label"] == "stale"
    assert stale["freshness"]["report_age_days"] == 216
    assert stale["status"] == "qualified"
    assert stale["qualification"]["valid_snapshot"] is True
    assert stale["qualification"]["disclosed_overlap_lower_bound_eligible"] is False
    assert stale["qualification"]["current_holdings_inference_eligible"] is False
    assert stale["snapshot_hash"] == fresh["snapshot_hash"]


def test_fetched_at_never_substitutes_for_missing_publication_time() -> None:
    result = _build(
        _top10_rows(),
        [{"公告标题": "测试基金2026年第2季度报告"}],
        fetched_at="2026-08-31T11:59:00+08:00",
    )

    assert result["status"] == "invalid"
    assert "announcement_available_at_invalid" in result["reason_codes"]
    assert result["available_at"] is None


def test_row_level_future_availability_is_filtered() -> None:
    future_rows = [
        _row(
            "000001",
            5,
            disclosed_at="2026-09-01T10:00:00+08:00",
        )
    ]
    result = _build(future_rows, [_announcement()])

    assert result["status"] == "unavailable"
    assert result["reason_codes"] == ["all_portfolio_rows_after_decision"]
    assert result["audit"]["future_rows_dropped"] == 1


def test_row_availability_is_hashed_and_controls_historical_replay() -> None:
    row = _row(
        "000001",
        5,
        disclosed_at="2026-08-01T10:00:00+08:00",
    )
    snapshot = _build(
        [row],
        [_announcement()],
        decision_at=datetime(2026, 8, 5, 12, 0, tzinfo=CN),
    )
    changed = _build(
        [{**row, "disclosed_at": "2026-08-04T10:00:00+08:00"}],
        [_announcement()],
        decision_at=datetime(2026, 8, 5, 12, 0, tzinfo=CN),
    )
    replay = materialize_fund_holdings_snapshot_for_decision(
        snapshot,
        decision_at=datetime(2026, 7, 25, 12, 0, tzinfo=CN),
    )

    assert snapshot["available_at"] == "2026-08-01T10:00:00+08:00"
    assert snapshot["source"]["availability_basis"] == (
        "max_of_matched_report_and_explicit_row_availability"
    )
    row_ref = next(
        ref for ref in snapshot["source_refs"] if ref["kind"] == "portfolio_row_availability"
    )
    assert row_ref["row_count"] == 1
    assert len(row_ref["availability_hash"]) == 64
    assert snapshot["snapshot_hash"] != changed["snapshot_hash"]
    assert replay["status"] == "unavailable"
    assert replay["reason_codes"] == ["snapshot_after_decision"]
    assert validate_fund_holdings_snapshot_hash(replay) is True


def test_future_row_rejects_the_entire_period_instead_of_building_a_hybrid() -> None:
    result = _build(
        [
            _row("000001", 5, disclosed_at="2026-07-21T00:00:00+08:00"),
            _row("000002", 4, disclosed_at="2026-09-01T00:00:00+08:00"),
            *_top10_rows(period="2026年1季度股票投资明细"),
        ],
        [
            _announcement(),
            _announcement("测试基金2026年第1季度报告", "2026-04-20"),
        ],
    )

    assert result["status"] == "qualified"
    assert result["report_period"] == "2026-Q1"
    assert result["audit"]["newer_periods_rejected"] == [
        {
            "report_period": "2026-Q2",
            "reason_codes": ["portfolio_period_revision_after_decision"],
        }
    ]


def test_historical_replay_refuses_default_live_providers(monkeypatch) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("historical replay must not touch default live providers")

    monkeypatch.setattr(
        "app.services.fund_holdings_snapshot._default_portfolio_rows_fetcher",
        unexpected,
    )
    monkeypatch.setattr(
        "app.services.fund_holdings_snapshot._default_announcements_fetcher",
        unexpected,
    )
    result = resolve_fund_holdings_snapshot(
        "000001",
        decision_at=datetime(2025, 7, 1, 12, 0, tzinfo=CN),
    )

    assert result["status"] == "unavailable"
    assert result["reason_codes"] == ["historical_live_fetch_disallowed"]


def test_injected_providers_allow_historical_replay_and_still_apply_pit() -> None:
    calls: list[tuple[str, object]] = []

    def portfolio_provider(code: str, *, years: list[str], decision_at: datetime):
        calls.append(("portfolio", (code, years, decision_at)))
        return _top10_rows(period="2025年1季度")

    def announcement_provider(code: str, *, limit: int, decision_at: datetime):
        calls.append(("announcement", (code, limit, decision_at)))
        return [
            _announcement("测试基金2025年第1季度报告", "2025-04-20"),
            _announcement("测试基金2025年第2季度报告", "2025-07-20"),
        ]

    decision = datetime(2025, 5, 1, 12, 0, tzinfo=CN)
    result = resolve_fund_holdings_snapshot(
        "000001",
        decision_at=decision,
        fetch_portfolio_rows=portfolio_provider,
        fetch_announcements=announcement_provider,
    )

    assert result["status"] == "qualified"
    assert result["report_period"] == "2025-Q1"
    assert [name for name, _details in calls] == ["portfolio", "announcement"]


def test_default_portfolio_provider_parses_raw_html_in_bounded_subprocess(
    monkeypatch,
) -> None:
    from app.services import fund_holdings_snapshot as module

    captured: dict[str, object] = {}
    raw_2025 = _eastmoney_raw_response(
        year=2025,
        quarter=4,
        row_count=12,
    )
    raw_2026 = _eastmoney_raw_response(
        year=2026,
        quarter=1,
        row_count=11,
        starred_ranks={11},
    )

    def runner(script: str, **kwargs: object) -> dict:
        captured["script"] = script
        captured.update(kwargs)
        return {
            "responses": [
                _transport_response(raw_2026, year="2026"),
                _transport_response(raw_2025, year="2025"),
            ]
        }

    monkeypatch.setattr(
        "app.services.akshare_subprocess.run_akshare_json_script",
        runner,
    )
    payload = module._default_portfolio_rows_fetcher(
        "000001",
        years=["2026", "2025"],
        decision_at=DECISION_AT,
    )

    assert len(payload["rows"]) == 22
    assert payload["provider_audit"]["status"] == "qualified"
    assert payload["provider_audit"]["requested_years"] == ["2025", "2026"]
    assert (
        payload["provider_audit"]["issuer_shareholder_inference"]
        ["excluded_row_count"]
        == 1
    )
    assert "FundArchivesDatas.aspx" in str(captured["script"])
    assert "fund_portfolio_hold_em" not in str(captured["script"])
    assert '"topline": "10000"' in str(captured["script"])
    assert captured["timeout"] == 45
    assert captured["warn_on_failure"] is False


def test_default_portfolio_provider_parse_error_is_unavailable(monkeypatch) -> None:
    from app.services import fund_holdings_snapshot as module

    raw = _eastmoney_raw_response(
        year=2026,
        quarter=1,
        row_count=11,
        starred_ranks={11},
        include_footnote=False,
    )

    def runner(_script: str, **_kwargs: object) -> dict:
        return {"responses": [_transport_response(raw, year="2026")]}

    monkeypatch.setattr(
        "app.services.akshare_subprocess.run_akshare_json_script",
        runner,
    )
    payload = module._default_portfolio_rows_fetcher(
        "000001",
        years=["2026"],
        decision_at=DECISION_AT,
    )
    result = _build(
        payload,
        [_announcement("测试基金2026年第1季度报告", "2026-04-20")],
    )

    assert payload["rows"] == []
    assert payload["provider_audit"]["status"] == "unavailable"
    assert result["status"] == "unavailable"
    assert result["reason_codes"] == ["eastmoney_holdings_star_footnote_missing"]
    response_audit = result["audit"]["portfolio_provider_raw_validation"][
        "responses"
    ][0]
    assert response_audit["raw_response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert response_audit["normalized_input_sha256"] is None


def test_report_period_conflict_is_invalid() -> None:
    row = _row("000001", 5, period="2026年2季度")
    row["as_of_date"] = "2026-03-31"
    result = _build([row], [_announcement()])

    assert result["status"] == "invalid"
    assert "portfolio_row_report_period_conflict" in result["reason_codes"]


def test_announcement_explicit_period_conflicting_with_title_is_invalid() -> None:
    announcement = _announcement()
    announcement["report_period"] = "2026-Q1"
    result = _build(_top10_rows(), [announcement])

    assert result["status"] == "invalid"
    assert "announcement_report_period_ambiguous" in result["reason_codes"]


def test_real_builder_contract_round_trips_append_only_pit_store() -> None:
    decision = datetime(2026, 7, 14, 12, 0, tzinfo=CN)
    rows = [
        _row(
            f"{index:06d}",
            2,
            period="2025年4季度股票投资明细",
            rank=index,
        )
        for index in range(1, 13)
    ]
    snapshot = _build(
        rows,
        [
            _announcement(
                "测试基金2025年年度报告",
                "2026-03-28",
                report_id="annual-real",
            )
        ],
        decision_at=decision,
    )
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)

    stored = database.save_fund_holdings_snapshot(snapshot, connection=connection)
    first_observed_at = datetime.fromisoformat(
        stored["record"]["first_observed_at"]
    )
    loaded = database.get_latest_fund_holdings_snapshot(
        fund_code="000001",
        decision_at=first_observed_at + timedelta(microseconds=1),
        connection=connection,
    )

    assert stored["stored"] is True
    assert loaded is not None
    assert loaded["snapshot_hash"] == snapshot["snapshot_hash"]
    assert loaded["payload"]["qualification"] == snapshot["qualification"]
