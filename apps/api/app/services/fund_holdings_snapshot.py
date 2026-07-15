"""Strict point-in-time fund holdings disclosure snapshots.

This module deliberately separates a report's *as-of* date from the time at
which its holdings became usable evidence.  The default resolver parses the
raw Eastmoney ``FundArchivesDatas.aspx?type=jjcc`` response before any
dataframe normalization so its original ``*`` provenance marker cannot be
lost.  The endpoint returns all periods in a requested year and can expose
rows expanded by a later semi-annual/annual report, so callers must never
select ``head()`` across that response or use the fetch time as disclosure
time.

The pure builder accepts frozen portfolio rows and fund-announcement records.
The resolver adds optional providers, while refusing default live providers
for historical replay.  Nothing in this module treats an inferred A/C family
relationship as an authoritative merge key.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from copy import deepcopy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.decision_clock import DecisionClock
from app.services.trading_session import build_trading_session


HOLDINGS_SNAPSHOT_SCHEMA_VERSION = "fund_holdings_snapshot.v1"
_EASTMONEY_RAW_PARSE_SCHEMA_VERSION = "eastmoney_fund_holdings_raw_parse.v1"
_EASTMONEY_PORTFOLIO_URL = (
    "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
)
_EASTMONEY_STAR_FOOTNOTE = (
    "注：加*号代表进入上市公司的十大流通股东却没有进入单只基金前十大重仓股的个股。"
)
_EASTMONEY_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_ISSUER_INFERENCE_SOURCE_KIND = "issuer_shareholder_inference"
_ISSUER_INFERENCE_EXCLUDED_REASON = (
    "eastmoney_issuer_shareholder_inference_excluded"
)
_LEGACY_HOLDINGS_SOURCE = "akshare.fund_portfolio_hold_em"
_DEFAULT_LIVE_HOLDINGS_SOURCE = "eastmoney.FundArchivesDatas.jjcc_raw"
DEFAULT_LIVE_FETCH_DECISION_SKEW_SECONDS = 30 * 60
DEFAULT_WEIGHT_TOLERANCE_PERCENT = 0.01
FRESH_REPORT_MAX_AGE_DAYS = 120
AGING_REPORT_MAX_AGE_DAYS = 210
CN_TZ = ZoneInfo("Asia/Shanghai")

_PERIOD_KEYS = (
    "report_period",
    "period",
    "quarter",
    "季度",
    "报告期",
    "持仓季度",
    "报告期间",
)
_AS_OF_KEYS = ("as_of_date", "as_of", "截止日期", "报告截止日")
_CODE_KEYS = (
    "security_code",
    "stock_code",
    "code",
    "证券代码",
    "股票代码",
)
_NAME_KEYS = (
    "security_name",
    "stock_name",
    "name",
    "证券名称",
    "股票名称",
)
_WEIGHT_KEYS = (
    "weight_percent",
    "weight",
    "占净值比例",
    "占基金净值比",
    "占净值比",
    "净值占比",
)
_RANK_KEYS = ("rank", "序号", "排名")
_SHARES_KEYS = ("shares", "持股数", "持股数（万股）", "持股数(万股)")
_MARKET_VALUE_KEYS = (
    "market_value",
    "持仓市值",
    "持仓市值（万元）",
    "持仓市值(万元)",
)
_ROW_AVAILABLE_AT_KEYS = (
    "row_available_at",
    "disclosed_at",
    "披露时间",
    "披露日期",
)
_SCOPE_KEYS = (
    "disclosure_scope",
    "scope",
    "report_kind",
    "报告类型",
    "披露范围",
)
_ANNOUNCEMENT_TITLE_KEYS = (
    "title",
    "announcement_title",
    "公告标题",
    "标题",
)
_ANNOUNCEMENT_TIME_KEYS = (
    "available_at",
    "published_at",
    "publish_time",
    "announcement_time",
    "公告时间",
    "公告日期",
    "发布日期",
    "发布时间",
    "送出时间",
    "送出日期",
    "date",
)
_ANNOUNCEMENT_ID_KEYS = (
    "announcement_id",
    "report_id",
    "notice_id",
    "art_code",
    "id",
    "公告ID",
    "报告ID",
)
_ANNOUNCEMENT_URL_KEYS = ("url", "link", "公告链接", "详情链接")
_FUND_CODE_KEYS = ("fund_code", "基金代码")
_DISCLOSURE_SOURCE_KIND_KEYS = (
    "disclosure_source_kind",
    "source_kind",
    "holding_source_kind",
)
_OBSERVATION_ONLY_KEYS = {
    "fetched_at",
    "retrieved_at",
    "observed_at",
    "first_observed_at",
    "cache_time",
    "cached_at",
}

_QUARTER_RE = re.compile(
    r"(?P<year>20\d{2})\s*(?:年|[-/.])?\s*(?:第\s*)?"
    r"(?P<quarter>[1234一二三四])\s*(?:季(?:度)?|q)",
    re.IGNORECASE,
)
_Q_PREFIX_RE = re.compile(
    r"(?P<year>20\d{2})\s*[-/.]?\s*q\s*(?P<quarter>[1234])",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class _DecisionContext:
    moment: datetime
    effective_date: date


@dataclass(frozen=True)
class _Period:
    year: int
    quarter: int

    @property
    def label(self) -> str:
        return f"{self.year:04d}-Q{self.quarter}"

    @property
    def as_of(self) -> date:
        month_day = {
            1: (3, 31),
            2: (6, 30),
            3: (9, 30),
            4: (12, 31),
        }[self.quarter]
        return date(self.year, *month_day)


@dataclass(frozen=True)
class _Announcement:
    period: _Period
    scope: str
    available_at: datetime | None
    title: str
    announcement_id: str | None
    url: str | None
    is_summary: bool
    raw: dict[str, Any]


class _EastmoneyPortfolioParseError(ValueError):
    def __init__(
        self,
        reason_code: str,
        *,
        raw_response_sha256: str | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.raw_response_sha256 = raw_response_sha256


def build_fund_holdings_snapshot(
    portfolio_rows: object,
    announcement_records: object,
    *,
    fund_code: object,
    decision_at: str | datetime | None = None,
    decision_clock: DecisionClock | None = None,
    source: str = _LEGACY_HOLDINGS_SOURCE,
    fetched_at: object = None,
    family_hint: Mapping[str, Any] | None = None,
    weight_tolerance: float = DEFAULT_WEIGHT_TOLERANCE_PERCENT,
) -> dict[str, Any]:
    """Build the latest valid PIT holdings snapshot from frozen inputs.

    ``available_at`` is derived only from a matching report announcement (or
    an explicit row-level disclosure timestamp).  ``fetched_at`` is retained
    as non-semantic audit metadata and never substitutes for publication time
    or participates in either hash.
    """

    decision, decision_reasons = _resolve_decision(
        decision_at=decision_at,
        decision_clock=decision_clock,
    )
    code = _fund_code(fund_code)
    base = _base_payload(
        fund_code=code,
        decision=decision,
        source=source,
        family_hint=family_hint,
        fetched_at=fetched_at,
    )
    reasons = list(decision_reasons)
    if code is None:
        reasons.append("fund_code_invalid")
    if (
        isinstance(weight_tolerance, bool)
        or not isinstance(weight_tolerance, (int, float))
        or not math.isfinite(float(weight_tolerance))
        or float(weight_tolerance) < 0
    ):
        reasons.append("weight_tolerance_invalid")
    if reasons:
        return _finish(base, status="invalid", reasons=reasons)

    provider_audit = _portfolio_provider_audit(portfolio_rows)
    if provider_audit is not None:
        base["audit"]["portfolio_provider_raw_validation"] = provider_audit
    rows, rows_reason = _records(portfolio_rows)
    announcements, announcements_reason = _records(announcement_records)
    base["audit"]["portfolio_rows_received"] = len(rows)
    base["audit"]["announcement_records_received"] = len(announcements)
    provider_reasons = _portfolio_provider_failure_reasons(provider_audit)
    if (
        provider_audit is None
        and str(source or "").strip() == _DEFAULT_LIVE_HOLDINGS_SOURCE
    ):
        provider_reasons.append("eastmoney_holdings_provider_audit_missing")
    if provider_reasons:
        base["source_hash"] = _raw_source_hash(rows, announcements)
        return _finish(base, status="unavailable", reasons=provider_reasons)
    if rows_reason:
        reasons.append(rows_reason)
    if announcements_reason:
        reasons.append(announcements_reason)
    if reasons:
        base["source_hash"] = _raw_source_hash(rows, announcements)
        return _finish(base, status="invalid", reasons=reasons)
    if not rows:
        base["source_hash"] = _raw_source_hash(rows, announcements)
        return _finish(base, status="unavailable", reasons=["portfolio_rows_missing"])
    if not announcements:
        base["source_hash"] = _raw_source_hash(rows, announcements)
        return _finish(
            base,
            status="unavailable",
            reasons=["announcement_records_missing"],
        )

    assert decision is not None
    (
        grouped,
        row_errors,
        future_row_count,
        future_row_periods,
        exact_duplicate_count,
    ) = _group_rows(
        rows,
        fund_code=code or "",
        decision=decision,
    )
    normalized_announcements, announcement_errors = _normalize_announcements(
        announcements,
        fund_code=code or "",
    )
    base["audit"].update(
        {
            "parsed_period_count": len(grouped),
            "future_rows_dropped": future_row_count,
            "exact_duplicate_rows_collapsed": exact_duplicate_count,
            "announcement_parse_error_count": len(announcement_errors),
        }
    )
    base["source_hash"] = _raw_source_hash(rows, announcements)
    if row_errors:
        return _finish(base, status="invalid", reasons=row_errors)
    if not grouped:
        reason = (
            "all_portfolio_rows_after_decision"
            if future_row_count
            else "portfolio_report_period_unavailable"
        )
        return _finish(base, status="unavailable", reasons=[reason])

    rejected: list[dict[str, Any]] = []
    invalid_candidates: list[tuple[_Period, list[str]]] = []
    candidate_periods = set(grouped) | set(future_row_periods)
    for period in sorted(candidate_periods, key=lambda item: item.as_of, reverse=True):
        if period in future_row_periods:
            rejected.append(
                {
                    "report_period": period.label,
                    "reason_codes": ["portfolio_period_revision_after_decision"],
                }
            )
            continue
        period_rows = grouped[period]
        result, candidate_reasons, rejected_future_announcements = _build_period_snapshot(
            fund_code=code or "",
            period=period,
            rows=period_rows,
            announcements=normalized_announcements,
            announcement_errors=announcement_errors,
            decision=decision,
            source=source,
            weight_tolerance=float(weight_tolerance),
            family_hint=base["family_hint"],
            provider_audit=provider_audit,
        )
        base["audit"]["future_announcements_dropped"] += (
            rejected_future_announcements
        )
        if result is not None:
            result["audit"].update(base["audit"])
            result["audit"]["newer_periods_rejected"] = rejected
            result["source_hash"] = _selected_source_hash(result)
            return _finish(result, status="qualified", reasons=[])
        rejected.append(
            {
                "report_period": period.label,
                "reason_codes": _unique(candidate_reasons),
            }
        )
        if any(_is_invalid_reason(reason) for reason in candidate_reasons):
            invalid_candidates.append((period, candidate_reasons))

    base["audit"]["newer_periods_rejected"] = rejected
    if invalid_candidates:
        period, candidate_reasons = invalid_candidates[0]
        base["report_period"] = period.label
        base["as_of_date"] = period.as_of.isoformat()
        return _finish(base, status="invalid", reasons=candidate_reasons)
    unavailable_reasons = [
        reason
        for item in rejected
        for reason in item.get("reason_codes") or []
    ]
    return _finish(
        base,
        status="unavailable",
        reasons=unavailable_reasons or ["point_in_time_snapshot_unavailable"],
    )


def resolve_fund_holdings_snapshot(
    fund_code: object,
    *,
    decision_at: str | datetime | None = None,
    decision_clock: DecisionClock | None = None,
    portfolio_rows: object = None,
    announcement_records: object = None,
    fetch_portfolio_rows: Any | None = None,
    fetch_announcements: Any | None = None,
    source: str = _LEGACY_HOLDINGS_SOURCE,
    fetched_at: object = None,
    family_hint: Mapping[str, Any] | None = None,
    weight_tolerance: float = DEFAULT_WEIGHT_TOLERANCE_PERCENT,
    live_fetch_decision_skew_seconds: int = DEFAULT_LIVE_FETCH_DECISION_SKEW_SECONDS,
) -> dict[str, Any]:
    """Resolve inputs and build a snapshot without historical live leakage.

    Supplying frozen rows or custom providers permits historical replay.  If a
    missing input would require a default live provider, the decision time must
    be between five minutes in the future and thirty minutes in the past (by
    default), matching the benchmark research boundary.
    """

    needs_default_portfolio = portfolio_rows is None and fetch_portfolio_rows is None
    needs_default_announcements = (
        announcement_records is None and fetch_announcements is None
    )
    resolved_source = (
        _DEFAULT_LIVE_HOLDINGS_SOURCE
        if needs_default_portfolio and source == _LEGACY_HOLDINGS_SOURCE
        else source
    )
    decision, decision_reasons = _resolve_decision(
        decision_at=decision_at,
        decision_clock=decision_clock,
    )
    if decision_reasons:
        return build_fund_holdings_snapshot(
            portfolio_rows,
            announcement_records,
            fund_code=fund_code,
            decision_at=decision_at,
            decision_clock=decision_clock,
            source=resolved_source,
            fetched_at=fetched_at,
            family_hint=family_hint,
            weight_tolerance=weight_tolerance,
        )
    if (
        isinstance(live_fetch_decision_skew_seconds, bool)
        or not isinstance(live_fetch_decision_skew_seconds, int)
        or live_fetch_decision_skew_seconds < 0
    ):
        result = _base_payload(
            fund_code=_fund_code(fund_code),
            decision=decision,
            source=resolved_source,
            family_hint=family_hint,
            fetched_at=fetched_at,
        )
        return _finish(
            result,
            status="invalid",
            reasons=["live_fetch_decision_skew_invalid"],
        )

    assert decision is not None
    if needs_default_portfolio or needs_default_announcements:
        now_utc = datetime.now(timezone.utc)
        skew = (now_utc - decision.moment.astimezone(timezone.utc)).total_seconds()
        if not (-300 <= skew <= live_fetch_decision_skew_seconds):
            result = _base_payload(
                fund_code=_fund_code(fund_code),
                decision=decision,
                source=resolved_source,
                family_hint=family_hint,
                fetched_at=fetched_at,
            )
            return _finish(
                result,
                status="unavailable",
                reasons=["historical_live_fetch_disallowed"],
            )

    code = _fund_code(fund_code)
    provider_reasons: list[str] = []
    resolved_rows = portfolio_rows
    resolved_announcements = announcement_records
    if resolved_rows is None and code is not None:
        provider = fetch_portfolio_rows or _default_portfolio_rows_fetcher
        try:
            resolved_rows = provider(
                code,
                years=_relevant_years(decision.moment.date()),
                decision_at=decision.moment,
            )
        except Exception:
            resolved_rows = []
            provider_reasons.append("portfolio_provider_error")
    if resolved_announcements is None and code is not None:
        provider = fetch_announcements or _default_announcements_fetcher
        try:
            resolved_announcements = provider(
                code,
                limit=100,
                decision_at=decision.moment,
            )
        except Exception:
            resolved_announcements = []
            provider_reasons.append("announcement_provider_error")

    result = build_fund_holdings_snapshot(
        resolved_rows,
        resolved_announcements,
        fund_code=fund_code,
        decision_at=decision.moment,
        source=resolved_source,
        fetched_at=fetched_at,
        family_hint=family_hint,
        weight_tolerance=weight_tolerance,
    )
    if provider_reasons:
        return _finish(
            result,
            status="unavailable",
            reasons=[*provider_reasons, *result.get("reason_codes", [])],
        )
    return result


def materialize_fund_holdings_snapshot_for_decision(
    snapshot: Mapping[str, Any],
    *,
    decision_at: str | datetime | None = None,
    decision_clock: DecisionClock | None = None,
) -> dict[str, Any]:
    """Rebuild decision-relative fields for one immutable disclosure.

    Stored ``payload_json`` is the view that happened to be produced when the
    immutable disclosure was first observed.  Its freshness and consumption
    gates therefore must never be reused at a later (or earlier) decision
    clock.  This function keeps the source/snapshot identity untouched and
    recomputes only the decision-relative view.

    The v1 disclosure contract can support point-in-time holdings research,
    but it cannot represent a complete current fund book (cash, bonds,
    derivatives and trades since the report date are absent).  Consequently
    ``nowcast_eligible`` is explicitly false even for a fresh qualified
    snapshot.
    """

    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")
    decision, decision_reasons = _resolve_decision(
        decision_at=decision_at,
        decision_clock=decision_clock,
    )
    if decision_reasons or decision is None:
        reason = decision_reasons[0] if decision_reasons else "decision_at_required"
        raise ValueError(reason)

    materialized = deepcopy(dict(snapshot))
    materialized["decision_at"] = decision.moment.isoformat()

    source_validation = snapshot.get("source_validation")
    source_validation_valid = bool(
        isinstance(source_validation, Mapping)
        and source_validation.get("schema_version")
        == "fund_holdings_source_validation.v1"
    )
    source_status = (
        str(source_validation.get("status") or "").strip().lower()
        if isinstance(source_validation, Mapping)
        else ""
    )
    source_qualified = bool(
        isinstance(source_validation, Mapping)
        and source_validation.get("qualified") is True
        and source_validation.get("valid_snapshot") is True
    )
    snapshot_hash_valid = validate_fund_holdings_snapshot_hash(snapshot)
    schema_valid = snapshot.get("schema_version") == HOLDINGS_SNAPSHOT_SCHEMA_VERSION
    scope = snapshot.get("scope")
    scope_valid = (
        isinstance(scope, Mapping)
        and scope.get("kind") in {"top10", "full_portfolio"}
    )
    holdings_valid = isinstance(snapshot.get("holdings"), Sequence) and not isinstance(
        snapshot.get("holdings"), (str, bytes)
    )
    coverage = snapshot.get("coverage")
    coverage_raw = None
    if isinstance(coverage, Mapping):
        coverage_raw = coverage.get("portfolio_weight_coverage_percent")
        if coverage_raw is None:
            coverage_raw = coverage.get("weight_sum_percent")
    coverage_value = _finite_number(coverage_raw)
    coverage_valid = bool(
        coverage_value is not None and 0 < coverage_value <= 100.01
    )
    as_of_raw = snapshot.get("as_of_date")
    available_raw = snapshot.get("available_at")
    try:
        as_of = date.fromisoformat(str(as_of_raw))
    except (TypeError, ValueError):
        as_of = None
    available_at = _aware_datetime(available_raw)

    reasons: list[str] = []
    status = source_status or "invalid"
    qualified = False
    if not snapshot_hash_valid:
        status = "invalid"
        reasons.append("stored_snapshot_hash_invalid")
    elif not source_validation_valid:
        status = "invalid"
        reasons.append("stored_snapshot_source_validation_missing")
    elif source_status != "qualified" or not source_qualified:
        reasons.extend(
            str(item)
            for item in source_validation.get("reason_codes") or []
            if item
        )
        if not reasons:
            reasons.append("stored_snapshot_source_not_qualified")
    elif not schema_valid:
        status = "invalid"
        reasons.append("stored_snapshot_schema_invalid")
    elif as_of is None:
        status = "invalid"
        reasons.append("stored_snapshot_as_of_invalid")
    elif available_at is None:
        status = "invalid"
        reasons.append("stored_snapshot_available_at_invalid")
    elif not scope_valid:
        status = "invalid"
        reasons.append("stored_snapshot_scope_invalid")
    elif not holdings_valid:
        status = "invalid"
        reasons.append("stored_snapshot_holdings_invalid")
    elif not coverage_valid:
        status = "invalid"
        reasons.append("stored_snapshot_coverage_invalid")
    elif available_at > decision.moment:
        status = "unavailable"
        reasons.append("snapshot_after_decision")
    elif as_of > decision.moment.date():
        status = "unavailable"
        reasons.append("snapshot_period_after_decision")
    else:
        status = "qualified"
        qualified = True

    if as_of is not None and available_at is not None:
        materialized["freshness"] = _freshness(
            decision=decision,
            as_of=as_of,
            available_at=available_at,
        )
    else:
        materialized["freshness"] = deepcopy(_base_payload(
            fund_code=_fund_code(snapshot.get("fund_code")),
            decision=decision,
            source="stored_snapshot",
            family_hint=None,
            fetched_at=None,
        )["freshness"])

    reason_codes = _unique(reasons)
    freshness = materialized.get("freshness")
    overlap_eligible = bool(
        qualified
        and scope_valid
        and isinstance(freshness, Mapping)
        and freshness.get("label") in {"fresh", "aging"}
    )
    materialized["status"] = status
    materialized["qualified"] = qualified
    materialized["reason_codes"] = reason_codes
    materialized["qualification"] = {
        "status": status,
        "qualified": qualified,
        "valid_snapshot": qualified,
        "pit_eligible": bool(qualified and available_at is not None),
        "disclosure_scope_identified": bool(qualified and scope_valid),
        "weight_validation_passed": qualified,
        "disclosed_overlap_lower_bound_eligible": overlap_eligible,
        "exact_full_portfolio_overlap_eligible": False,
        "current_holdings_inference_eligible": False,
        "nowcast_eligible": False,
        "reason_codes": reason_codes,
    }
    # Deliberately preserve the append-only identity.  decision_at,
    # freshness and qualification are excluded from that identity contract.
    materialized["snapshot_hash"] = snapshot.get("snapshot_hash")
    return materialized


def _build_period_snapshot(
    *,
    fund_code: str,
    period: _Period,
    rows: list[dict[str, Any]],
    announcements: list[_Announcement],
    announcement_errors: list[dict[str, Any]],
    decision: _DecisionContext,
    source: str,
    weight_tolerance: float,
    family_hint: dict[str, Any],
    provider_audit: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str], int]:
    reasons: list[str] = []
    matching_errors = [
        item for item in announcement_errors if item.get("report_period") == period.label
    ]
    if matching_errors:
        reasons.extend(str(item["reason"]) for item in matching_errors)

    period_announcements = [item for item in announcements if item.period == period]
    future_count = sum(
        item.available_at is not None and item.available_at > decision.moment
        for item in period_announcements
    )
    eligible = [
        item
        for item in period_announcements
        if item.available_at is not None and item.available_at <= decision.moment
    ]
    if not eligible:
        reasons.append(
            "announcement_after_decision"
            if future_count
            else "matching_report_announcement_missing"
        )
        return None, _unique(reasons), future_count

    row_scopes = {str(row.get("_scope_hint") or "unknown") for row in rows}
    if len(row_scopes - {"unknown"}) > 1:
        reasons.append("portfolio_rows_mixed_disclosure_scope")
        return None, _unique(reasons), future_count
    explicit_scope = next(iter(row_scopes - {"unknown"}), None)
    if explicit_scope is not None and "unknown" in row_scopes:
        reasons.append("portfolio_rows_partial_scope_provenance")
        return None, _unique(reasons), future_count

    row_count = len(rows)
    target_scope: str | None = explicit_scope
    top10 = [item for item in eligible if item.scope == "top10"]
    full = [item for item in eligible if item.scope == "full_portfolio"]
    unknown = [item for item in eligible if item.scope == "unknown"]

    if period.quarter in {1, 3}:
        if row_count > 10:
            reasons.append("quarterly_top10_row_count_exceeded")
        target_scope = target_scope or "top10"
        if target_scope != "top10":
            reasons.append("quarterly_scope_conflict")
    elif target_scope is None:
        if row_count > 10:
            target_scope = "full_portfolio"
            if not full:
                reasons.append("later_expanded_rows_without_full_report_availability")
        elif top10 and full:
            reasons.append("q2_q4_disclosure_scope_ambiguous")
        elif top10:
            target_scope = "top10"
        elif full:
            target_scope = "full_portfolio"
        else:
            reasons.append("q2_q4_disclosure_scope_unproven")
    if target_scope not in {"top10", "full_portfolio"}:
        reasons.append("disclosure_scope_invalid")
    if target_scope == "top10" and row_count > 10:
        reasons.append("top10_disclosure_row_count_exceeded")

    supporting = (
        top10 if target_scope == "top10" else full if target_scope == "full_portfolio" else []
    )
    if not supporting and unknown and period.quarter in {1, 3} and target_scope == "top10":
        supporting = unknown
    if not supporting:
        reasons.append("supporting_report_announcement_missing")
    chosen, choose_reasons = _choose_supporting_announcement(
        supporting,
        target_scope=target_scope or "unknown",
    )
    reasons.extend(choose_reasons)

    normalized_holdings, holding_reasons, duplicate_count = _validate_holdings(
        rows,
        weight_tolerance=weight_tolerance,
    )
    reasons.extend(holding_reasons)
    if reasons or chosen is None or normalized_holdings is None:
        return None, _unique(reasons), future_count

    weight_sum = round(
        sum(float(row["weight_percent"]) for row in normalized_holdings),
        8,
    )
    scope = {
        "kind": target_scope,
        "completeness": "full" if target_scope == "full_portfolio" else "partial",
        "basis": (
            "matched_semiannual_or_annual_report"
            if target_scope == "full_portfolio"
            else "matched_quarterly_report_top10"
        ),
        "quarter": period.quarter,
        "row_limit_semantics": (
            "all_disclosed_equity_positions"
            if target_scope == "full_portfolio"
            else "top10_disclosed_positions_only"
        ),
    }
    report_available_at = max(
        item.available_at for item in chosen if item.available_at is not None
    )
    row_available_values = [
        parsed
        for row in rows
        if (parsed := _aware_datetime(row.get("_row_available_at"))) is not None
    ]
    effective_available_at = max([report_available_at, *row_available_values])
    source_refs = [_announcement_source_ref(item, source=source) for item in chosen]
    provider_source_ref, provider_source_reason = _portfolio_provider_source_ref(
        provider_audit,
        period=period,
    )
    if provider_source_reason:
        return None, _unique([*reasons, provider_source_reason]), future_count
    if provider_source_ref is not None:
        if provider_source_ref.get("fund_disclosure_row_count") != len(rows):
            return (
                None,
                _unique([*reasons, "eastmoney_holdings_period_row_count_mismatch"]),
                future_count,
            )
        source_refs.append(provider_source_ref)
    if row_available_values:
        source_refs.append(
            _row_availability_source_ref(
                rows,
                period=period,
                source=source,
                effective_available_at=effective_available_at,
            )
        )
    payload = _base_payload(
        fund_code=fund_code,
        decision=decision,
        source=source,
        family_hint=family_hint,
        fetched_at=None,
    )
    payload.update(
        {
            "report_period": period.label,
            "as_of_date": period.as_of.isoformat(),
            "available_at": effective_available_at.isoformat(),
            "scope": scope,
            "freshness": _freshness(
                decision=decision,
                as_of=period.as_of,
                available_at=effective_available_at,
            ),
            "coverage": {
                "disclosed_holding_count": len(normalized_holdings),
                "weight_sum_percent": weight_sum,
                "portfolio_weight_coverage_percent": weight_sum,
                "coverage_ratio": round(weight_sum / 100.0, 10),
                "is_complete_security_list": target_scope == "full_portfolio",
                "is_complete_fund_portfolio": False,
                "coverage_denominator": "fund_nav_percent",
                "duplicate_rows_collapsed": duplicate_count,
            },
            "holdings": normalized_holdings,
            "source_refs": source_refs,
        }
    )
    payload["source"]["availability_basis"] = (
        "max_of_matched_report_and_explicit_row_availability"
        if row_available_values
        else "matched_report_announcement_only"
    )
    payload["audit"]["selected_period_row_count"] = len(rows)
    payload["audit"]["supporting_announcement_count"] = len(chosen)
    payload["audit"]["row_availability_count"] = len(row_available_values)
    payload["audit"]["effective_available_at"] = effective_available_at.isoformat()
    return payload, [], future_count


def _group_rows(
    rows: list[dict[str, Any]],
    *,
    fund_code: str,
    decision: _DecisionContext,
) -> tuple[
    dict[_Period, list[dict[str, Any]]],
    list[str],
    int,
    set[_Period],
    int,
]:
    grouped: dict[_Period, list[dict[str, Any]]] = {}
    errors: list[str] = []
    future_count = 0
    future_periods: set[_Period] = set()
    exact_duplicate_count = 0
    fingerprints: set[str] = set()
    for raw in rows:
        source_kind_raw, source_kind_present = _first_present(
            raw,
            _DISCLOSURE_SOURCE_KIND_KEYS,
        )
        if source_kind_present and str(source_kind_raw or "").strip() != "fund_disclosure":
            errors.append("portfolio_row_source_kind_not_fund_disclosure")
            continue
        raw_fund_code, raw_fund_code_present = _first_present(
            raw,
            _FUND_CODE_KEYS,
        )
        if raw_fund_code_present:
            row_fund_code = _fund_code(raw_fund_code)
            if row_fund_code is None:
                errors.append("portfolio_row_fund_code_invalid")
                continue
            if row_fund_code != fund_code:
                errors.append("portfolio_row_fund_code_mismatch")
                continue
        periods = _row_periods(raw)
        if not periods:
            errors.append("portfolio_row_report_period_unparseable")
            continue
        if len(periods) != 1:
            errors.append("portfolio_row_report_period_conflict")
            continue
        period = next(iter(periods))
        if period.as_of > decision.moment.date():
            future_count += 1
            future_periods.add(period)
            continue
        row_available_at, row_availability_present = _row_available_at(raw)
        if row_availability_present and row_available_at is None:
            errors.append("portfolio_row_available_at_invalid")
            continue
        if row_available_at is not None and row_available_at > decision.moment:
            future_count += 1
            future_periods.add(period)
            continue
        normalized = dict(raw)
        normalized["_period"] = period.label
        normalized["_scope_hint"] = _scope_hint(_first(raw, _SCOPE_KEYS))
        normalized["_row_available_at"] = (
            row_available_at.isoformat() if row_available_at is not None else None
        )
        fingerprint = _hash_material(_json_safe_mapping(normalized))
        if fingerprint in fingerprints:
            exact_duplicate_count += 1
            continue
        fingerprints.add(fingerprint)
        grouped.setdefault(period, []).append(normalized)

    for period_rows in grouped.values():
        has_row_time = [row.get("_row_available_at") is not None for row in period_rows]
        if any(has_row_time) and not all(has_row_time):
            errors.append("portfolio_rows_partial_availability_provenance")
    return grouped, _unique(errors), future_count, future_periods, exact_duplicate_count


def _normalize_announcements(
    rows: list[dict[str, Any]],
    *,
    fund_code: str,
) -> tuple[list[_Announcement], list[dict[str, Any]]]:
    output: list[_Announcement] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        record_code = _fund_code(_first(raw, _FUND_CODE_KEYS))
        if record_code is not None and record_code != fund_code:
            continue
        title = _text(_first(raw, _ANNOUNCEMENT_TITLE_KEYS)) or ""
        explicit_periods = _periods_from_values(
            [_first(raw, _PERIOD_KEYS), _first(raw, _AS_OF_KEYS)]
        )
        title_period = _period_from_text(title)
        periods = set(explicit_periods)
        if title_period is not None:
            periods.add(title_period)
        if not periods:
            continue
        if len(periods) != 1:
            for conflicting_period in sorted(periods, key=lambda item: item.as_of):
                errors.append(
                    {
                        "report_period": conflicting_period.label,
                        "reason": "announcement_report_period_ambiguous",
                    }
                )
            continue
        period = next(iter(periods))
        raw_time, time_present = _first_present(raw, _ANNOUNCEMENT_TIME_KEYS)
        available_at = _publication_available_at(raw_time) if time_present else None
        if available_at is None:
            errors.append(
                {
                    "report_period": period.label,
                    "reason": "announcement_available_at_invalid",
                }
            )
            continue
        scope = _announcement_scope(
            title=title,
            period=period,
            explicit=_first(raw, _SCOPE_KEYS),
        )
        item = _Announcement(
            period=period,
            scope=scope,
            available_at=available_at,
            title=title,
            announcement_id=_text(_first(raw, _ANNOUNCEMENT_ID_KEYS)),
            url=_text(_first(raw, _ANNOUNCEMENT_URL_KEYS)),
            is_summary=bool(re.search(r"摘要|summary", title, re.IGNORECASE)),
            raw=_json_safe_mapping(raw),
        )
        fingerprint = _hash_material(
            {
                "period": item.period.label,
                "scope": item.scope,
                "available_at": item.available_at.isoformat(),
                "title": item.title,
                "announcement_id": item.announcement_id,
                "url": item.url,
                "raw": item.raw,
            }
        )
        if fingerprint not in seen:
            seen.add(fingerprint)
            output.append(item)
    output.sort(
        key=lambda item: (
            item.period.as_of,
            item.scope,
            item.available_at or datetime.min.replace(tzinfo=CN_TZ),
            item.announcement_id or "",
            item.title,
        )
    )
    return output, errors


def _choose_supporting_announcement(
    candidates: list[_Announcement],
    *,
    target_scope: str,
) -> tuple[list[_Announcement] | None, list[str]]:
    if not candidates:
        return None, []
    primary = [item for item in candidates if not item.is_summary]
    if target_scope == "full_portfolio" and not primary:
        return None, ["full_report_primary_announcement_missing"]
    pool = primary or candidates
    identities = {
        (
            item.available_at.isoformat() if item.available_at else None,
            item.announcement_id,
            item.url,
            item.title,
        )
        for item in pool
    }
    if len(identities) != 1:
        return None, ["supporting_report_announcement_ambiguous"]
    selected = [
        item
        for item in candidates
        if item.available_at == pool[0].available_at
        and (not item.is_summary or item.available_at == pool[0].available_at)
    ]
    return selected, []


def _validate_holdings(
    rows: list[dict[str, Any]],
    *,
    weight_tolerance: float,
) -> tuple[list[dict[str, Any]] | None, list[str], int]:
    reasons: list[str] = []
    by_code: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for raw in rows:
        raw_code = _first(raw, _CODE_KEYS)
        code = _security_code(raw_code)
        name = _text(_first(raw, _NAME_KEYS)) or ""
        weight = _finite_number(_first(raw, _WEIGHT_KEYS))
        if code is None:
            reasons.append("holding_security_code_invalid")
            continue
        if weight is None:
            reasons.append("holding_weight_invalid")
            continue
        if weight < 0:
            reasons.append("holding_weight_negative")
            continue
        if weight > 100:
            reasons.append("holding_weight_above_100")
            continue
        rank_raw = _first(raw, _RANK_KEYS)
        rank = _positive_int(rank_raw) if rank_raw is not None else None
        shares_raw = _first(raw, _SHARES_KEYS)
        shares = _finite_number(shares_raw) if shares_raw is not None else None
        market_value_raw = _first(raw, _MARKET_VALUE_KEYS)
        market_value = (
            _finite_number(market_value_raw) if market_value_raw is not None else None
        )
        if shares is not None and shares < 0:
            reasons.append("holding_shares_negative")
        if market_value is not None and market_value < 0:
            reasons.append("holding_market_value_negative")
        security_id, identity_basis = _static_security_identity(
            code,
            raw_value=raw_code,
        )
        normalized = {
            "security_code": code,
            "security_name": name,
            "security_id": security_id,
            "security_identity_basis": identity_basis,
            "weight_percent": round(weight, 8),
            "rank": rank,
            "shares": round(shares, 8) if shares is not None else None,
            "market_value": (
                round(market_value, 8) if market_value is not None else None
            ),
        }
        existing = by_code.get(code)
        if existing is None:
            by_code[code] = normalized
        elif existing == normalized:
            duplicate_count += 1
        else:
            reasons.append("holding_duplicate_conflict")
    if reasons:
        return None, _unique(reasons), duplicate_count
    normalized_rows = sorted(
        by_code.values(),
        key=lambda row: (
            -float(row["weight_percent"]),
            row["rank"] if row["rank"] is not None else 10**9,
            str(row["security_code"]),
            str(row["security_name"]),
        ),
    )
    total = sum(float(row["weight_percent"]) for row in normalized_rows)
    if total <= 0:
        return None, ["holding_weight_coverage_missing"], duplicate_count
    if total > 100.0 + weight_tolerance:
        return None, ["holding_weight_sum_above_100"], duplicate_count
    return normalized_rows, [], duplicate_count


def _base_payload(
    *,
    fund_code: str | None,
    decision: _DecisionContext | None,
    source: str,
    family_hint: Mapping[str, Any] | None,
    fetched_at: object,
) -> dict[str, Any]:
    return {
        "schema_version": HOLDINGS_SNAPSHOT_SCHEMA_VERSION,
        "fund_code": fund_code,
        "fund_master_key": fund_code,
        "decision_at": decision.moment.isoformat() if decision else None,
        "report_period": None,
        "as_of_date": None,
        "available_at": None,
        "first_observed_at": None,
        "status": "unavailable",
        "qualified": False,
        "source_validation": {
            "schema_version": "fund_holdings_source_validation.v1",
            "status": "unavailable",
            "qualified": False,
            "valid_snapshot": False,
            "available_at_known": False,
            "disclosure_scope_identified": False,
            "weight_validation_passed": False,
            "reason_codes": [],
        },
        "qualification": {
            "status": "unavailable",
            "qualified": False,
            "valid_snapshot": False,
            "pit_eligible": False,
            "disclosure_scope_identified": False,
            "weight_validation_passed": False,
            "disclosed_overlap_lower_bound_eligible": False,
            "exact_full_portfolio_overlap_eligible": False,
            "current_holdings_inference_eligible": False,
            "nowcast_eligible": False,
            "reason_codes": [],
        },
        "reason_codes": [],
        "scope": {
            "kind": "unknown",
            "completeness": "unknown",
            "basis": None,
            "quarter": None,
            "row_limit_semantics": "unknown",
        },
        "freshness": {
            "report_age_days": None,
            "available_age_days": None,
            "label": "unknown",
            "fresh_report_max_age_days": FRESH_REPORT_MAX_AGE_DAYS,
            "aging_report_max_age_days": AGING_REPORT_MAX_AGE_DAYS,
            "stale_blocks_valid_snapshot": False,
            "stale_blocks_disclosed_overlap_use": True,
            "decision_relative_not_snapshot_identity": True,
        },
        "coverage": {
            "disclosed_holding_count": 0,
            "weight_sum_percent": None,
            "portfolio_weight_coverage_percent": None,
            "coverage_ratio": None,
            "is_complete_security_list": False,
            "is_complete_fund_portfolio": False,
            "coverage_denominator": "fund_nav_percent",
            "duplicate_rows_collapsed": 0,
        },
        "holdings": [],
        "source": {
            "provider": str(source or "unknown").strip() or "unknown",
            "dataset": (
                "FundArchivesDatas.aspx:type=jjcc_raw"
                if str(source or "").strip() == _DEFAULT_LIVE_HOLDINGS_SOURCE
                else "fund_portfolio_hold_em"
            ),
            "availability_basis": "matched_report_announcement_only",
        },
        "source_refs": [],
        "source_hash": None,
        "snapshot_hash": None,
        "family_hint": _family_hint(family_hint),
        "policies": {
            "timezone": "Asia/Shanghai",
            "date_only_announcement_available_at": "next_calendar_day_00:00",
            "fetched_at_is_available_at": False,
            "future_pit_records": "drop",
            "q1_q3_scope": "quarterly_top10",
            "q2_q4_scope": "quarterly_top10_or_semiannual_annual_full_explicit",
            "eastmoney_starred_rows": (
                "exclude_as_issuer_shareholder_inference_or_fail_closed"
            ),
            "issuer_shareholder_inference_available_at": "never_inherit_fund_report",
            "family_merge": "never_from_unverified_hint",
            "stale_exposure_policy": (
                "valid_as_of_snapshot_retained_but_current_overlap_use_disabled"
            ),
            "freshness_labels": {
                "fresh": f"report_age_days<={FRESH_REPORT_MAX_AGE_DAYS}",
                "aging": (
                    f"{FRESH_REPORT_MAX_AGE_DAYS}<report_age_days"
                    f"<={AGING_REPORT_MAX_AGE_DAYS}"
                ),
                "stale": f"report_age_days>{AGING_REPORT_MAX_AGE_DAYS}",
            },
        },
        "audit": {
            "portfolio_rows_received": 0,
            "announcement_records_received": 0,
            "parsed_period_count": 0,
            "future_rows_dropped": 0,
            "future_announcements_dropped": 0,
            "exact_duplicate_rows_collapsed": 0,
            "announcement_parse_error_count": 0,
            "newer_periods_rejected": [],
            "fetched_at": _audit_text(fetched_at),
        },
    }


def _finish(
    payload: dict[str, Any],
    *,
    status: str,
    reasons: Sequence[str],
) -> dict[str, Any]:
    reason_codes = _unique(str(reason) for reason in reasons if reason)
    qualified = status == "qualified" and not reason_codes
    payload["status"] = "qualified" if qualified else status
    payload["qualified"] = qualified
    payload["reason_codes"] = reason_codes
    # Source validation is immutable and content-addressed.  The similarly
    # named top-level fields below are a decision-time view and may change when
    # the same disclosure is replayed at another clock.
    payload["source_validation"] = {
        "schema_version": "fund_holdings_source_validation.v1",
        "status": payload["status"],
        "qualified": qualified,
        "valid_snapshot": qualified,
        "available_at_known": bool(payload.get("available_at")),
        "disclosure_scope_identified": bool(
            qualified
            and isinstance(payload.get("scope"), Mapping)
            and payload["scope"].get("kind") in {"top10", "full_portfolio"}
        ),
        "weight_validation_passed": bool(qualified),
        "reason_codes": reason_codes,
    }
    payload["qualification"] = {
        "status": payload["status"],
        "qualified": qualified,
        "valid_snapshot": qualified,
        "pit_eligible": bool(qualified and payload.get("available_at")),
        "disclosure_scope_identified": bool(
            qualified
            and isinstance(payload.get("scope"), Mapping)
            and payload["scope"].get("kind") in {"top10", "full_portfolio"}
        ),
        "weight_validation_passed": bool(qualified),
        "disclosed_overlap_lower_bound_eligible": bool(
            qualified
            and isinstance(payload.get("scope"), Mapping)
            and payload["scope"].get("kind") in {"top10", "full_portfolio"}
            and isinstance(payload.get("freshness"), Mapping)
            and payload["freshness"].get("label") in {"fresh", "aging"}
        ),
        # Even a full equity disclosure omits cash, bonds, derivatives and
        # other assets, so it cannot authorize an exact whole-fund overlap.
        "exact_full_portfolio_overlap_eligible": False,
        # Periodic disclosures describe the report date, never current books.
        "current_holdings_inference_eligible": False,
        # Disclosed equity positions are not a complete current NAV book.
        "nowcast_eligible": False,
        "reason_codes": reason_codes,
    }
    payload["snapshot_hash"] = _snapshot_hash(payload)
    return payload


def _snapshot_hash(payload: Mapping[str, Any]) -> str:
    material = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "snapshot_hash",
            "decision_at",
            "first_observed_at",
            "audit",
            # These are the decision-relative projection.  Their immutable
            # source counterparts live in ``source_validation`` and remain in
            # the hash material, so callers cannot promote invalid evidence by
            # rewriting the view.
            "status",
            "qualified",
            "reason_codes",
            # Qualification contains decision-relative consumption gates (for
            # example stale overlap eligibility) and is reproducible from the
            # immutable disclosure plus the decision clock.
            "qualification",
            # Freshness is decision-relative.  Advancing the decision clock
            # must not create a different append-only source snapshot.
            "freshness",
        }
    }
    return _hash_material(material)


def compute_fund_holdings_snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    """Compute the canonical immutable identity for a snapshot payload."""

    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")
    return _snapshot_hash(snapshot)


def validate_fund_holdings_snapshot_hash(snapshot: Mapping[str, Any]) -> bool:
    """Return whether ``snapshot_hash`` matches the canonical static facts."""

    if not isinstance(snapshot, Mapping):
        return False
    expected = str(snapshot.get("snapshot_hash") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return False
    try:
        actual = _snapshot_hash(snapshot)
    except (TypeError, ValueError, OverflowError):
        # Hash validation is a trust-boundary predicate.  Non-JSON material
        # such as NaN/Infinity must be rejected, never escape as an exception.
        return False
    return expected == actual


def _selected_source_hash(payload: Mapping[str, Any]) -> str:
    return _hash_material(
        {
            "fund_code": payload.get("fund_code"),
            "report_period": payload.get("report_period"),
            "as_of_date": payload.get("as_of_date"),
            "available_at": payload.get("available_at"),
            "scope": payload.get("scope"),
            "holdings": payload.get("holdings"),
            "source": payload.get("source"),
            "source_refs": payload.get("source_refs"),
        }
    )


def _freshness(
    *,
    decision: _DecisionContext,
    as_of: date,
    available_at: datetime,
) -> dict[str, Any]:
    report_age_days = max((decision.moment.date() - as_of).days, 0)
    available_age_days = max(
        (decision.moment.date() - available_at.astimezone(CN_TZ).date()).days,
        0,
    )
    if report_age_days <= FRESH_REPORT_MAX_AGE_DAYS:
        label = "fresh"
    elif report_age_days <= AGING_REPORT_MAX_AGE_DAYS:
        label = "aging"
    else:
        label = "stale"
    return {
        "report_age_days": report_age_days,
        "available_age_days": available_age_days,
        "label": label,
        "fresh_report_max_age_days": FRESH_REPORT_MAX_AGE_DAYS,
        "aging_report_max_age_days": AGING_REPORT_MAX_AGE_DAYS,
        "stale_blocks_valid_snapshot": False,
        "stale_blocks_disclosed_overlap_use": True,
        "decision_relative_not_snapshot_identity": True,
    }


def _raw_source_hash(rows: list[dict[str, Any]], announcements: list[dict[str, Any]]) -> str:
    return _hash_material(
        {
            "portfolio_rows": _stable_raw_records(rows),
            "announcement_records": _stable_raw_records(announcements),
        }
    )


def _stable_raw_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = [_json_safe_mapping(row) for row in rows]
    return sorted(
        normalized,
        key=lambda row: json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _hash_material(material: object) -> str:
    return hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _resolve_decision(
    *,
    decision_at: str | datetime | None,
    decision_clock: DecisionClock | None,
) -> tuple[_DecisionContext | None, list[str]]:
    reasons: list[str] = []
    clock_moment: datetime | None = None
    if decision_clock is not None:
        clock_moment = getattr(decision_clock, "decision_at", None)
        if not isinstance(clock_moment, datetime):
            reasons.append("decision_clock_invalid")
            clock_moment = None
    explicit = _aware_datetime(decision_at) if decision_at is not None else None
    if decision_at is not None and explicit is None:
        reasons.append("decision_at_timezone_required")
    if clock_moment is not None:
        if clock_moment.tzinfo is None or clock_moment.utcoffset() is None:
            reasons.append("decision_clock_timezone_required")
            clock_moment = None
        else:
            clock_moment = clock_moment.astimezone(CN_TZ)
    if explicit is not None and clock_moment is not None:
        if explicit.astimezone(timezone.utc) != clock_moment.astimezone(timezone.utc):
            reasons.append("decision_clock_mismatch")
    moment = explicit or clock_moment
    if moment is None:
        if not reasons:
            reasons.append("decision_at_required")
        return None, _unique(reasons)
    try:
        session = build_trading_session(moment)
        canonical = datetime.fromisoformat(str(session["decision_at"]))
        effective = date.fromisoformat(str(session["effective_trade_date"]))
    except (KeyError, TypeError, ValueError):
        return None, _unique([*reasons, "decision_session_invalid"])
    return _DecisionContext(canonical, effective), _unique(reasons)


def _aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(CN_TZ)


def _publication_available_at(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=CN_TZ)
        return value.astimezone(CN_TZ)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=CN_TZ) + timedelta(days=1)
    text = str(value or "").strip()
    if not text:
        return None
    if _ISO_DATE_RE.fullmatch(text):
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError:
            return None
        return datetime.combine(parsed_date, time.min, tzinfo=CN_TZ) + timedelta(days=1)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def _row_available_at(raw: Mapping[str, Any]) -> tuple[datetime | None, bool]:
    value, present = _first_present(raw, _ROW_AVAILABLE_AT_KEYS)
    if not present:
        return None, False
    return _publication_available_at(value), True


def _row_periods(raw: Mapping[str, Any]) -> set[_Period]:
    values = [_first(raw, _PERIOD_KEYS), _first(raw, _AS_OF_KEYS)]
    return _periods_from_values(values)


def _periods_from_values(values: Sequence[object]) -> set[_Period]:
    output: set[_Period] = set()
    for value in values:
        period = _period_from_text(value)
        if period is not None:
            output.add(period)
    return output


def _period_from_text(value: object) -> _Period | None:
    if isinstance(value, datetime):
        return _period_from_date(value.date())
    if isinstance(value, date):
        return _period_from_date(value)
    text = str(value or "").strip()
    if not text:
        return None
    match = _Q_PREFIX_RE.search(text) or _QUARTER_RE.search(text)
    if match:
        quarter_raw = match.group("quarter")
        quarter = {"一": 1, "二": 2, "三": 3, "四": 4}.get(
            quarter_raw,
            int(quarter_raw) if quarter_raw.isdigit() else 0,
        )
        year = int(match.group("year"))
        if 1 <= quarter <= 4:
            return _Period(year, quarter)
    year_match = re.search(r"(?P<year>20\d{2})\s*年?\s*(?:中期|半年度|半年)", text)
    if year_match:
        return _Period(int(year_match.group("year")), 2)
    annual_match = re.search(r"(?P<year>20\d{2})\s*年(?:度)?(?:报告|年报)?", text)
    if annual_match and re.search(r"年度报告|年报|年度$", text):
        return _Period(int(annual_match.group("year")), 4)
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    return _period_from_date(parsed)


def _period_from_date(value: date) -> _Period | None:
    ends = {(3, 31): 1, (6, 30): 2, (9, 30): 3, (12, 31): 4}
    quarter = ends.get((value.month, value.day))
    return _Period(value.year, quarter) if quarter is not None else None


def _scope_hint(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return "unknown"
    if any(
        token in text
        for token in (
            "full",
            "semiannual",
            "semi-annual",
            "annual",
            "完整",
            "全量",
            "全部",
            "中报",
            "年报",
            "半年度",
        )
    ):
        return "full_portfolio"
    if any(token in text for token in ("top10", "top_10", "前十", "季度报告", "季报")):
        return "top10"
    return "unknown"


def _announcement_scope(*, title: str, period: _Period, explicit: object) -> str:
    hinted = _scope_hint(explicit)
    if hinted != "unknown":
        return hinted
    compact = re.sub(r"\s+", "", title).casefold()
    if re.search(r"中期报告|半年度报告|半年报", compact):
        return "full_portfolio" if period.quarter == 2 else "unknown"
    if re.search(r"年度报告|(?<!半)年报", compact):
        return "full_portfolio" if period.quarter == 4 else "unknown"
    if re.search(r"第?[一二三四1234]季度报告|[一二三四]季报", compact):
        return "top10"
    if period.quarter in {1, 3} and "季度" in compact and "报告" in compact:
        return "top10"
    return "unknown"


def _announcement_source_ref(item: _Announcement, *, source: str) -> dict[str, Any]:
    return {
        "kind": "fund_report_announcement",
        "source": str(source or "unknown").strip() or "unknown",
        "report_period": item.period.label,
        "disclosure_scope": item.scope,
        "announcement_id": item.announcement_id,
        "title": item.title,
        "url": item.url,
        "available_at": item.available_at.isoformat() if item.available_at else None,
        "raw_fields": item.raw,
    }


def _row_availability_source_ref(
    rows: Sequence[Mapping[str, Any]],
    *,
    period: _Period,
    source: str,
    effective_available_at: datetime,
) -> dict[str, Any]:
    records = sorted(
        (
            {
                "security_code": _security_code(_first(row, _CODE_KEYS)),
                "row_available_at": str(row.get("_row_available_at") or ""),
            }
            for row in rows
        ),
        key=lambda item: (str(item["security_code"]), item["row_available_at"]),
    )
    available_values = [
        value
        for item in records
        if (value := _aware_datetime(item["row_available_at"])) is not None
    ]
    return {
        "kind": "portfolio_row_availability",
        "source": str(source or "unknown").strip() or "unknown",
        "report_period": period.label,
        "row_count": len(records),
        "row_available_at_min": min(available_values).isoformat(),
        "row_available_at_max": max(available_values).isoformat(),
        "effective_available_at": effective_available_at.isoformat(),
        "availability_hash": _hash_material(records),
    }


def _family_hint(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    related = raw.get("related_codes")
    if not isinstance(related, Sequence) or isinstance(related, (str, bytes)):
        related = []
    codes = sorted({code for item in related if (code := _fund_code(item)) is not None})
    hinted_master = _text(raw.get("hinted_master_key") or raw.get("fund_master_key"))
    basis = _text(raw.get("basis") or raw.get("reason"))
    return {
        "status": "unverified_hint" if raw else "not_provided",
        "verified": False,
        "hard_merge_applied": False,
        "hinted_master_key": hinted_master,
        "related_codes": codes,
        "basis": basis,
    }


def _invalid_portfolio_provider_audit() -> dict[str, Any]:
    return {
        "schema_version": _EASTMONEY_RAW_PARSE_SCHEMA_VERSION,
        "status": "unavailable",
        "reason_codes": ["eastmoney_holdings_provider_audit_invalid"],
        "requested_years": [],
        "normalized_input_sha256": None,
        "fund_disclosure_sha256": None,
        "responses": [],
        "issuer_shareholder_inference": {
            "source_kind": _ISSUER_INFERENCE_SOURCE_KIND,
            "excluded_row_count": 0,
            "reason_codes": [],
            "inherits_fund_report_available_at": False,
            "participates_in_fund_disclosure": False,
            "participates_in_coverage_overlap_or_sector_inference": False,
        },
    }


def _portfolio_provider_audit(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    raw = value.get("provider_audit")
    if not isinstance(raw, Mapping):
        return None
    if raw.get("schema_version") != _EASTMONEY_RAW_PARSE_SCHEMA_VERSION:
        return _invalid_portfolio_provider_audit()
    provider_rows, provider_rows_reason = _records(value)
    if provider_rows_reason:
        return _invalid_portfolio_provider_audit()
    actual_rows_by_period: dict[str, list[dict[str, Any]]] = {}
    for provider_row in provider_rows:
        periods = _row_periods(provider_row)
        if len(periods) != 1:
            return _invalid_portfolio_provider_audit()
        period = next(iter(periods))
        actual_rows_by_period.setdefault(period.label, []).append(provider_row)
    status = str(raw.get("status") or "")
    if status not in {"qualified", "unavailable"}:
        return _invalid_portfolio_provider_audit()
    requested_raw = raw.get("requested_years")
    if not isinstance(requested_raw, Sequence) or isinstance(
        requested_raw, (str, bytes)
    ):
        return _invalid_portfolio_provider_audit()
    requested_years = sorted({str(item) for item in requested_raw})
    if len(requested_years) != len(requested_raw) or any(
        re.fullmatch(r"20\d{2}", year) is None for year in requested_years
    ):
        return _invalid_portfolio_provider_audit()
    responses_raw = raw.get("responses")
    if not isinstance(responses_raw, Sequence) or isinstance(
        responses_raw, (str, bytes)
    ):
        return _invalid_portfolio_provider_audit()
    responses: list[dict[str, Any]] = []
    response_years: set[str] = set()
    for response_raw in responses_raw:
        if not isinstance(response_raw, Mapping):
            return _invalid_portfolio_provider_audit()
        year = str(response_raw.get("year") or "")
        parser_status = str(response_raw.get("parser_status") or "")
        if (
            year not in requested_years
            or year in response_years
            or parser_status not in {"qualified", "unavailable"}
        ):
            return _invalid_portfolio_provider_audit()
        response_years.add(year)
        raw_hash = _sha256_hex(response_raw.get("raw_response_sha256"))
        normalized_hash = _sha256_hex(response_raw.get("normalized_input_sha256"))
        disclosure_hash = _sha256_hex(
            response_raw.get("fund_disclosure_sha256")
        )
        inference_hash = _sha256_hex(
            response_raw.get("issuer_shareholder_inference_sha256")
        )
        disclosure_count = _nonnegative_int(
            response_raw.get("fund_disclosure_row_count")
        )
        inference_count = _nonnegative_int(
            response_raw.get("issuer_shareholder_inference_excluded_count")
        )
        if raw_hash is None or disclosure_count is None or inference_count is None:
            return _invalid_portfolio_provider_audit()
        if parser_status == "qualified" and (
            normalized_hash is None
            or disclosure_hash is None
            or inference_hash is None
        ):
            return _invalid_portfolio_provider_audit()
        periods_raw = response_raw.get("periods")
        if not isinstance(periods_raw, Sequence) or isinstance(
            periods_raw, (str, bytes)
        ):
            return _invalid_portfolio_provider_audit()
        periods: list[dict[str, Any]] = []
        period_labels: set[str] = set()
        for period_raw in periods_raw:
            if not isinstance(period_raw, Mapping):
                return _invalid_portfolio_provider_audit()
            report_period = str(period_raw.get("report_period") or "")
            period_hash = _sha256_hex(period_raw.get("normalized_input_sha256"))
            period_disclosure_hash = _sha256_hex(
                period_raw.get("fund_disclosure_sha256")
            )
            period_inference_hash = _sha256_hex(
                period_raw.get("issuer_shareholder_inference_sha256")
            )
            period_disclosure_count = _nonnegative_int(
                period_raw.get("fund_disclosure_row_count")
            )
            period_inference_count = _nonnegative_int(
                period_raw.get("issuer_shareholder_inference_excluded_count")
            )
            if (
                re.fullmatch(rf"{re.escape(year)}-Q[1-4]", report_period) is None
                or report_period in period_labels
                or period_hash is None
                or period_disclosure_hash is None
                or period_inference_hash is None
                or period_disclosure_count is None
                or period_inference_count is None
            ):
                return _invalid_portfolio_provider_audit()
            period_labels.add(report_period)
            periods.append(
                {
                    "report_period": report_period,
                    "normalized_input_sha256": period_hash,
                    "fund_disclosure_sha256": period_disclosure_hash,
                    "fund_disclosure_row_count": period_disclosure_count,
                    "issuer_shareholder_inference_excluded_count": (
                        period_inference_count
                    ),
                    "issuer_shareholder_inference_sha256": period_inference_hash,
                }
            )
        periods.sort(key=lambda item: item["report_period"])
        if parser_status == "qualified" and (
            sum(item["fund_disclosure_row_count"] for item in periods)
            != disclosure_count
            or sum(
                item["issuer_shareholder_inference_excluded_count"]
                for item in periods
            )
            != inference_count
        ):
            return _invalid_portfolio_provider_audit()
        response_reasons = response_raw.get("reason_codes")
        if not isinstance(response_reasons, Sequence) or isinstance(
            response_reasons, (str, bytes)
        ):
            return _invalid_portfolio_provider_audit()
        responses.append(
            {
                "year": year,
                "parser_status": parser_status,
                "reason_codes": _unique(str(item) for item in response_reasons if item),
                "raw_response_sha256": raw_hash,
                "normalized_input_sha256": normalized_hash,
                "fund_disclosure_sha256": disclosure_hash,
                "fund_disclosure_row_count": disclosure_count,
                "issuer_shareholder_inference_excluded_count": inference_count,
                "issuer_shareholder_inference_sha256": inference_hash,
                "periods": periods,
            }
        )
    responses.sort(key=lambda item: item["year"])
    reason_codes_raw = raw.get("reason_codes")
    if not isinstance(reason_codes_raw, Sequence) or isinstance(
        reason_codes_raw, (str, bytes)
    ):
        return _invalid_portfolio_provider_audit()
    reason_codes = _unique(str(item) for item in reason_codes_raw if item)
    normalized_input_sha256 = _sha256_hex(raw.get("normalized_input_sha256"))
    fund_disclosure_sha256 = _sha256_hex(raw.get("fund_disclosure_sha256"))
    if status == "qualified":
        expected_aggregate = _hash_material(
            {
                "domain": "eastmoney_normalized_holdings_cross_year_input.v1",
                "responses": [
                    {
                        "year": item["year"],
                        "normalized_input_sha256": item["normalized_input_sha256"],
                    }
                    for item in responses
                ],
            }
        )
        expected_disclosure_aggregate = _hash_material(
            {
                "domain": "eastmoney_fund_disclosure_cross_year_input.v1",
                "responses": [
                    {
                        "year": item["year"],
                        "fund_disclosure_sha256": item[
                            "fund_disclosure_sha256"
                        ],
                    }
                    for item in responses
                ],
            }
        )
        if (
            reason_codes
            or response_years != set(requested_years)
            or any(item["parser_status"] != "qualified" for item in responses)
            or normalized_input_sha256 != expected_aggregate
            or fund_disclosure_sha256 != expected_disclosure_aggregate
        ):
            return _invalid_portfolio_provider_audit()
        audited_periods: set[str] = set()
        for response in responses:
            response_rows: list[dict[str, Any]] = []
            for period_evidence in response["periods"]:
                report_period = period_evidence["report_period"]
                audited_periods.add(report_period)
                actual_period_rows = actual_rows_by_period.get(report_period, [])
                if (
                    len(actual_period_rows)
                    != period_evidence["fund_disclosure_row_count"]
                    or _eastmoney_commitment(
                        domain="eastmoney_fund_disclosure_period_input.v1",
                        records=actual_period_rows,
                    )
                    != period_evidence["fund_disclosure_sha256"]
                ):
                    return _invalid_portfolio_provider_audit()
                response_rows.extend(actual_period_rows)
            if (
                len(response_rows) != response["fund_disclosure_row_count"]
                or _eastmoney_commitment(
                    domain="eastmoney_fund_disclosure_response_input.v1",
                    records=response_rows,
                )
                != response["fund_disclosure_sha256"]
            ):
                return _invalid_portfolio_provider_audit()
        if set(actual_rows_by_period) != audited_periods:
            return _invalid_portfolio_provider_audit()
    elif provider_rows:
        # A failed all-or-nothing transport/parse batch must never smuggle rows
        # alongside an unavailable audit envelope.
        return _invalid_portfolio_provider_audit()
    inference_raw = raw.get("issuer_shareholder_inference")
    if not isinstance(inference_raw, Mapping):
        return _invalid_portfolio_provider_audit()
    excluded_count = _nonnegative_int(inference_raw.get("excluded_row_count"))
    expected_excluded_count = sum(
        item["issuer_shareholder_inference_excluded_count"] for item in responses
    )
    if (
        inference_raw.get("source_kind") != _ISSUER_INFERENCE_SOURCE_KIND
        or excluded_count is None
        or excluded_count != expected_excluded_count
    ):
        return _invalid_portfolio_provider_audit()
    return {
        "schema_version": _EASTMONEY_RAW_PARSE_SCHEMA_VERSION,
        "status": status,
        "reason_codes": reason_codes,
        "requested_years": requested_years,
        "normalized_input_sha256": normalized_input_sha256,
        "fund_disclosure_sha256": fund_disclosure_sha256,
        "responses": responses,
        "issuer_shareholder_inference": {
            "source_kind": _ISSUER_INFERENCE_SOURCE_KIND,
            "excluded_row_count": excluded_count,
            "reason_codes": (
                [_ISSUER_INFERENCE_EXCLUDED_REASON] if excluded_count else []
            ),
            "inherits_fund_report_available_at": False,
            "participates_in_fund_disclosure": False,
            "participates_in_coverage_overlap_or_sector_inference": False,
        },
    }


def _portfolio_provider_failure_reasons(
    provider_audit: Mapping[str, Any] | None,
) -> list[str]:
    if provider_audit is None or provider_audit.get("status") == "qualified":
        return []
    reasons = provider_audit.get("reason_codes")
    if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)):
        return ["eastmoney_holdings_provider_audit_invalid"]
    output = _unique(str(item) for item in reasons if item)
    return output or ["eastmoney_holdings_provider_unavailable"]


def _portfolio_provider_source_ref(
    provider_audit: Mapping[str, Any] | None,
    *,
    period: _Period,
) -> tuple[dict[str, Any] | None, str | None]:
    if provider_audit is None:
        return None, None
    if provider_audit.get("status") != "qualified":
        return None, "eastmoney_holdings_provider_unavailable"
    matches: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    responses = provider_audit.get("responses")
    if not isinstance(responses, Sequence) or isinstance(responses, (str, bytes)):
        return None, "eastmoney_holdings_provider_audit_invalid"
    for response in responses:
        if not isinstance(response, Mapping):
            continue
        periods = response.get("periods")
        if not isinstance(periods, Sequence) or isinstance(periods, (str, bytes)):
            continue
        for period_evidence in periods:
            if (
                isinstance(period_evidence, Mapping)
                and period_evidence.get("report_period") == period.label
            ):
                matches.append((response, period_evidence))
    if len(matches) != 1:
        return None, "eastmoney_holdings_period_evidence_missing"
    response, period_evidence = matches[0]
    excluded_count = int(
        period_evidence["issuer_shareholder_inference_excluded_count"]
    )
    return (
        {
            "kind": "fund_holdings_raw_response_validation",
            "source": "eastmoney.FundArchivesDatas.aspx",
            "schema_version": _EASTMONEY_RAW_PARSE_SCHEMA_VERSION,
            "report_period": period.label,
            "raw_response_sha256": response["raw_response_sha256"],
            "response_normalized_input_sha256": response[
                "normalized_input_sha256"
            ],
            "response_fund_disclosure_sha256": response[
                "fund_disclosure_sha256"
            ],
            "normalized_input_sha256": period_evidence[
                "normalized_input_sha256"
            ],
            "fund_disclosure_sha256": period_evidence[
                "fund_disclosure_sha256"
            ],
            "fund_disclosure_row_count": period_evidence[
                "fund_disclosure_row_count"
            ],
            "excluded_source_kind": _ISSUER_INFERENCE_SOURCE_KIND,
            "issuer_shareholder_inference_excluded_count": excluded_count,
            "issuer_shareholder_inference_sha256": period_evidence[
                "issuer_shareholder_inference_sha256"
            ],
            "issuer_shareholder_inference_reason_codes": (
                [_ISSUER_INFERENCE_EXCLUDED_REASON] if excluded_count else []
            ),
            "inherits_fund_report_available_at": False,
            "participates_in_fund_disclosure": False,
            "participates_in_coverage_overlap_or_sector_inference": False,
        },
        None,
    )


def _records(value: object) -> tuple[list[dict[str, Any]], str | None]:
    if value is None:
        return [], None
    raw: object = value
    if isinstance(value, Mapping):
        for key in ("rows", "data", "items", "records", "holdings"):
            candidate = value.get(key)
            if candidate is not None:
                raw = candidate
                break
        else:
            raw = [value]
    elif hasattr(value, "to_dict"):
        try:
            raw = value.to_dict("records")
        except (TypeError, ValueError):
            return [], "provider_records_invalid"
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return [], "provider_records_invalid"
    output: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            return [], "provider_record_not_mapping"
        output.append(dict(item))
    return output, None


def _json_safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, raw in value.items():
        name = str(key)
        if name.casefold() in _OBSERVATION_ONLY_KEYS:
            continue
        if raw is None or isinstance(raw, (str, int, bool)):
            output[name] = raw
        elif isinstance(raw, float):
            output[name] = raw if math.isfinite(raw) else str(raw)
        elif isinstance(raw, (date, datetime)):
            output[name] = raw.isoformat()
        elif isinstance(raw, Mapping):
            output[name] = _json_safe_mapping(raw)
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            output[name] = [
                _json_safe_mapping(item) if isinstance(item, Mapping) else str(item)
                for item in raw
            ]
        else:
            output[name] = str(raw)
    return dict(sorted(output.items()))


def _first(mapping: Mapping[str, Any], keys: Sequence[str]) -> object:
    value, _present = _first_present(mapping, keys)
    return value


def _first_present(
    mapping: Mapping[str, Any],
    keys: Sequence[str],
) -> tuple[object, bool]:
    for key in keys:
        if key in mapping:
            return mapping[key], True
    normalized = {_normalize_key(key): value for key, value in mapping.items()}
    for key in keys:
        normalized_key = _normalize_key(key)
        if normalized_key in normalized:
            return normalized[normalized_key], True
    return None, False


def _normalize_key(value: object) -> str:
    return re.sub(r"[\s_%％()（）\-]", "", str(value or "")).casefold()


def _fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{1,6}(?:\.0+)?", text):
        return str(int(float(text))).zfill(6)
    return None


def _security_code(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    # A five-digit code is a valid Hong Kong identifier.  Preserve it exactly
    # instead of padding to six digits and accidentally reclassifying it as an
    # A-share in cross-market QDII research.
    if re.fullmatch(r"\d{5,6}", text):
        return text
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        digits = str(int(float(text)))
        if len(digits) <= 6:
            # Leading zeroes may already have been discarded by a numeric
            # dataframe/JSON conversion.  Preserve the observable value and
            # leave its market namespace unresolved instead of guessing.
            return digits
        return digits
    if not re.fullmatch(r"[A-Z0-9.\-]{1,32}", text):
        return None
    return text


def _static_security_identity(
    code: str,
    *,
    raw_value: object,
) -> tuple[str | None, str]:
    """Derive only the market namespace proven by the disclosed code shape.

    Six numeric digits are the mainland namespace and five numeric digits are
    the Hong Kong namespace.  This deliberately does not infer SSE/SZSE/BSE,
    and an alphabetic ticker remains unscoped without explicit exchange
    evidence.
    """

    # Numeric cells and decimal-looking strings may have lost significant
    # leading zeroes (for example HK 00700 -> 700).  Only an explicit string
    # with the complete width proves the namespace.
    if not isinstance(raw_value, str):
        return None, "unresolved_numeric_code_provenance"
    raw_text = raw_value.strip()
    if re.fullmatch(r"\d{6}", raw_text) and code == raw_text:
        return f"CN:{code}", "disclosed_code_format_cn_6_digit"
    if re.fullmatch(r"\d{5}", raw_text) and code == raw_text:
        return f"HK:{code}", "disclosed_code_format_hk_5_digit"
    return None, "unresolved_unscoped_disclosed_code"


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "").replace("％", "").replace("%", "")
    if not text or text.casefold() in {"nan", "none", "null", "--"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_int(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _audit_text(value: object) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return _text(value)


def _unique(values: Sequence[str] | Any) -> list[str]:
    output: list[str] = []
    for value in values:
        if value and value not in output:
            output.append(value)
    return output


def _is_invalid_reason(reason: str) -> bool:
    unavailable = {
        "announcement_after_decision",
        "matching_report_announcement_missing",
        "supporting_report_announcement_missing",
    }
    return reason not in unavailable


def _relevant_years(anchor: date) -> list[str]:
    return [str(anchor.year), str(anchor.year - 1)]


def _compact_source_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _sha256_hex(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else None


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def _eastmoney_parse_error(
    reason_code: str,
    *,
    raw_response_sha256: str | None,
) -> _EastmoneyPortfolioParseError:
    return _EastmoneyPortfolioParseError(
        reason_code,
        raw_response_sha256=raw_response_sha256,
    )


def _validate_eastmoney_content_type(
    value: object,
    *,
    raw_response_sha256: str,
) -> None:
    text = str(value or "").strip().lower()
    parts = [part.strip() for part in text.split(";") if part.strip()]
    mime = parts[0] if parts else ""
    charset_values = [
        part.split("=", 1)[1].strip().strip('"\'')
        for part in parts[1:]
        if part.startswith("charset=") and "=" in part
    ]
    if mime != "text/html" or len(charset_values) != 1:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_content_type_invalid",
            raw_response_sha256=raw_response_sha256,
        )
    if charset_values[0].replace("-", "") != "utf8":
        raise _eastmoney_parse_error(
            "eastmoney_holdings_charset_invalid",
            raw_response_sha256=raw_response_sha256,
        )


def _extract_eastmoney_html_content(
    data_text: str,
    *,
    expected_year: str,
    raw_response_sha256: str,
) -> str:
    prefix = re.search(r"\bvar\s+apidata\s*=\s*\{", data_text)
    if prefix is None:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_js_envelope_invalid",
            raw_response_sha256=raw_response_sha256,
        )
    field = re.match(r"\s*content\s*:\s*", data_text[prefix.end() :])
    if field is None:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_js_content_missing",
            raw_response_sha256=raw_response_sha256,
        )
    literal_start = prefix.end() + field.end()
    if literal_start >= len(data_text) or data_text[literal_start] != '"':
        raise _eastmoney_parse_error(
            "eastmoney_holdings_js_content_invalid",
            raw_response_sha256=raw_response_sha256,
        )
    escaped = False
    literal_end: int | None = None
    for index in range(literal_start + 1, len(data_text)):
        char = data_text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            literal_end = index
            break
    if literal_end is None:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_js_content_invalid",
            raw_response_sha256=raw_response_sha256,
        )
    try:
        content = json.loads(data_text[literal_start : literal_end + 1])
    except json.JSONDecodeError as exc:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_js_content_invalid",
            raw_response_sha256=raw_response_sha256,
        ) from exc
    if not isinstance(content, str):
        raise _eastmoney_parse_error(
            "eastmoney_holdings_js_content_invalid",
            raw_response_sha256=raw_response_sha256,
        )
    tail = data_text[literal_end + 1 :]
    years = re.findall(r"\bcuryear\s*:\s*(20\d{2})\b", tail)
    if years != [expected_year]:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_response_year_mismatch",
            raw_response_sha256=raw_response_sha256,
        )
    if not re.search(r"\}\s*;?\s*$", tail):
        raise _eastmoney_parse_error(
            "eastmoney_holdings_js_envelope_invalid",
            raw_response_sha256=raw_response_sha256,
        )
    return content


def _eastmoney_column_indexes(
    header_cells: Sequence[object],
    *,
    raw_response_sha256: str,
) -> dict[str, int]:
    names = [
        _normalize_key(getattr(cell, "get_text")(" ", strip=True))
        for cell in header_cells
    ]
    predicates = {
        "rank": lambda item: item == "序号",
        "code": lambda item: item in {"股票代码", "证券代码"},
        "name": lambda item: item in {"股票名称", "证券名称"},
        "weight": lambda item: item.startswith("占净值比例"),
        "shares": lambda item: item.startswith("持股数"),
        "market_value": lambda item: item.startswith("持仓市值"),
    }
    output: dict[str, int] = {}
    for field, predicate in predicates.items():
        matches = [index for index, name in enumerate(names) if predicate(name)]
        if len(matches) != 1:
            raise _eastmoney_parse_error(
                "eastmoney_holdings_html_header_invalid",
                raw_response_sha256=raw_response_sha256,
            )
        output[field] = matches[0]
    return output


def _eastmoney_commitment(
    *,
    domain: str,
    records: Sequence[Mapping[str, Any]],
) -> str:
    return _hash_material(
        {
            "domain": domain,
            "records": _stable_raw_records([dict(item) for item in records]),
        }
    )


def _parse_eastmoney_portfolio_response(
    raw_response: bytes,
    *,
    fund_code: str,
    expected_year: str,
    content_type: str,
) -> dict[str, Any]:
    """Parse raw Eastmoney markup while the original ``*`` rank survives.

    The returned evidence contains only one-way commitments and counts.  Raw
    HTML and excluded security records are intentionally not retained.
    """

    if not isinstance(raw_response, bytes):
        raise _EastmoneyPortfolioParseError(
            "eastmoney_holdings_response_bytes_required"
        )
    raw_response_sha256 = hashlib.sha256(raw_response).hexdigest()
    if len(raw_response) > _EASTMONEY_MAX_RESPONSE_BYTES:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_response_too_large",
            raw_response_sha256=raw_response_sha256,
        )
    if not re.fullmatch(r"20\d{2}", str(expected_year or "")):
        raise _eastmoney_parse_error(
            "eastmoney_holdings_requested_year_invalid",
            raw_response_sha256=raw_response_sha256,
        )
    _validate_eastmoney_content_type(
        content_type,
        raw_response_sha256=raw_response_sha256,
    )
    try:
        data_text = raw_response.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_charset_decode_failed",
            raw_response_sha256=raw_response_sha256,
        ) from exc
    content = _extract_eastmoney_html_content(
        data_text,
        expected_year=str(expected_year),
        raw_response_sha256=raw_response_sha256,
    )

    if not content.strip():
        empty_hash = _eastmoney_commitment(
            domain="eastmoney_normalized_holdings_input.v1",
            records=[],
        )
        return {
            "rows": [],
            "response_evidence": {
                "year": str(expected_year),
                "parser_status": "qualified",
                "reason_codes": [],
                "raw_response_sha256": raw_response_sha256,
                "normalized_input_sha256": empty_hash,
                "fund_disclosure_sha256": _eastmoney_commitment(
                    domain="eastmoney_fund_disclosure_response_input.v1",
                    records=[],
                ),
                "fund_disclosure_row_count": 0,
                "issuer_shareholder_inference_excluded_count": 0,
                "issuer_shareholder_inference_sha256": _eastmoney_commitment(
                    domain="eastmoney_issuer_shareholder_inference.v1",
                    records=[],
                ),
                "periods": [],
            },
        }

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, features="lxml")
    headings = list(soup.select("h4.t"))
    tables = list(soup.find_all("table"))
    if not headings or len(headings) != len(tables):
        raise _eastmoney_parse_error(
            "eastmoney_holdings_html_structure_invalid",
            raw_response_sha256=raw_response_sha256,
        )
    compact_page_text = _compact_source_text(soup.get_text(" ", strip=True))
    compact_star_footnote = _compact_source_text(_EASTMONEY_STAR_FOOTNOTE)
    exact_star_footnote_present = compact_star_footnote in compact_page_text
    star_footnote_marker_present = any(
        marker in compact_page_text
        for marker in (
            "加*号",
            "十大流通股东却没有进入单只基金前十大重仓股",
        )
    )

    disclosure_rows: list[dict[str, Any]] = []
    normalized_records: list[dict[str, Any]] = []
    inference_records: list[dict[str, Any]] = []
    period_records: dict[str, list[dict[str, Any]]] = {}
    period_disclosure_rows: dict[str, list[dict[str, Any]]] = {}
    period_inference_records: dict[str, list[dict[str, Any]]] = {}
    seen_periods: set[str] = set()
    for heading, table in zip(headings, tables, strict=True):
        if heading.find_next("table") is not table:
            raise _eastmoney_parse_error(
                "eastmoney_holdings_html_structure_invalid",
                raw_response_sha256=raw_response_sha256,
            )
        label = heading.get_text(" ", strip=True)
        period = _period_from_text(label)
        if period is None or str(period.year) != str(expected_year):
            raise _eastmoney_parse_error(
                "eastmoney_holdings_html_period_invalid",
                raw_response_sha256=raw_response_sha256,
            )
        if period.label in seen_periods:
            raise _eastmoney_parse_error(
                "eastmoney_holdings_html_period_duplicate",
                raw_response_sha256=raw_response_sha256,
            )
        seen_periods.add(period.label)
        table_rows = list(table.find_all("tr"))
        if len(table_rows) < 2:
            raise _eastmoney_parse_error(
                "eastmoney_holdings_html_table_empty",
                raw_response_sha256=raw_response_sha256,
            )
        header_cells = list(table_rows[0].find_all(["th", "td"]))
        indexes = _eastmoney_column_indexes(
            header_cells,
            raw_response_sha256=raw_response_sha256,
        )
        max_index = max(indexes.values())
        expected_rank = 1
        for table_row in table_rows[1:]:
            cells = list(table_row.find_all("td"))
            if len(cells) <= max_index:
                raise _eastmoney_parse_error(
                    "eastmoney_holdings_html_row_invalid",
                    raw_response_sha256=raw_response_sha256,
                )
            values = [cell.get_text(" ", strip=True) for cell in cells]
            raw_rank = re.sub(r"\s+", "", values[indexes["rank"]])
            rank_match = re.fullmatch(r"([1-9]\d*)(\*)?", raw_rank)
            if rank_match is None or int(rank_match.group(1)) != expected_rank:
                raise _eastmoney_parse_error(
                    "eastmoney_holdings_raw_rank_invalid",
                    raw_response_sha256=raw_response_sha256,
                )
            rank = expected_rank
            expected_rank += 1
            starred = rank_match.group(2) == "*"
            code = values[indexes["code"]].strip()
            name = values[indexes["name"]].strip()
            if not code or not name:
                raise _eastmoney_parse_error(
                    "eastmoney_holdings_html_row_invalid",
                    raw_response_sha256=raw_response_sha256,
                )
            normalized = {
                "report_period": period.label,
                "raw_rank": raw_rank,
                "rank": rank,
                "starred": starred,
                "source_kind": (
                    _ISSUER_INFERENCE_SOURCE_KIND if starred else "fund_disclosure"
                ),
                "security_code": code,
                "security_name": name,
                "weight": values[indexes["weight"]].strip(),
                "shares": values[indexes["shares"]].strip(),
                "market_value": values[indexes["market_value"]].strip(),
            }
            normalized_records.append(normalized)
            period_records.setdefault(period.label, []).append(normalized)
            if starred:
                inference_records.append(normalized)
                period_inference_records.setdefault(period.label, []).append(normalized)
                continue
            disclosure_row = {
                "fund_code": fund_code,
                "序号": rank,
                "股票代码": code,
                "股票名称": name,
                "占净值比例": values[indexes["weight"]].strip(),
                "持股数": values[indexes["shares"]].strip(),
                "持仓市值": values[indexes["market_value"]].strip(),
                "季度": label,
                "disclosure_source_kind": "fund_disclosure",
            }
            disclosure_rows.append(disclosure_row)
            period_disclosure_rows.setdefault(period.label, []).append(
                disclosure_row
            )

    if inference_records and not exact_star_footnote_present:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_star_footnote_missing",
            raw_response_sha256=raw_response_sha256,
        )
    if star_footnote_marker_present and not exact_star_footnote_present:
        raise _eastmoney_parse_error(
            "eastmoney_holdings_star_footnote_invalid",
            raw_response_sha256=raw_response_sha256,
        )

    period_evidence: list[dict[str, Any]] = []
    for period_label in sorted(period_records):
        records = period_records[period_label]
        disclosed = period_disclosure_rows.get(period_label, [])
        inferred = period_inference_records.get(period_label, [])
        period_evidence.append(
            {
                "report_period": period_label,
                "normalized_input_sha256": _eastmoney_commitment(
                    domain="eastmoney_normalized_holdings_period_input.v1",
                    records=records,
                ),
                "fund_disclosure_sha256": _eastmoney_commitment(
                    domain="eastmoney_fund_disclosure_period_input.v1",
                    records=disclosed,
                ),
                "fund_disclosure_row_count": sum(
                    item["source_kind"] == "fund_disclosure" for item in records
                ),
                "issuer_shareholder_inference_excluded_count": len(inferred),
                "issuer_shareholder_inference_sha256": _eastmoney_commitment(
                    domain="eastmoney_issuer_shareholder_inference.v1",
                    records=inferred,
                ),
            }
        )
    return {
        "rows": disclosure_rows,
        "response_evidence": {
            "year": str(expected_year),
            "parser_status": "qualified",
            "reason_codes": [],
            "raw_response_sha256": raw_response_sha256,
            "normalized_input_sha256": _eastmoney_commitment(
                domain="eastmoney_normalized_holdings_input.v1",
                records=normalized_records,
            ),
            "fund_disclosure_sha256": _eastmoney_commitment(
                domain="eastmoney_fund_disclosure_response_input.v1",
                records=disclosure_rows,
            ),
            "fund_disclosure_row_count": len(disclosure_rows),
            "issuer_shareholder_inference_excluded_count": len(inference_records),
            "issuer_shareholder_inference_sha256": _eastmoney_commitment(
                domain="eastmoney_issuer_shareholder_inference.v1",
                records=inference_records,
            ),
            "periods": period_evidence,
        },
    }


def _eastmoney_provider_payload(
    *,
    years: Sequence[str],
    parsed_responses: Sequence[Mapping[str, Any]],
    reason_codes: Sequence[str] = (),
) -> dict[str, Any]:
    ordered_years = sorted({str(year) for year in years})
    failed = _unique(str(reason) for reason in reason_codes if reason)
    rows: list[dict[str, Any]] = []
    if not failed:
        for response in parsed_responses:
            response_rows = response.get("rows")
            if isinstance(response_rows, Sequence) and not isinstance(
                response_rows, (str, bytes)
            ):
                rows.extend(
                    dict(item) for item in response_rows if isinstance(item, Mapping)
                )
    evidence_rows = [
        dict(response["response_evidence"])
        for response in parsed_responses
        if isinstance(response.get("response_evidence"), Mapping)
    ]
    evidence_rows = sorted(evidence_rows, key=lambda item: str(item.get("year") or ""))
    excluded_count = sum(
        _nonnegative_int(item.get("issuer_shareholder_inference_excluded_count")) or 0
        for item in evidence_rows
    )
    normalized_hashes = [
        {
            "year": item.get("year"),
            "normalized_input_sha256": item.get("normalized_input_sha256"),
        }
        for item in evidence_rows
    ]
    disclosure_hashes = [
        {
            "year": item.get("year"),
            "fund_disclosure_sha256": item.get("fund_disclosure_sha256"),
        }
        for item in evidence_rows
    ]
    audit = {
        "schema_version": _EASTMONEY_RAW_PARSE_SCHEMA_VERSION,
        "status": "unavailable" if failed else "qualified",
        "reason_codes": failed,
        "requested_years": ordered_years,
        "normalized_input_sha256": (
            None
            if failed or len(evidence_rows) != len(ordered_years)
            else _hash_material(
                {
                    "domain": "eastmoney_normalized_holdings_cross_year_input.v1",
                    "responses": normalized_hashes,
                }
            )
        ),
        "fund_disclosure_sha256": (
            None
            if failed or len(evidence_rows) != len(ordered_years)
            else _hash_material(
                {
                    "domain": "eastmoney_fund_disclosure_cross_year_input.v1",
                    "responses": disclosure_hashes,
                }
            )
        ),
        "responses": evidence_rows,
        "issuer_shareholder_inference": {
            "source_kind": _ISSUER_INFERENCE_SOURCE_KIND,
            "excluded_row_count": excluded_count,
            "reason_codes": (
                [_ISSUER_INFERENCE_EXCLUDED_REASON] if excluded_count else []
            ),
            "inherits_fund_report_available_at": False,
            "participates_in_fund_disclosure": False,
            "participates_in_coverage_overlap_or_sector_inference": False,
        },
    }
    return {"rows": [] if failed else rows, "provider_audit": audit}


def _eastmoney_unavailable_provider_payload(
    *,
    years: Sequence[str],
    reason_code: str,
    response_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    parsed = [
        {"rows": [], "response_evidence": dict(item)} for item in response_evidence
    ]
    return _eastmoney_provider_payload(
        years=years,
        parsed_responses=parsed,
        reason_codes=[reason_code],
    )


def _default_portfolio_rows_fetcher(
    fund_code: str,
    *,
    years: Sequence[str],
    decision_at: datetime,
) -> dict[str, Any]:
    del decision_at
    from app.services.akshare_subprocess import run_akshare_json_script

    requested_years = sorted(
        {
            str(year).strip()
            for year in years
            if re.fullmatch(r"20\d{2}", str(year).strip())
        }
    )
    if not requested_years or len(requested_years) != len(set(str(y) for y in years)):
        return _eastmoney_unavailable_provider_payload(
            years=requested_years,
            reason_code="eastmoney_holdings_requested_year_invalid",
        )
    code_json = json.dumps(fund_code, ensure_ascii=True)
    years_json = json.dumps(requested_years, ensure_ascii=True)
    script = f"""
import base64
import hashlib
import json
import requests

fund_code = {code_json}
years = {years_json}
url = {_EASTMONEY_PORTFOLIO_URL!r}
max_bytes = {_EASTMONEY_MAX_RESPONSE_BYTES}
headers = {{
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://fundf10.eastmoney.com/ccmx_" + fund_code + ".html",
}}
responses = []

try:
    for year in years:
        response = requests.get(
            url,
            params={{
                "type": "jjcc",
                "code": fund_code,
                "topline": "10000",
                "year": year,
                "month": "",
                "rt": "0.913877030254846",
            }},
            headers=headers,
            timeout=(5, 15),
        )
        response.raise_for_status()
        body = response.content
        digest = hashlib.sha256(body).hexdigest()
        entry = {{
            "year": year,
            "content_type": response.headers.get("Content-Type"),
            "raw_response_bytes": len(body),
            "raw_response_sha256": digest,
        }}
        if len(body) > max_bytes:
            entry["transport_reason_code"] = "eastmoney_holdings_response_too_large"
        else:
            entry["body_base64"] = base64.b64encode(body).decode("ascii")
        responses.append(entry)
        if entry.get("transport_reason_code"):
            break
    print(json.dumps({{"responses": responses}}, ensure_ascii=True))
except Exception:
    print(json.dumps({{
        "provider_reason_code": "eastmoney_holdings_request_failed",
        "responses": responses,
    }}, ensure_ascii=True))
"""
    payload = run_akshare_json_script(
        script,
        label=f"fund_holdings_snapshot:{fund_code}",
        timeout=45,
        warn_on_failure=False,
    )
    if not isinstance(payload, Mapping):
        return _eastmoney_unavailable_provider_payload(
            years=requested_years,
            reason_code="eastmoney_holdings_request_failed",
        )
    provider_reason = _text(payload.get("provider_reason_code"))
    response_items = payload.get("responses")
    if not isinstance(response_items, Sequence) or isinstance(
        response_items, (str, bytes)
    ):
        return _eastmoney_unavailable_provider_payload(
            years=requested_years,
            reason_code=provider_reason or "eastmoney_holdings_provider_payload_invalid",
        )
    response_by_year: dict[str, Mapping[str, Any]] = {}
    for item in response_items:
        if not isinstance(item, Mapping):
            return _eastmoney_unavailable_provider_payload(
                years=requested_years,
                reason_code="eastmoney_holdings_provider_payload_invalid",
            )
        year = str(item.get("year") or "")
        if year not in requested_years or year in response_by_year:
            return _eastmoney_unavailable_provider_payload(
                years=requested_years,
                reason_code="eastmoney_holdings_provider_payload_invalid",
            )
        response_by_year[year] = item
    if provider_reason:
        completed_evidence = []
        for year in sorted(response_by_year):
            raw_hash = _sha256_hex(
                response_by_year[year].get("raw_response_sha256")
            )
            if raw_hash is None:
                continue
            completed_evidence.append(
                {
                    "year": year,
                    "parser_status": "unavailable",
                    "reason_codes": [provider_reason],
                    "raw_response_sha256": raw_hash,
                    "normalized_input_sha256": None,
                    "fund_disclosure_sha256": None,
                    "fund_disclosure_row_count": 0,
                    "issuer_shareholder_inference_excluded_count": 0,
                    "issuer_shareholder_inference_sha256": None,
                    "periods": [],
                }
            )
        return _eastmoney_unavailable_provider_payload(
            years=requested_years,
            reason_code=provider_reason,
            response_evidence=completed_evidence,
        )
    if sorted(response_by_year) != requested_years:
        return _eastmoney_unavailable_provider_payload(
            years=requested_years,
            reason_code="eastmoney_holdings_provider_payload_incomplete",
        )

    parsed_responses: list[dict[str, Any]] = []
    for year in requested_years:
        item = response_by_year[year]
        transport_reason = _text(item.get("transport_reason_code"))
        child_hash = _sha256_hex(item.get("raw_response_sha256"))
        if transport_reason:
            evidence = {
                "year": year,
                "parser_status": "unavailable",
                "reason_codes": [transport_reason],
                "raw_response_sha256": child_hash,
                "normalized_input_sha256": None,
                "fund_disclosure_sha256": None,
                "fund_disclosure_row_count": 0,
                "issuer_shareholder_inference_excluded_count": 0,
                "issuer_shareholder_inference_sha256": None,
                "periods": [],
            }
            return _eastmoney_unavailable_provider_payload(
                years=requested_years,
                reason_code=transport_reason,
                response_evidence=[
                    *(
                        dict(item["response_evidence"])
                        for item in parsed_responses
                        if isinstance(item.get("response_evidence"), Mapping)
                    ),
                    evidence,
                ],
            )
        encoded = item.get("body_base64")
        if not isinstance(encoded, str):
            return _eastmoney_unavailable_provider_payload(
                years=requested_years,
                reason_code="eastmoney_holdings_provider_payload_invalid",
            )
        try:
            raw_response = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return _eastmoney_unavailable_provider_payload(
                years=requested_years,
                reason_code="eastmoney_holdings_provider_payload_invalid",
            )
        actual_hash = hashlib.sha256(raw_response).hexdigest()
        reported_size = _nonnegative_int(item.get("raw_response_bytes"))
        if child_hash != actual_hash or reported_size != len(raw_response):
            return _eastmoney_unavailable_provider_payload(
                years=requested_years,
                reason_code="eastmoney_holdings_response_integrity_invalid",
            )
        try:
            parsed = _parse_eastmoney_portfolio_response(
                raw_response,
                fund_code=fund_code,
                expected_year=year,
                content_type=str(item.get("content_type") or ""),
            )
        except _EastmoneyPortfolioParseError as exc:
            evidence = {
                "year": year,
                "parser_status": "unavailable",
                "reason_codes": [exc.reason_code],
                "raw_response_sha256": exc.raw_response_sha256 or actual_hash,
                "normalized_input_sha256": None,
                "fund_disclosure_sha256": None,
                "fund_disclosure_row_count": 0,
                "issuer_shareholder_inference_excluded_count": 0,
                "issuer_shareholder_inference_sha256": None,
                "periods": [],
            }
            return _eastmoney_unavailable_provider_payload(
                years=requested_years,
                reason_code=exc.reason_code,
                response_evidence=[
                    *(
                        dict(item["response_evidence"])
                        for item in parsed_responses
                        if isinstance(item.get("response_evidence"), Mapping)
                    ),
                    evidence,
                ],
            )
        parsed_responses.append(parsed)
    return _eastmoney_provider_payload(
        years=requested_years,
        parsed_responses=parsed_responses,
    )


def _default_announcements_fetcher(
    fund_code: str,
    *,
    limit: int,
    decision_at: datetime,
) -> list[dict[str, Any]]:
    del decision_at
    from app.services.eastmoney_news_client import fetch_fund_announcement_report_em

    return fetch_fund_announcement_report_em(fund_code, limit=limit)


__all__ = [
    "AGING_REPORT_MAX_AGE_DAYS",
    "DEFAULT_LIVE_FETCH_DECISION_SKEW_SECONDS",
    "DEFAULT_WEIGHT_TOLERANCE_PERCENT",
    "FRESH_REPORT_MAX_AGE_DAYS",
    "HOLDINGS_SNAPSHOT_SCHEMA_VERSION",
    "build_fund_holdings_snapshot",
    "compute_fund_holdings_snapshot_hash",
    "materialize_fund_holdings_snapshot_for_decision",
    "resolve_fund_holdings_snapshot",
    "validate_fund_holdings_snapshot_hash",
]
