"""Deterministic point-in-time fund holdings look-through research.

The module consumes frozen ``fund_holdings_snapshot.v1`` payloads and user
position truth.  It deliberately computes *disclosed lower bounds* only:
periodic fund reports omit part of the portfolio and therefore can never
support an exact whole-portfolio overlap claim.

No provider, database, or LLM is used here.  Security identities are joined
only through an explicit ``security_id`` or through an explicitly evidenced
``listing_market`` plus ``security_code`` pair.  Missing identities and
classifications remain unknown mass instead of being guessed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.fund_holdings_snapshot import (
    AGING_REPORT_MAX_AGE_DAYS,
    FRESH_REPORT_MAX_AGE_DAYS,
    HOLDINGS_SNAPSHOT_SCHEMA_VERSION,
    materialize_fund_holdings_snapshot_for_decision,
    validate_fund_holdings_snapshot_hash,
)


LOOKTHROUGH_RESEARCH_SCHEMA_VERSION = "fund_lookthrough_research.v1"
CN_TZ = ZoneInfo("Asia/Shanghai")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_WEIGHT_TOLERANCE = 0.01
_DEFAULT_TOP_EXPOSURES = 20
_DEFAULT_TOP_COMMON = 10
_SAME_RUN_OBSERVATION_WINDOW_SECONDS = 30 * 60
_MIN_RISK_GUARD_DISCLOSED_MASS_PERCENT = 20.0
_MIN_RISK_GUARD_IDENTITY_RATIO = 0.60


@dataclass(frozen=True)
class _Security:
    key: str
    label: str
    weight_percent: float
    industry: str | None
    listing_market: str | None


@dataclass(frozen=True)
class _PreparedSnapshot:
    fund_code: str
    aggregation_key: str
    master_key_verified: bool
    snapshot_hash: str
    report_period: str | None
    as_of_date: str
    available_at: str
    first_observed_at: str | None
    observation_status: str
    replay_eligible: bool
    scope_kind: str
    current_freshness_label: str
    current_report_age_days: int
    eligible: bool
    invalid: bool
    reason_codes: tuple[str, ...]
    disclosed_mass_percent: float
    securities: dict[str, _Security]
    industry_weights: dict[str, float]
    market_weights: dict[str, float]

    @property
    def identified_mass_percent(self) -> float:
        return _round(sum(item.weight_percent for item in self.securities.values()))

    @property
    def unknown_mass_percent(self) -> float:
        return _round(max(100.0 - self.identified_mass_percent, 0.0))

    @property
    def identity_coverage_ratio(self) -> float | None:
        if self.disclosed_mass_percent <= 0:
            return None
        return _round(self.identified_mass_percent / self.disclosed_mass_percent)


def build_fund_lookthrough_research(
    existing_snapshots: object,
    user_holdings: object,
    candidate_snapshots: object,
    *,
    decision_at: str | datetime,
    portfolio_positions_complete: bool | None = None,
    portfolio_denominator_yuan: object = None,
    portfolio_denominator_source: Mapping[str, Any] | None = None,
    current_run_observation: Mapping[str, Any] | None = None,
    top_exposure_limit: int = _DEFAULT_TOP_EXPOSURES,
    top_common_limit: int = _DEFAULT_TOP_COMMON,
) -> dict[str, Any]:
    """Build disclosed fund and portfolio overlap lower bounds.

    ``portfolio_positions_complete`` must be explicitly true, and a complete
    account denominator must have PIT-usable source evidence, before the
    result can be execution-qualified.  Without that denominator the function
    uses the sum of supplied fund positions only and labels every exposure
    ``fund_holdings_only``.
    """

    decision = _aware_datetime(decision_at)
    base = _base_payload(decision)
    config_reasons = _validate_limits(top_exposure_limit, top_common_limit)
    if decision is None:
        config_reasons.append("decision_at_timezone_required")
    if current_run_observation is not None and not isinstance(
        current_run_observation, Mapping
    ):
        config_reasons.append("current_run_observation_invalid")
    if config_reasons:
        return _finish(base, status="invalid", reasons=config_reasons)

    assert decision is not None
    base["input_audit"]["current_run_observation_ref"] = (
        _text(current_run_observation.get("ref_id"))
        if isinstance(current_run_observation, Mapping)
        else None
    )
    positions, position_meta, position_reasons = _normalize_positions(
        user_holdings,
        explicit_complete=portfolio_positions_complete,
        decision=decision,
    )
    base["input_audit"]["user_position_count"] = len(positions)
    base["input_audit"]["position_truth_pit_qualified"] = bool(
        position_meta.get("position_truth_pit_qualified") is True
    )
    if position_reasons:
        return _finish(base, status="invalid", reasons=position_reasons)

    existing_rows, existing_reasons = _snapshot_records(existing_snapshots)
    candidate_rows, candidate_reasons = _snapshot_records(candidate_snapshots)
    if existing_reasons or candidate_reasons:
        return _finish(
            base,
            status="invalid",
            reasons=[*existing_reasons, *candidate_reasons],
        )

    prepared_existing, existing_prepare_reasons = _prepare_snapshot_set(
        existing_rows,
        decision=decision,
        role="existing",
        current_run_observation=current_run_observation,
    )
    prepared_candidates, candidate_prepare_reasons = _prepare_snapshot_set(
        candidate_rows,
        decision=decision,
        role="candidate",
        current_run_observation=current_run_observation,
    )
    base["input_audit"].update(
        {
            "existing_snapshot_count": len(prepared_existing),
            "candidate_snapshot_count": len(prepared_candidates),
        }
    )
    candidate_overlap_requested = bool(candidate_rows)
    base["scope"] = (
        "portfolio_and_candidates"
        if candidate_overlap_requested
        else "portfolio_only"
    )
    base["capabilities"] = {
        "portfolio_lookthrough": {"status": "requested"},
        "candidate_overlap": {
            "status": "requested" if candidate_overlap_requested else "not_requested"
        },
    }
    structural_reasons = [
        *existing_prepare_reasons,
        *candidate_prepare_reasons,
    ]
    if structural_reasons:
        return _finish(base, status="invalid", reasons=structural_reasons)

    denominator = _resolve_denominator(
        positions=positions,
        embedded_meta=position_meta,
        explicit_amount=portfolio_denominator_yuan,
        explicit_source=portfolio_denominator_source,
        decision=decision,
    )
    if denominator["invalid_reason"]:
        return _finish(
            base,
            status="invalid",
            reasons=[str(denominator["invalid_reason"])],
        )

    by_code = {item.fund_code: item for item in prepared_existing}
    total_fund_amount = _round(sum(item["holding_amount"] for item in positions))
    analysis_denominator = float(denominator["analysis_denominator_yuan"] or 0.0)
    portfolio_scope = str(denominator["scope"])

    security_exposure: dict[str, dict[str, Any]] = {}
    security_exposure_by_vintage: dict[str, dict[str, dict[str, Any]]] = {}
    industry_exposure: dict[str, float] = {}
    market_exposure: dict[str, float] = {}
    existing_output: list[dict[str, Any]] = []
    snapshot_covered_amount = 0.0
    disclosed_account_mass = 0.0
    identity_known_account_mass = 0.0

    for position in positions:
        fund_code = position["fund_code"]
        amount = float(position["holding_amount"])
        snapshot = by_code.get(fund_code)
        portfolio_weight = (
            amount / analysis_denominator * 100.0 if analysis_denominator > 0 else 0.0
        )
        row = {
            "fund_code": fund_code,
            "holding_amount_yuan": _round(amount),
            "portfolio_weight_percent": _round(portfolio_weight),
            "exposure_scope": portfolio_scope,
            "snapshot": _snapshot_summary(snapshot),
        }
        if snapshot is None or not snapshot.eligible:
            row["status"] = "unavailable"
            row["reason_codes"] = (
                list(snapshot.reason_codes)
                if snapshot is not None
                else ["eligible_snapshot_missing"]
            )
            row["lookthrough"] = None
            existing_output.append(row)
            continue

        snapshot_covered_amount += amount
        disclosed_account_mass += portfolio_weight * snapshot.disclosed_mass_percent / 100.0
        identity_known_account_mass += (
            portfolio_weight * snapshot.identified_mass_percent / 100.0
        )
        for security in snapshot.securities.values():
            contribution = portfolio_weight * security.weight_percent / 100.0
            target = security_exposure.setdefault(
                security.key,
                {
                    "security_key": security.key,
                    "security_name": security.label,
                    "exposure_lower_bound_percent": 0.0,
                },
            )
            target["exposure_lower_bound_percent"] += contribution
            vintage_target = security_exposure_by_vintage.setdefault(
                snapshot.as_of_date,
                {},
            ).setdefault(
                security.key,
                {
                    "security_key": security.key,
                    "security_name": security.label,
                    "exposure_lower_bound_percent": 0.0,
                },
            )
            vintage_target["exposure_lower_bound_percent"] += contribution
        for industry, weight in snapshot.industry_weights.items():
            industry_exposure[industry] = industry_exposure.get(industry, 0.0) + (
                portfolio_weight * weight / 100.0
            )
        for market, weight in snapshot.market_weights.items():
            market_exposure[market] = market_exposure.get(market, 0.0) + (
                portfolio_weight * weight / 100.0
            )
        row["status"] = "qualified"
        row["reason_codes"] = []
        row["lookthrough"] = _single_fund_summary(snapshot, top_exposure_limit)
        existing_output.append(row)

    security_rows = sorted(
        (
            {
                **value,
                "exposure_lower_bound_percent": _round(
                    value["exposure_lower_bound_percent"]
                ),
            }
            for value in security_exposure.values()
        ),
        key=lambda item: (-item["exposure_lower_bound_percent"], item["security_key"]),
    )
    industry_rows = _exposure_rows(industry_exposure, "industry")
    market_rows = _exposure_rows(market_exposure, "listing_market")
    known_denominator = bool(denominator["whole_account_denominator_qualified"])
    position_groups = _fund_position_groups(
        positions,
        by_code=by_code,
        denominator=analysis_denominator,
    )
    base["portfolio"] = {
        "scope": portfolio_scope,
        "portfolio_positions_complete": bool(position_meta["positions_complete"]),
        "position_truth_pit_qualified": bool(
            position_meta.get("position_truth_pit_qualified") is True
        ),
        "position_truth_source": position_meta.get("source_audit"),
        "fund_holding_amount_yuan": total_fund_amount,
        "analysis_denominator_yuan": _round(analysis_denominator),
        "whole_account_denominator_yuan": denominator["whole_account_denominator_yuan"],
        "denominator_source": denominator["source_audit"],
        "whole_account_denominator_qualified": known_denominator,
        "fund_position_mass_percent": (
            _round(total_fund_amount / analysis_denominator * 100.0)
            if analysis_denominator > 0
            else None
        ),
        "non_fund_or_cash_mass_percent": (
            _round(max(100.0 - total_fund_amount / analysis_denominator * 100.0, 0.0))
            if known_denominator and analysis_denominator > 0
            else None
        ),
        "snapshot_covered_fund_amount_yuan": _round(snapshot_covered_amount),
        "snapshot_coverage_of_fund_amount_percent": (
            _round(snapshot_covered_amount / total_fund_amount * 100.0)
            if total_fund_amount > 0
            else None
        ),
        "fund_position_groups": position_groups,
        "disclosed_security_mass_lower_bound_percent": _round(disclosed_account_mass),
        "identity_known_security_mass_lower_bound_percent": _round(
            identity_known_account_mass
        ),
        "unknown_account_mass_percent": (
            _round(max(100.0 - identity_known_account_mass, 0.0))
            if known_denominator
            else None
        ),
        "unknown_fund_holdings_scope_mass_percent": (
            _round(max(100.0 - identity_known_account_mass, 0.0))
            if portfolio_scope == "fund_holdings_only" and analysis_denominator > 0
            else None
        ),
        "security_exposure_lower_bounds": security_rows,
        "industry_exposure_lower_bounds": industry_rows,
        "industry_unknown_mass_percent": _classification_unknown_mass(
            industry_rows,
            known_denominator=known_denominator,
            scope=portfolio_scope,
        ),
        "listing_market_exposure_lower_bounds": market_rows,
        "listing_market_unknown_mass_percent": _classification_unknown_mass(
            market_rows,
            known_denominator=known_denominator,
            scope=portfolio_scope,
        ),
        "exact_full_portfolio_exposure_eligible": False,
    }
    base["existing_funds"] = sorted(existing_output, key=lambda item: item["fund_code"])

    position_codes = {item["fund_code"] for item in positions}
    missing_position_snapshots = any(
        by_code.get(code) is None or not by_code[code].eligible for code in position_codes
    )
    truth_complete = bool(position_meta["positions_complete"])
    position_truth_pit_qualified = bool(
        position_meta.get("position_truth_pit_qualified") is True
    )
    existing_snapshots_replay_qualified = all(
        by_code.get(code) is not None
        and by_code[code].eligible
        and by_code[code].replay_eligible
        for code in position_codes
    )
    portfolio_execution_qualified = bool(
        truth_complete
        and position_truth_pit_qualified
        and known_denominator
        and analysis_denominator > 0
        and not missing_position_snapshots
        and existing_snapshots_replay_qualified
    )
    base["portfolio_execution_qualified"] = portfolio_execution_qualified

    candidates_output: list[dict[str, Any]] = []
    for candidate in prepared_candidates:
        portfolio_alignment = _vintage_alignment(
            [candidate.as_of_date],
            list(security_exposure_by_vintage),
        )
        candidate_row = {
            "fund_code": candidate.fund_code,
            "aggregation_key": candidate.aggregation_key,
            "master_key_verified": candidate.master_key_verified,
            "snapshot": _snapshot_summary(candidate),
            "coverage": _coverage_summary(candidate),
            "vintage_alignment": portfolio_alignment,
            "exposure_scope": portfolio_scope,
            "exact_full_portfolio_overlap_percent": None,
            "exact_full_portfolio_overlap_eligible": False,
        }
        if not candidate.eligible:
            unavailable_capabilities = _decision_capabilities(
                research_eligible=False,
                risk_guard_eligible=False,
                reasons=candidate.reason_codes,
            )
            candidate_row.update(
                {
                    "status": "unavailable",
                    "execution_qualified": False,
                    "reason_codes": list(candidate.reason_codes),
                    "capabilities": unavailable_capabilities,
                    "decision_use": unavailable_capabilities,
                    "research_eligible": False,
                    "concentration_risk_guard_eligible": False,
                    "allocation_authorization_eligible": False,
                    "lookthrough": None,
                    "existing_fund_overlaps": [],
                    "max_existing_fund_overlap_lower_bound": None,
                    "max_existing_fund_overlap_lower_bound_percent": None,
                    "portfolio_security_overlap_lower_bound": None,
                    "portfolio_security_overlap_lower_bound_percent": None,
                    "reported_as_of_disclosed_overlap_percent": None,
                    "common_disclosed_weight_percent": 0.0,
                    "cross_vintage_disclosed_similarity_percent": None,
                    "overlap_evidence_state": "snapshot_unavailable",
                    "portfolio_overlap_interpretation": "snapshot_not_eligible",
                    "top_common_with_portfolio": [],
                }
            )
            candidates_output.append(candidate_row)
            continue

        pair_rows: list[dict[str, Any]] = []
        for position in positions:
            existing = by_code.get(position["fund_code"])
            if existing is None or not existing.eligible:
                continue
            pair = _pair_overlap(existing, candidate, top_common_limit)
            pair.update(
                {
                    "existing_fund_code": existing.fund_code,
                    "existing_aggregation_key": existing.aggregation_key,
                    "existing_holding_amount_yuan": position["holding_amount"],
                }
            )
            pair_rows.append(pair)
        pair_rows.sort(
            key=lambda item: (
                0
                if isinstance(item.get("overlap_lower_bound_percent"), (int, float))
                else 1,
                -float(item["overlap_lower_bound_percent"])
                if isinstance(item.get("overlap_lower_bound_percent"), (int, float))
                else 0.0,
                item["existing_fund_code"],
            )
        )
        portfolio_overlap = _portfolio_overlap(
            candidate,
            security_exposure,
            security_exposure_by_vintage,
            top_common_limit,
            portfolio_disclosed_mass=disclosed_account_mass,
            portfolio_identified_mass=identity_known_account_mass,
            portfolio_replay_eligible=existing_snapshots_replay_qualified,
        )
        numeric_pair_overlaps = [
            float(item["overlap_lower_bound_percent"])
            for item in pair_rows
            if isinstance(item.get("overlap_lower_bound_percent"), (int, float))
        ]
        max_existing_overlap = (
            max(numeric_pair_overlaps) if numeric_pair_overlaps else None
        )
        pair_guard_eligible = any(
            isinstance(item.get("capabilities"), Mapping)
            and item["capabilities"].get("concentration_risk_guard_eligible") is True
            for item in pair_rows
        )
        portfolio_guard_eligible = bool(
            isinstance(portfolio_overlap.get("capabilities"), Mapping)
            and portfolio_overlap["capabilities"].get(
                "concentration_risk_guard_eligible"
            )
            is True
        )
        risk_guard_eligible = bool(
            position_truth_pit_qualified
            and candidate.replay_eligible
            and portfolio_overlap["vintage_alignment"].get("status")
            == "same_as_of_date"
            and (pair_guard_eligible or portfolio_guard_eligible)
        )
        capability_reasons = _candidate_capability_reasons(
            candidate=candidate,
            position_truth_pit_qualified=position_truth_pit_qualified,
            pair_guard_eligible=pair_guard_eligible,
            portfolio_guard_eligible=portfolio_guard_eligible,
            portfolio_alignment=portfolio_overlap["vintage_alignment"],
            has_reported_overlap=bool(numeric_pair_overlaps)
            or isinstance(
                portfolio_overlap.get("overlap_lower_bound_percent"),
                (int, float),
            ),
        )
        capability_reasons = _unique(
            [
                *capability_reasons,
                *_execution_reasons(
                    truth_complete=truth_complete,
                    position_truth_pit_qualified=position_truth_pit_qualified,
                    denominator_qualified=known_denominator,
                    missing_snapshots=missing_position_snapshots,
                    snapshots_replay_qualified=existing_snapshots_replay_qualified,
                ),
            ]
        )
        candidate_capabilities = _decision_capabilities(
            research_eligible=True,
            risk_guard_eligible=risk_guard_eligible,
            reasons=capability_reasons,
        )
        candidate_row.update(
            {
                "status": "qualified",
                # Compatibility field: overlap research never authorizes an
                # allocation, even when portfolio truth itself is complete.
                "execution_qualified": False,
                "reason_codes": capability_reasons,
                "capabilities": candidate_capabilities,
                "decision_use": candidate_capabilities,
                "research_eligible": candidate_capabilities["research_eligible"],
                "concentration_risk_guard_eligible": candidate_capabilities[
                    "concentration_risk_guard_eligible"
                ],
                "allocation_authorization_eligible": False,
                "lookthrough": _single_fund_summary(candidate, top_exposure_limit),
                "existing_fund_overlaps": pair_rows,
                "max_existing_fund_overlap_lower_bound": max_existing_overlap,
                "max_existing_fund_overlap_lower_bound_percent": max_existing_overlap,
                "portfolio_security_overlap_lower_bound": portfolio_overlap[
                    "overlap_lower_bound_percent"
                ],
                "portfolio_security_overlap_lower_bound_percent": portfolio_overlap[
                    "overlap_lower_bound_percent"
                ],
                "reported_as_of_disclosed_overlap_percent": portfolio_overlap[
                    "overlap_lower_bound_percent"
                ],
                "common_disclosed_weight_percent": portfolio_overlap[
                    "common_disclosed_weight_percent"
                ],
                "cross_vintage_disclosed_similarity_percent": portfolio_overlap[
                    "cross_vintage_disclosed_similarity_percent"
                ],
                "vintage_alignment": portfolio_overlap["vintage_alignment"],
                "overlap_evidence_state": _overlap_evidence_state(
                    portfolio_overlap
                ),
                "portfolio_overlap_interpretation": portfolio_overlap[
                    "interpretation"
                ],
                "top_common_with_portfolio": portfolio_overlap["top_common_securities"],
            }
        )
        candidates_output.append(candidate_row)

    base["candidates"] = sorted(candidates_output, key=lambda item: item["fund_code"])
    # C2 produces research and one-way concentration guards only.  It never
    # grants authority to allocate capital.
    base["execution_qualified"] = False
    base["decision_use"] = _decision_capabilities(
        research_eligible=bool(prepared_existing),
        risk_guard_eligible=any(
            isinstance(item.get("capabilities"), Mapping)
            and item["capabilities"].get("concentration_risk_guard_eligible") is True
            for item in candidates_output
        ),
        reasons=_execution_reasons(
            truth_complete=truth_complete,
            position_truth_pit_qualified=position_truth_pit_qualified,
            denominator_qualified=known_denominator,
            missing_snapshots=missing_position_snapshots,
            snapshots_replay_qualified=existing_snapshots_replay_qualified,
        ),
    )
    unavailable_count = sum(
        not item.eligible for item in [*prepared_existing, *prepared_candidates]
    )
    if not positions or total_fund_amount <= 0:
        return _finish(base, status="unavailable", reasons=["fund_positions_missing"])
    if not candidate_overlap_requested:
        base["capabilities"]["portfolio_lookthrough"]["status"] = (
            "partial" if unavailable_count or missing_position_snapshots else "qualified"
        )
        if unavailable_count or missing_position_snapshots:
            return _finish(
                base,
                status="partial",
                reasons=["some_snapshots_not_eligible"],
            )
        return _finish(base, status="qualified", reasons=[])
    base["capabilities"]["candidate_overlap"]["status"] = (
        "qualified" if any(item.eligible for item in prepared_candidates) else "unavailable"
    )
    base["capabilities"]["portfolio_lookthrough"]["status"] = (
        "partial" if missing_position_snapshots else "qualified"
    )
    if not prepared_candidates:
        return _finish(base, status="unavailable", reasons=["candidate_snapshots_missing"])
    if not any(item.eligible for item in prepared_candidates):
        return _finish(
            base,
            status="unavailable",
            reasons=["eligible_candidate_snapshot_missing"],
        )
    if unavailable_count or missing_position_snapshots:
        return _finish(
            base,
            status="partial",
            reasons=["some_snapshots_not_eligible"],
        )
    return _finish(base, status="qualified", reasons=[])


def compact_fund_lookthrough_for_llm(
    value: Mapping[str, Any] | None,
    *,
    max_candidates: int = 8,
    max_common_per_candidate: int = 5,
    max_exposures: int = 8,
) -> dict[str, Any]:
    """Return a bounded summary that never contains source snapshot holdings."""

    if not isinstance(value, Mapping):
        return {
            "schema_version": LOOKTHROUGH_RESEARCH_SCHEMA_VERSION,
            "status": "unavailable",
            "reason_codes": ["lookthrough_research_missing"],
            "raw_holdings_included": False,
        }
    limits = (max_candidates, max_common_per_candidate, max_exposures)
    if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in limits):
        return {
            "schema_version": LOOKTHROUGH_RESEARCH_SCHEMA_VERSION,
            "status": "invalid",
            "reason_codes": ["compact_limit_invalid"],
            "raw_holdings_included": False,
        }
    portfolio = value.get("portfolio") if isinstance(value.get("portfolio"), Mapping) else {}
    candidates = value.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        candidates = []
    compact_candidates: list[dict[str, Any]] = []
    for raw in list(candidates)[:max_candidates]:
        if not isinstance(raw, Mapping):
            continue
        pairs = raw.get("existing_fund_overlaps")
        if not isinstance(pairs, Sequence) or isinstance(pairs, (str, bytes)):
            pairs = []
        compact_candidates.append(
            {
                "fund_code": _compact_text(raw.get("fund_code")),
                "status": _compact_text(raw.get("status")),
                "execution_qualified": raw.get("execution_qualified") is True,
                "reason_codes": _compact_reason_codes(raw.get("reason_codes")),
                "capabilities": _compact_decision_capabilities(
                    raw.get("capabilities")
                ),
                "decision_use": _compact_decision_capabilities(
                    raw.get("decision_use") or raw.get("capabilities")
                ),
                "vintage_alignment": _compact_vintage_alignment(
                    raw.get("vintage_alignment")
                ),
                "coverage": _compact_coverage(raw.get("coverage")),
                "max_existing_fund_overlap_lower_bound_percent": _compact_number(raw.get(
                    "max_existing_fund_overlap_lower_bound_percent"
                )),
                "max_existing_fund_overlap_lower_bound": _compact_number(raw.get(
                    "max_existing_fund_overlap_lower_bound"
                )),
                "portfolio_security_overlap_lower_bound_percent": _compact_number(raw.get(
                    "portfolio_security_overlap_lower_bound_percent"
                )),
                "portfolio_security_overlap_lower_bound": _compact_number(raw.get(
                    "portfolio_security_overlap_lower_bound"
                )),
                "reported_as_of_disclosed_overlap_percent": _compact_number(
                    raw.get("reported_as_of_disclosed_overlap_percent")
                ),
                "overlap_evidence_state": _compact_text(
                    raw.get("overlap_evidence_state"),
                    max_length=64,
                ),
                "common_disclosed_weight_percent": _compact_number(
                    raw.get("common_disclosed_weight_percent")
                ),
                "cross_vintage_disclosed_similarity_percent": _compact_number(
                    raw.get("cross_vintage_disclosed_similarity_percent")
                ),
                "portfolio_overlap_interpretation": _compact_text(raw.get(
                    "portfolio_overlap_interpretation"
                )),
                "top_common_with_portfolio": _compact_common_rows(
                    raw.get("top_common_with_portfolio"),
                    limit=max_common_per_candidate,
                ),
                "top_existing_fund_overlaps": _compact_pair_rows(
                    pairs,
                    limit=max_common_per_candidate,
                ),
            }
        )
    return {
        "schema_version": LOOKTHROUGH_RESEARCH_SCHEMA_VERSION,
        "status": _compact_text(value.get("status")),
        "scope": _compact_text(value.get("scope")),
        "research_qualified": value.get("research_qualified") is True,
        "execution_qualified": value.get("execution_qualified") is True,
        "portfolio_execution_qualified": value.get("portfolio_execution_qualified") is True,
        "reason_codes": _compact_reason_codes(value.get("reason_codes")),
        "qualification": _compact_qualification(value.get("qualification")),
        "capabilities": _compact_structural_capabilities(value.get("capabilities")),
        "decision_use": _compact_decision_capabilities(value.get("decision_use")),
        "research_hash": _compact_hash(value.get("research_hash")),
        "portfolio": {
            "scope": _compact_text(portfolio.get("scope")),
            "portfolio_positions_complete": portfolio.get(
                "portfolio_positions_complete"
            ) is True,
            "position_truth_pit_qualified": portfolio.get(
                "position_truth_pit_qualified"
            ) is True,
            "whole_account_denominator_qualified": portfolio.get(
                "whole_account_denominator_qualified"
            ) is True,
            "snapshot_coverage_of_fund_amount_percent": _compact_number(portfolio.get(
                "snapshot_coverage_of_fund_amount_percent"
            )),
            "disclosed_security_mass_lower_bound_percent": _compact_number(
                portfolio.get("disclosed_security_mass_lower_bound_percent")
            ),
            "identity_known_security_mass_lower_bound_percent": _compact_number(portfolio.get(
                "identity_known_security_mass_lower_bound_percent"
            )),
            "unknown_account_mass_percent": _compact_number(
                portfolio.get("unknown_account_mass_percent")
            ),
            "unknown_fund_holdings_scope_mass_percent": _compact_number(portfolio.get(
                "unknown_fund_holdings_scope_mass_percent"
            )),
            "industry_unknown_mass_percent": _compact_number(
                portfolio.get("industry_unknown_mass_percent")
            ),
            "listing_market_unknown_mass_percent": _compact_number(
                portfolio.get("listing_market_unknown_mass_percent")
            ),
            "top_security_exposure_lower_bounds": _compact_exposure_rows(
                portfolio.get("security_exposure_lower_bounds"),
                label_keys=("security_key", "security_name"),
                limit=max_exposures,
            ),
            "top_industry_exposure_lower_bounds": _compact_exposure_rows(
                portfolio.get("industry_exposure_lower_bounds"),
                label_keys=("industry",),
                limit=max_exposures,
            ),
            "top_listing_market_exposure_lower_bounds": _compact_exposure_rows(
                portfolio.get("listing_market_exposure_lower_bounds"),
                label_keys=("listing_market",),
                limit=max_exposures,
            ),
        },
        "candidates": compact_candidates,
        "disclaimer": (
            "Only disclosed-scope lower bounds; unknown mass is retained and "
            "zero exact full-portfolio overlap is never inferred."
        ),
        "raw_holdings_included": False,
    }


def _compact_text(value: object, *, max_length: int = 256) -> str | None:
    text = _text(value)
    return text[:max_length] if text is not None else None


def _compact_hash(value: object) -> str | None:
    text = _compact_text(value, max_length=64)
    return text if text is not None and _HASH_RE.fullmatch(text.lower()) else None


def _compact_number(value: object) -> float | None:
    number = _finite_number(value)
    return _round(number) if number is not None else None


def _compact_reason_codes(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return _unique(
        text
        for item in list(value)[:50]
        if (text := _compact_text(item, max_length=128)) is not None
    )


def _compact_decision_capabilities(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "research_eligible": raw.get("research_eligible") is True,
        "concentration_risk_guard_eligible": raw.get(
            "concentration_risk_guard_eligible"
        )
        is True,
        "allocation_authorization_eligible": False,
        "reason_codes": _compact_reason_codes(raw.get("reason_codes")),
    }


def _compact_structural_capabilities(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    output: dict[str, Any] = {}
    for key in ("portfolio_lookthrough", "candidate_overlap"):
        item = raw.get(key)
        item = item if isinstance(item, Mapping) else {}
        output[key] = {"status": _compact_text(item.get("status"), max_length=32)}
    return output


def _compact_qualification(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "research_qualified": raw.get("research_qualified") is True,
        "execution_qualified": raw.get("execution_qualified") is True,
        "reason_codes": _compact_reason_codes(raw.get("reason_codes")),
    }


def _compact_vintage_alignment(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    raw_status = _compact_text(raw.get("status"), max_length=32)
    status = (
        raw_status
        if raw_status in {"same_as_of_date", "cross_vintage", "mixed"}
        else "mixed"
    )
    gap_number = _compact_number(raw.get("gap_days"))
    gap_days = int(gap_number) if gap_number is not None and gap_number >= 0 else None
    dates_raw = raw.get("as_of_dates")
    dates: list[str] = []
    if isinstance(dates_raw, Sequence) and not isinstance(dates_raw, (str, bytes)):
        for item in list(dates_raw)[:16]:
            parsed = _iso_date(item)
            if parsed is not None:
                dates.append(parsed.isoformat())
    return {
        "status": status,
        "gap_days": gap_days,
        "as_of_dates": sorted(set(dates)),
    }


def _compact_coverage(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        key: _compact_number(raw.get(key))
        for key in (
            "disclosed_mass_percent",
            "identified_mass_percent",
            "identity_coverage_ratio",
            "unknown_mass_percent",
            "minimum_risk_guard_disclosed_mass_percent",
            "minimum_risk_guard_identity_ratio",
        )
    }


def _compact_common_rows(value: object, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    rows: list[dict[str, Any]] = []
    for item in list(value)[:limit]:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "security_key": _compact_text(item.get("security_key")),
                "security_name": _compact_text(item.get("security_name")),
                "portfolio_exposure_lower_bound_percent": _compact_number(
                    item.get("portfolio_exposure_lower_bound_percent")
                ),
                "existing_weight_percent": _compact_number(
                    item.get("existing_weight_percent")
                ),
                "candidate_weight_percent": _compact_number(
                    item.get("candidate_weight_percent")
                ),
                "overlap_contribution_lower_bound_percent": _compact_number(
                    item.get("overlap_contribution_lower_bound_percent")
                ),
            }
        )
    return rows


def _compact_pair_rows(value: object, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    rows: list[dict[str, Any]] = []
    for item in list(value)[:limit]:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "existing_fund_code": _compact_text(item.get("existing_fund_code")),
                "overlap_lower_bound_percent": _compact_number(
                    item.get("overlap_lower_bound_percent")
                ),
                "common_disclosed_weight_percent": _compact_number(
                    item.get("common_disclosed_weight_percent")
                ),
                "cross_vintage_disclosed_similarity_percent": _compact_number(
                    item.get("cross_vintage_disclosed_similarity_percent")
                ),
                "interpretation": _compact_text(item.get("interpretation")),
                "vintage_alignment": _compact_vintage_alignment(
                    item.get("vintage_alignment")
                ),
                "capabilities": _compact_decision_capabilities(
                    item.get("capabilities")
                ),
                "reason_codes": _compact_reason_codes(item.get("reason_codes")),
                "left_disclosed_mass_percent": _compact_number(
                    item.get("left_disclosed_mass_percent")
                ),
                "left_identity_coverage_ratio": _compact_number(
                    item.get("left_identity_coverage_ratio")
                ),
                "right_disclosed_mass_percent": _compact_number(
                    item.get("right_disclosed_mass_percent")
                ),
                "right_identity_coverage_ratio": _compact_number(
                    item.get("right_identity_coverage_ratio")
                ),
            }
        )
    return rows


def _compact_exposure_rows(
    value: object,
    *,
    label_keys: Sequence[str],
    limit: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    rows: list[dict[str, Any]] = []
    for item in list(value)[:limit]:
        if not isinstance(item, Mapping):
            continue
        row = {
            key: _compact_text(item.get(key))
            for key in label_keys
        }
        row["exposure_lower_bound_percent"] = _compact_number(
            item.get("exposure_lower_bound_percent")
        )
        rows.append(row)
    return rows


# More explicit alias for callers that prefer the full contract name.
compact_fund_lookthrough_research_for_llm = compact_fund_lookthrough_for_llm


def _prepare_snapshot_set(
    rows: list[dict[str, Any]],
    *,
    decision: datetime,
    role: str,
    current_run_observation: Mapping[str, Any] | None,
) -> tuple[list[_PreparedSnapshot], list[str]]:
    prepared: list[_PreparedSnapshot] = []
    reasons: list[str] = []
    seen: dict[str, tuple[str, str]] = {}
    for raw in rows:
        item = _prepare_snapshot(
            raw,
            decision=decision,
            current_run_observation=current_run_observation,
        )
        if item.invalid:
            reasons.extend(f"{role}_{reason}" for reason in item.reason_codes)
        fingerprint = _prepared_snapshot_fingerprint(item)
        existing = seen.get(item.fund_code)
        if existing is not None:
            if existing != (item.snapshot_hash, fingerprint):
                reasons.append(f"{role}_snapshot_duplicate_conflict")
            continue
        seen[item.fund_code] = (item.snapshot_hash, fingerprint)
        prepared.append(item)
    prepared.sort(key=lambda item: item.fund_code)
    return prepared, _unique(reasons)


def _prepare_snapshot(
    raw: Mapping[str, Any],
    *,
    decision: datetime,
    current_run_observation: Mapping[str, Any] | None,
) -> _PreparedSnapshot:
    reasons: list[str] = []
    ineligible: list[str] = []
    snapshot_hash = str(raw.get("snapshot_hash") or "").strip().lower()
    hash_shape_valid = bool(_HASH_RE.fullmatch(snapshot_hash))
    try:
        hash_valid = hash_shape_valid and validate_fund_holdings_snapshot_hash(raw)
    except (TypeError, ValueError, OverflowError):
        # Malformed non-finite material must fail closed instead of escaping
        # through the canonical JSON hasher.
        hash_valid = False
    if not hash_shape_valid:
        reasons.append("snapshot_hash_invalid")
    elif not hash_valid:
        reasons.append("snapshot_hash_mismatch")
    if hash_valid:
        try:
            view: Mapping[str, Any] = materialize_fund_holdings_snapshot_for_decision(
                raw,
                decision_at=decision,
            )
        except (TypeError, ValueError):
            view = raw
            reasons.append("snapshot_materialization_failed")
    else:
        # No static field from a hash-mismatched payload is allowed to feed a
        # lower-bound calculation.  The remaining parsing below is audit-only;
        # the whole research result will fail before any metric is emitted.
        view = raw

    fund_code = _fund_code(view.get("fund_code"))
    if fund_code is None:
        reasons.append("snapshot_fund_code_invalid")
        fund_code = "unknown"
    if view.get("schema_version") != HOLDINGS_SNAPSHOT_SCHEMA_VERSION:
        reasons.append("snapshot_schema_invalid")

    as_of = _iso_date(view.get("as_of_date"))
    available = _aware_datetime(view.get("available_at"))
    (
        first_observed,
        observation_status,
        observation_research_eligible,
        observation_replay_eligible,
        observation_reason,
    ) = _snapshot_observation(
        view,
        decision=decision,
        current_run_observation=current_run_observation,
    )
    if as_of is None:
        reasons.append("snapshot_as_of_date_invalid")
    if available is None:
        reasons.append("snapshot_available_at_timezone_required")
    if as_of is not None and as_of > decision.astimezone(CN_TZ).date():
        ineligible.append("snapshot_as_of_after_decision")
    if available is not None and available > decision:
        ineligible.append("snapshot_available_after_decision")
    if not observation_research_eligible:
        ineligible.append(observation_reason or "snapshot_observation_not_qualified")

    report_age = (
        max((decision.astimezone(CN_TZ).date() - as_of).days, 0)
        if as_of is not None
        else 10**9
    )
    freshness = (
        "fresh"
        if report_age <= FRESH_REPORT_MAX_AGE_DAYS
        else "aging"
        if report_age <= AGING_REPORT_MAX_AGE_DAYS
        else "stale"
    )
    if freshness == "stale":
        ineligible.append("snapshot_stale_at_decision")

    qualification = view.get("qualification")
    if view.get("status") != "qualified" or view.get("qualified") is not True:
        ineligible.append("snapshot_status_not_qualified")
        ineligible.extend(str(item) for item in view.get("reason_codes") or [] if item)
    if not isinstance(qualification, Mapping):
        ineligible.append("snapshot_qualification_missing")
    elif qualification.get("disclosed_overlap_lower_bound_eligible") is not True:
        ineligible.append(
            "snapshot_stale_at_decision"
            if freshness == "stale"
            else "snapshot_disclosed_overlap_not_eligible"
        )
    scope = view.get("scope")
    scope_kind = str(scope.get("kind") or "unknown") if isinstance(scope, Mapping) else "unknown"
    if scope_kind not in {"top10", "full_portfolio"}:
        ineligible.append("snapshot_disclosure_scope_invalid")

    holdings = view.get("holdings")
    if not isinstance(holdings, Sequence) or isinstance(holdings, (str, bytes)):
        reasons.append("snapshot_holdings_invalid")
        holdings = []
    securities: dict[str, _Security] = {}
    industry_weights: dict[str, float] = {}
    market_weights: dict[str, float] = {}
    disclosed_mass = 0.0
    seen_explicit_security_ids: set[str] = set()
    seen_listing_ids: set[str] = set()
    for row in holdings:
        if not isinstance(row, Mapping):
            reasons.append("snapshot_holding_not_mapping")
            continue
        weight = _finite_number(row.get("weight_percent"))
        if weight is None:
            reasons.append("snapshot_holding_weight_invalid")
            continue
        if weight < 0 or weight > 100:
            reasons.append("snapshot_holding_weight_out_of_range")
            continue
        disclosed_mass += weight
        explicit_security_id = _text(row.get("security_id"))
        listed_market = _classification(row, "listing_market", decision=decision)
        listed_code = _text(row.get("security_code"))
        explicit_key = (
            explicit_security_id.upper() if explicit_security_id is not None else None
        )
        listing_key = (
            f"{listed_market.upper()}:{listed_code.upper()}"
            if listed_market is not None and listed_code is not None
            else None
        )
        if explicit_key is not None and explicit_key in seen_explicit_security_ids:
            reasons.append("snapshot_holding_duplicate_identity_conflict")
            continue
        if listing_key is not None and listing_key in seen_listing_ids:
            reasons.append("snapshot_holding_duplicate_identity_conflict")
            continue
        if explicit_key is not None:
            seen_explicit_security_ids.add(explicit_key)
        if listing_key is not None:
            seen_listing_ids.add(listing_key)
        identity, listing_market, identity_reason = _security_identity(row, decision=decision)
        if identity_reason:
            # Missing/ambiguous identity is unknown mass, not a guessed join.
            identity = None
        industry = _classification(row, "industry", decision=decision)
        market_for_exposure = listing_market
        if identity is not None:
            if identity in securities:
                reasons.append("snapshot_holding_duplicate_identity_conflict")
                continue
            securities[identity] = _Security(
                key=identity,
                label=_text(row.get("security_name")) or identity,
                weight_percent=_round(weight),
                industry=industry,
                listing_market=market_for_exposure,
            )
        if industry is not None:
            industry_weights[industry] = industry_weights.get(industry, 0.0) + weight
        if market_for_exposure is not None:
            market_weights[market_for_exposure] = (
                market_weights.get(market_for_exposure, 0.0) + weight
            )
    if disclosed_mass > 100.0 + _WEIGHT_TOLERANCE:
        reasons.append("snapshot_holding_weight_sum_above_100")
    coverage = view.get("coverage")
    if isinstance(coverage, Mapping) and coverage.get("weight_sum_percent") is not None:
        stated = _finite_number(coverage.get("weight_sum_percent"))
        if stated is None or abs(stated - disclosed_mass) > _WEIGHT_TOLERANCE:
            reasons.append("snapshot_coverage_weight_sum_conflict")

    aggregation_key, verified = _aggregation_identity(view, fund_code, decision=decision)
    invalid = bool(reasons)
    eligible = not invalid and not ineligible
    return _PreparedSnapshot(
        fund_code=fund_code,
        aggregation_key=aggregation_key,
        master_key_verified=verified,
        snapshot_hash=snapshot_hash,
        report_period=_text(view.get("report_period")),
        as_of_date=as_of.isoformat() if as_of is not None else "",
        available_at=available.isoformat() if available is not None else "",
        first_observed_at=(
            first_observed.isoformat() if first_observed is not None else None
        ),
        observation_status=observation_status,
        replay_eligible=observation_replay_eligible,
        scope_kind=scope_kind,
        current_freshness_label=freshness,
        current_report_age_days=report_age,
        eligible=eligible,
        invalid=invalid,
        reason_codes=tuple(_unique([*reasons, *ineligible])),
        disclosed_mass_percent=_round(disclosed_mass),
        securities=securities,
        industry_weights={key: _round(value) for key, value in industry_weights.items()},
        market_weights={key: _round(value) for key, value in market_weights.items()},
    )


def _security_identity(
    row: Mapping[str, Any],
    *,
    decision: datetime,
) -> tuple[str | None, str | None, str | None]:
    security_id = _text(row.get("security_id"))
    market = _classification(row, "listing_market", decision=decision)
    code = _text(row.get("security_code"))
    if security_id:
        identity = f"security_id:{security_id.upper()}"
        # When both identifiers are present, retaining both prevents a
        # conflicting listing/code assertion elsewhere from being silently
        # joined merely because the free-form security_id happens to match.
        if market and code:
            identity += f"|listing:{market.upper()}:{code.upper()}"
        return identity, market, None
    if market and code:
        return f"listing:{market.upper()}:{code.upper()}", market, None
    return None, market, "security_identity_unknown"


def _classification(
    row: Mapping[str, Any],
    field: str,
    *,
    decision: datetime,
) -> str | None:
    raw = row.get(field)
    evidence: object = row.get(f"{field}_evidence")
    value: object = raw
    if isinstance(raw, Mapping):
        value = raw.get("value") or raw.get("name") or raw.get(field)
        evidence = raw.get("evidence") or raw
    label = _text(value)
    if label is None or not _pit_evidence_usable(evidence, decision=decision):
        return None
    return label.upper() if field == "listing_market" else label


def _aggregation_identity(
    raw: Mapping[str, Any],
    fund_code: str,
    *,
    decision: datetime,
) -> tuple[str, bool]:
    master = _text(raw.get("fund_master_key"))
    if master is None or master == fund_code:
        return fund_code, False
    proof = raw.get("master_key_verification") or raw.get("family_identity")
    if not isinstance(proof, Mapping):
        return fund_code, False
    proof_master = _text(proof.get("master_key") or proof.get("fund_master_key"))
    if (
        proof.get("verified") is True
        and proof_master == master
        and _pit_evidence_usable(proof, decision=decision)
    ):
        return master, True
    return fund_code, False


def _coverage_summary(snapshot: _PreparedSnapshot) -> dict[str, Any]:
    return {
        "disclosed_mass_percent": snapshot.disclosed_mass_percent,
        "identified_mass_percent": snapshot.identified_mass_percent,
        "identity_coverage_ratio": snapshot.identity_coverage_ratio,
        "unknown_mass_percent": snapshot.unknown_mass_percent,
        "minimum_risk_guard_disclosed_mass_percent": (
            _MIN_RISK_GUARD_DISCLOSED_MASS_PERCENT
        ),
        "minimum_risk_guard_identity_ratio": _MIN_RISK_GUARD_IDENTITY_RATIO,
    }


def _snapshot_risk_coverage_qualified(snapshot: _PreparedSnapshot) -> bool:
    return bool(
        snapshot.disclosed_mass_percent >= _MIN_RISK_GUARD_DISCLOSED_MASS_PERCENT
        and snapshot.identity_coverage_ratio is not None
        and snapshot.identity_coverage_ratio >= _MIN_RISK_GUARD_IDENTITY_RATIO
    )


def _vintage_alignment(
    left_dates: Sequence[str],
    right_dates: Sequence[str],
) -> dict[str, Any]:
    left = sorted({value for value in left_dates if _iso_date(value) is not None})
    right = sorted({value for value in right_dates if _iso_date(value) is not None})
    all_dates = sorted(set(left) | set(right))
    if len(left) == 1 and len(right) == 1:
        status = "same_as_of_date" if left[0] == right[0] else "cross_vintage"
    else:
        status = "mixed"
    parsed = [_iso_date(value) for value in all_dates]
    valid_dates = [value for value in parsed if value is not None]
    gap_days = (
        (max(valid_dates) - min(valid_dates)).days if valid_dates else None
    )
    return {
        "status": status,
        "gap_days": gap_days,
        "as_of_dates": all_dates,
    }


def _decision_capabilities(
    *,
    research_eligible: bool,
    risk_guard_eligible: bool,
    reasons: Sequence[str],
) -> dict[str, Any]:
    return {
        "research_eligible": bool(research_eligible),
        "concentration_risk_guard_eligible": bool(risk_guard_eligible),
        "allocation_authorization_eligible": False,
        "reason_codes": _unique(reasons),
    }


def _risk_guard_reasons(
    *,
    alignment: Mapping[str, Any],
    positive_overlap: bool,
    candidate_coverage_qualified: bool,
    comparison_coverage_qualified: bool,
    replay_qualified: bool,
) -> list[str]:
    reasons: list[str] = []
    if alignment.get("status") != "same_as_of_date":
        reasons.append("holdings_vintage_not_aligned")
    if not positive_overlap:
        reasons.append("positive_reported_overlap_missing")
    if not candidate_coverage_qualified:
        reasons.append("candidate_holdings_coverage_insufficient")
    if not comparison_coverage_qualified:
        reasons.append("comparison_holdings_coverage_insufficient")
    if not replay_qualified:
        reasons.append("snapshot_observation_not_replay_eligible")
    return reasons


def _candidate_capability_reasons(
    *,
    candidate: _PreparedSnapshot,
    position_truth_pit_qualified: bool,
    pair_guard_eligible: bool,
    portfolio_guard_eligible: bool,
    portfolio_alignment: Mapping[str, Any],
    has_reported_overlap: bool,
) -> list[str]:
    reasons: list[str] = []
    if not position_truth_pit_qualified:
        reasons.append("portfolio_position_truth_not_pit_qualified")
    if not candidate.replay_eligible:
        reasons.append("candidate_snapshot_not_replay_eligible")
    if portfolio_alignment.get("status") != "same_as_of_date":
        reasons.append("holdings_vintage_not_aligned")
    if not has_reported_overlap:
        reasons.append("positive_reported_overlap_missing")
    if not pair_guard_eligible and not portfolio_guard_eligible:
        reasons.append("concentration_risk_guard_evidence_insufficient")
    return _unique(reasons)


def _overlap_evidence_state(value: Mapping[str, Any]) -> str:
    interpretation = str(value.get("interpretation") or "")
    alignment = value.get("vintage_alignment")
    status = (
        str(alignment.get("status") or "mixed")
        if isinstance(alignment, Mapping)
        else "mixed"
    )
    overlap = value.get("overlap_lower_bound_percent")
    if "identity_evidence_insufficient" in interpretation:
        return "identity_evidence_insufficient"
    if status == "same_as_of_date":
        if isinstance(overlap, (int, float)) and overlap > 0:
            return "positive_same_vintage_reported_overlap"
        return "no_common_same_vintage_disclosed_scope"
    if status == "cross_vintage":
        return "cross_vintage_descriptive_only"
    return "mixed_vintage_descriptive_only"


def _pair_overlap(
    left: _PreparedSnapshot,
    right: _PreparedSnapshot,
    limit: int,
) -> dict[str, Any]:
    alignment = _vintage_alignment([left.as_of_date], [right.as_of_date])
    common: list[dict[str, Any]] = []
    for key in sorted(set(left.securities) & set(right.securities)):
        left_security = left.securities[key]
        right_security = right.securities[key]
        contribution = min(left_security.weight_percent, right_security.weight_percent)
        common.append(
            {
                "security_key": key,
                "security_name": right_security.label or left_security.label,
                "existing_weight_percent": left_security.weight_percent,
                "candidate_weight_percent": right_security.weight_percent,
                "overlap_contribution_lower_bound_percent": _round(contribution),
            }
        )
    common.sort(
        key=lambda item: (
            -item["overlap_contribution_lower_bound_percent"],
            item["security_key"],
        )
    )
    common_weight = _round(
        sum(item["overlap_contribution_lower_bound_percent"] for item in common)
    )
    same_vintage = alignment["status"] == "same_as_of_date"
    identity_sufficient = bool(left.securities and right.securities)
    if same_vintage and common:
        interpretation = "positive_disclosed_overlap_lower_bound"
        reported_overlap: float | None = common_weight
        cross_vintage_similarity: float | None = None
    elif same_vintage and identity_sufficient:
        interpretation = "no_common_in_disclosed_scope"
        reported_overlap = None
        cross_vintage_similarity = None
    elif same_vintage:
        interpretation = "identity_evidence_insufficient"
        reported_overlap = None
        cross_vintage_similarity = None
    elif identity_sufficient:
        interpretation = (
            "cross_vintage_descriptive_similarity"
            if common
            else "cross_vintage_no_common_in_disclosed_scope"
        )
        reported_overlap = None
        cross_vintage_similarity = common_weight
    else:
        interpretation = "identity_evidence_insufficient"
        reported_overlap = None
        cross_vintage_similarity = None
    risk_reasons = _risk_guard_reasons(
        alignment=alignment,
        positive_overlap=reported_overlap is not None and reported_overlap > 0,
        candidate_coverage_qualified=_snapshot_risk_coverage_qualified(right),
        comparison_coverage_qualified=_snapshot_risk_coverage_qualified(left),
        replay_qualified=left.replay_eligible and right.replay_eligible,
    )
    capabilities = _decision_capabilities(
        research_eligible=True,
        risk_guard_eligible=not risk_reasons,
        reasons=risk_reasons,
    )
    return {
        "overlap_lower_bound_percent": reported_overlap,
        "reported_overlap_lower_bound_percent": reported_overlap,
        "common_disclosed_weight_percent": common_weight,
        "cross_vintage_disclosed_similarity_percent": cross_vintage_similarity,
        "interpretation": interpretation,
        "vintage_alignment": alignment,
        "capabilities": capabilities,
        "decision_use": capabilities,
        "reason_codes": risk_reasons,
        "left_disclosed_mass_percent": left.disclosed_mass_percent,
        "left_identity_coverage_ratio": left.identity_coverage_ratio,
        "left_unknown_mass_percent": left.unknown_mass_percent,
        "right_disclosed_mass_percent": right.disclosed_mass_percent,
        "right_identity_coverage_ratio": right.identity_coverage_ratio,
        "right_unknown_mass_percent": right.unknown_mass_percent,
        "top_common_securities": common[:limit],
        "exact_full_portfolio_overlap_percent": None,
        "exact_full_portfolio_overlap_eligible": False,
    }


def _portfolio_overlap(
    candidate: _PreparedSnapshot,
    portfolio: Mapping[str, Mapping[str, Any]],
    portfolio_by_vintage: Mapping[str, Mapping[str, Mapping[str, Any]]],
    limit: int,
    *,
    portfolio_disclosed_mass: float,
    portfolio_identified_mass: float,
    portfolio_replay_eligible: bool,
) -> dict[str, Any]:
    alignment = _vintage_alignment(
        [candidate.as_of_date],
        list(portfolio_by_vintage),
    )
    comparison_portfolio: Mapping[str, Mapping[str, Any]] = portfolio
    if alignment["status"] == "same_as_of_date":
        comparison_portfolio = portfolio_by_vintage.get(candidate.as_of_date, {})
    common: list[dict[str, Any]] = []
    for key in sorted(set(candidate.securities) & set(comparison_portfolio)):
        candidate_security = candidate.securities[key]
        portfolio_weight = float(
            comparison_portfolio[key]["exposure_lower_bound_percent"]
        )
        contribution = min(candidate_security.weight_percent, portfolio_weight)
        common.append(
            {
                "security_key": key,
                "security_name": candidate_security.label,
                "portfolio_exposure_lower_bound_percent": _round(portfolio_weight),
                "candidate_weight_percent": candidate_security.weight_percent,
                "overlap_contribution_lower_bound_percent": _round(contribution),
            }
        )
    common.sort(
        key=lambda item: (
            -item["overlap_contribution_lower_bound_percent"],
            item["security_key"],
        )
    )
    common_weight = _round(
        sum(item["overlap_contribution_lower_bound_percent"] for item in common)
    )
    same_vintage = alignment["status"] == "same_as_of_date"
    identity_sufficient = bool(candidate.securities and comparison_portfolio)
    if same_vintage and common:
        interpretation = "positive_disclosed_overlap_lower_bound"
        reported_overlap: float | None = common_weight
        cross_vintage_similarity: float | None = None
    elif same_vintage and identity_sufficient:
        interpretation = "no_common_in_disclosed_scope"
        reported_overlap = None
        cross_vintage_similarity = None
    elif same_vintage:
        interpretation = "identity_evidence_insufficient"
        reported_overlap = None
        cross_vintage_similarity = None
    elif identity_sufficient:
        interpretation = (
            "cross_vintage_descriptive_similarity"
            if common
            else "cross_vintage_no_common_in_disclosed_scope"
        )
        reported_overlap = None
        cross_vintage_similarity = common_weight
    else:
        interpretation = "identity_evidence_insufficient"
        reported_overlap = None
        cross_vintage_similarity = None
    portfolio_identity_ratio = (
        _round(portfolio_identified_mass / portfolio_disclosed_mass)
        if portfolio_disclosed_mass > 0
        else None
    )
    portfolio_coverage_qualified = bool(
        portfolio_disclosed_mass >= _MIN_RISK_GUARD_DISCLOSED_MASS_PERCENT
        and portfolio_identity_ratio is not None
        and portfolio_identity_ratio >= _MIN_RISK_GUARD_IDENTITY_RATIO
    )
    risk_reasons = _risk_guard_reasons(
        alignment=alignment,
        positive_overlap=reported_overlap is not None and reported_overlap > 0,
        candidate_coverage_qualified=_snapshot_risk_coverage_qualified(candidate),
        comparison_coverage_qualified=portfolio_coverage_qualified,
        replay_qualified=candidate.replay_eligible and portfolio_replay_eligible,
    )
    capabilities = _decision_capabilities(
        research_eligible=True,
        risk_guard_eligible=not risk_reasons,
        reasons=risk_reasons,
    )
    return {
        "overlap_lower_bound_percent": reported_overlap,
        "reported_overlap_lower_bound_percent": reported_overlap,
        "common_disclosed_weight_percent": common_weight,
        "cross_vintage_disclosed_similarity_percent": cross_vintage_similarity,
        "interpretation": interpretation,
        "vintage_alignment": alignment,
        "portfolio_disclosed_mass_percent": _round(portfolio_disclosed_mass),
        "portfolio_identity_coverage_ratio": portfolio_identity_ratio,
        "capabilities": capabilities,
        "decision_use": capabilities,
        "reason_codes": risk_reasons,
        "top_common_securities": common[:limit],
    }


def _single_fund_summary(snapshot: _PreparedSnapshot, limit: int) -> dict[str, Any]:
    securities = sorted(
        (
            {
                "security_key": item.key,
                "security_name": item.label,
                "weight_lower_bound_percent": item.weight_percent,
            }
            for item in snapshot.securities.values()
        ),
        key=lambda item: (-item["weight_lower_bound_percent"], item["security_key"]),
    )
    industries = _exposure_rows(snapshot.industry_weights, "industry")
    markets = _exposure_rows(snapshot.market_weights, "listing_market")
    return {
        "disclosed_mass_percent": snapshot.disclosed_mass_percent,
        "identity_known_disclosed_mass_percent": snapshot.identified_mass_percent,
        "identity_unknown_disclosed_mass_percent": _round(
            max(snapshot.disclosed_mass_percent - snapshot.identified_mass_percent, 0.0)
        ),
        "undisclosed_mass_percent": _round(
            max(100.0 - snapshot.disclosed_mass_percent, 0.0)
        ),
        "unknown_mass_percent": snapshot.unknown_mass_percent,
        "top_disclosed_security_weights": securities[:limit],
        "industry_exposure_lower_bounds": industries[:limit],
        "industry_unknown_mass_percent": _round(
            max(100.0 - sum(item["exposure_lower_bound_percent"] for item in industries), 0.0)
        ),
        "listing_market_exposure_lower_bounds": markets[:limit],
        "listing_market_unknown_mass_percent": _round(
            max(100.0 - sum(item["exposure_lower_bound_percent"] for item in markets), 0.0)
        ),
        "exact_full_portfolio_exposure_eligible": False,
    }


def _snapshot_summary(snapshot: _PreparedSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "schema_version": HOLDINGS_SNAPSHOT_SCHEMA_VERSION,
        "fund_code": snapshot.fund_code,
        "aggregation_key": snapshot.aggregation_key,
        "master_key_verified": snapshot.master_key_verified,
        "snapshot_hash": snapshot.snapshot_hash,
        "report_period": snapshot.report_period,
        "as_of_date": snapshot.as_of_date,
        "available_at": snapshot.available_at,
        "first_observed_at": snapshot.first_observed_at,
        "observation_status": snapshot.observation_status,
        "replay_eligible": snapshot.replay_eligible,
        "scope_kind": snapshot.scope_kind,
        "current_freshness_label": snapshot.current_freshness_label,
        "current_report_age_days": snapshot.current_report_age_days,
        "disclosed_overlap_lower_bound_eligible": snapshot.eligible,
        "reason_codes": list(snapshot.reason_codes),
    }


def _normalize_positions(
    value: object,
    *,
    explicit_complete: bool | None,
    decision: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    raw = value
    embedded: Mapping[str, Any] = {}
    if isinstance(value, Mapping):
        embedded = value
        for key in ("positions", "holdings", "items"):
            if key in value:
                raw = value.get(key)
                break
        else:
            raw = [value]
    if raw is None:
        raw = []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return [], {}, ["user_holdings_invalid"]
    complete_raw = (
        explicit_complete
        if explicit_complete is not None
        else embedded.get("positions_complete")
    )
    if complete_raw is not True and complete_raw is not False and complete_raw is not None:
        return [], {}, ["portfolio_positions_complete_invalid"]
    positions: list[dict[str, Any]] = []
    seen: set[str] = set()
    reasons: list[str] = []
    for row in raw:
        if not isinstance(row, Mapping):
            reasons.append("user_holding_not_mapping")
            continue
        code = _fund_code(row.get("fund_code"))
        amount = _finite_number(row.get("holding_amount"))
        if code is None:
            reasons.append("user_holding_fund_code_invalid")
            continue
        if amount is None:
            reasons.append("user_holding_amount_invalid")
            continue
        if amount < 0:
            reasons.append("user_holding_amount_negative")
            continue
        if code in seen:
            reasons.append("user_holding_duplicate_fund_conflict")
            continue
        seen.add(code)
        positions.append({"fund_code": code, "holding_amount": _round(amount)})
    positions.sort(key=lambda item: item["fund_code"])
    available_raw = embedded.get("available_at")
    if available_raw is not None:
        available = _aware_datetime(available_raw)
        if available is None:
            reasons.append("user_holdings_available_at_timezone_required")
        elif available > decision:
            reasons.append("user_holdings_available_after_decision")
    as_of_raw = embedded.get("as_of_date")
    if as_of_raw is not None:
        as_of = _iso_date(as_of_raw)
        if as_of is None:
            reasons.append("user_holdings_as_of_date_invalid")
        elif as_of > decision.astimezone(CN_TZ).date():
            reasons.append("user_holdings_as_of_after_decision")
    first_observed_raw = embedded.get("first_observed_at")
    if first_observed_raw is not None:
        first_observed = _aware_datetime(first_observed_raw)
        if first_observed is None:
            reasons.append("user_holdings_first_observed_at_timezone_required")
        elif first_observed > decision:
            reasons.append("user_holdings_first_observed_after_decision")
    position_truth_pit_qualified = _pit_evidence_usable(
        embedded,
        decision=decision,
    )
    source_audit = {
        "status": "qualified" if position_truth_pit_qualified else "unavailable",
        "source": _text(embedded.get("source")),
        "ref_id": _text(embedded.get("ref_id") or embedded.get("snapshot_id")),
        "available_at": (
            _aware_datetime(embedded.get("available_at")).isoformat()
            if _aware_datetime(embedded.get("available_at")) is not None
            else None
        ),
        "first_observed_at": (
            _aware_datetime(embedded.get("first_observed_at")).isoformat()
            if _aware_datetime(embedded.get("first_observed_at")) is not None
            else None
        ),
    }
    return (
        positions,
        {
            "positions_complete": complete_raw is True,
            "position_truth_pit_qualified": position_truth_pit_qualified,
            "source_audit": source_audit,
            "portfolio_denominator_yuan": embedded.get("portfolio_denominator_yuan"),
            "portfolio_denominator_source": embedded.get("portfolio_denominator_source"),
        },
        _unique(reasons),
    )


def _resolve_denominator(
    *,
    positions: Sequence[Mapping[str, Any]],
    embedded_meta: Mapping[str, Any],
    explicit_amount: object,
    explicit_source: Mapping[str, Any] | None,
    decision: datetime,
) -> dict[str, Any]:
    fund_sum = sum(float(item["holding_amount"]) for item in positions)
    amount_raw = (
        explicit_amount
        if explicit_amount is not None
        else embedded_meta.get("portfolio_denominator_yuan")
    )
    source = (
        explicit_source
        if explicit_source is not None
        else embedded_meta.get("portfolio_denominator_source")
    )
    if amount_raw is None:
        return {
            "scope": "fund_holdings_only",
            "analysis_denominator_yuan": fund_sum,
            "whole_account_denominator_yuan": None,
            "whole_account_denominator_qualified": False,
            "source_audit": None,
            "invalid_reason": None,
        }
    amount = _finite_number(amount_raw)
    if amount is None or amount <= 0:
        return {"invalid_reason": "portfolio_denominator_invalid"}
    if amount + 1e-8 < fund_sum:
        return {"invalid_reason": "portfolio_denominator_below_fund_holdings"}
    if source is not None and not isinstance(source, Mapping):
        return {"invalid_reason": "portfolio_denominator_source_invalid"}
    if not _pit_evidence_usable(source, decision=decision):
        # The unverified amount is not used; preserve a safe fund-only view.
        return {
            "scope": "fund_holdings_only",
            "analysis_denominator_yuan": fund_sum,
            "whole_account_denominator_yuan": None,
            "whole_account_denominator_qualified": False,
            "source_audit": {
                "status": "unavailable",
                "reason": "portfolio_denominator_source_not_pit_qualified",
            },
            "invalid_reason": None,
        }
    assert isinstance(source, Mapping)
    return {
        "scope": "whole_account",
        "analysis_denominator_yuan": amount,
        "whole_account_denominator_yuan": _round(amount),
        "whole_account_denominator_qualified": True,
        "source_audit": {
            "status": "qualified",
            "source": _text(source.get("source")),
            "ref_id": _text(source.get("ref_id")),
            "available_at": _aware_datetime(source.get("available_at")).isoformat(),
            "first_observed_at": _aware_datetime(
                source.get("first_observed_at")
            ).isoformat(),
        },
        "invalid_reason": None,
    }


def _snapshot_records(value: object) -> tuple[list[dict[str, Any]], list[str]]:
    if value is None:
        return [], []
    raw: object = value
    if isinstance(value, Mapping):
        if value.get("schema_version") == HOLDINGS_SNAPSHOT_SCHEMA_VERSION:
            raw = [value]
        else:
            for key in ("snapshots", "items", "candidates"):
                if key in value:
                    raw = value.get(key)
                    break
            else:
                rows: list[dict[str, Any]] = []
                for key, item in value.items():
                    if not isinstance(item, Mapping):
                        return [], ["snapshot_collection_invalid"]
                    copy = dict(item)
                    copy.setdefault("fund_code", key)
                    rows.append(copy)
                raw = rows
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return [], ["snapshot_collection_invalid"]
    rows = []
    for item in raw:
        if not isinstance(item, Mapping):
            return [], ["snapshot_record_not_mapping"]
        rows.append(dict(item))
    return rows, []


def _snapshot_observation(
    snapshot: Mapping[str, Any],
    *,
    decision: datetime,
    current_run_observation: Mapping[str, Any] | None,
) -> tuple[datetime | None, str, bool, bool, str | None]:
    raw_first_observed: object = snapshot.get("first_observed_at")
    audit = snapshot.get("audit")
    repository = (
        audit.get("snapshot_repository") if isinstance(audit, Mapping) else None
    )
    if (raw_first_observed is None or raw_first_observed == "") and isinstance(
        repository, Mapping
    ):
        raw_first_observed = repository.get("first_observed_at")
    if raw_first_observed is None or raw_first_observed == "":
        return (
            None,
            "observation_missing",
            False,
            False,
            "snapshot_first_observed_at_missing",
        )
    first_observed = _aware_datetime(raw_first_observed)
    if first_observed is None:
        return (
            None,
            "observation_invalid",
            False,
            False,
            "snapshot_first_observed_at_timezone_required",
        )
    if first_observed <= decision:
        return first_observed, "historical_replay", True, True, None

    observation_gap = (first_observed - decision).total_seconds()
    proof = (
        current_run_observation
        if isinstance(current_run_observation, Mapping)
        else {}
    )
    current_same_run = bool(
        0 < observation_gap <= _SAME_RUN_OBSERVATION_WINDOW_SECONDS
        and _current_run_proof_valid(
            proof,
            snapshot=snapshot,
            first_observed=first_observed,
            decision=decision,
        )
    )
    if current_same_run:
        return first_observed, "current_live_same_run", True, False, None
    return (
        first_observed,
        "observed_after_decision",
        False,
        False,
        "snapshot_first_observed_after_decision",
    )


def _current_run_proof_valid(
    proof: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
    first_observed: datetime,
    decision: datetime,
) -> bool:
    if (
        proof.get("mode") != "current_live_same_run"
        or proof.get("source") != "fund_lookthrough_context.live_resolution"
        or _aware_datetime(proof.get("decision_at")) != decision
    ):
        return False
    ref_id = _text(proof.get("ref_id"))
    if ref_id is None or not _HASH_RE.fullmatch(ref_id.lower()):
        return False
    material = {key: value for key, value in proof.items() if key != "ref_id"}
    try:
        if _hash_material(material) != ref_id.lower():
            return False
    except (TypeError, ValueError, OverflowError):
        return False

    observations = proof.get("observations")
    proof_hashes = proof.get("snapshot_hashes")
    if (
        not isinstance(observations, Sequence)
        or isinstance(observations, (str, bytes))
        or not isinstance(proof_hashes, Sequence)
        or isinstance(proof_hashes, (str, bytes))
    ):
        return False
    normalized_rows: list[tuple[str, datetime]] = []
    for raw in observations:
        if not isinstance(raw, Mapping):
            return False
        row_hash = _text(raw.get("snapshot_hash"))
        row_observed = _aware_datetime(raw.get("first_observed_at"))
        if (
            row_hash is None
            or not _HASH_RE.fullmatch(row_hash.lower())
            or row_observed is None
        ):
            return False
        normalized_rows.append((row_hash.lower(), row_observed))
    if not normalized_rows or len({row[0] for row in normalized_rows}) != len(
        normalized_rows
    ):
        return False
    normalized_rows.sort(key=lambda row: (row[0], row[1].isoformat()))
    normalized_hashes = [row[0] for row in normalized_rows]
    supplied_hashes = [
        str(value).strip().lower()
        for value in proof_hashes
        if isinstance(value, str)
    ]
    if supplied_hashes != normalized_hashes:
        return False
    proof_observed = _aware_datetime(proof.get("observed_at"))
    if (
        proof_observed is None
        or proof_observed != max(row[1] for row in normalized_rows)
        or not 0
        <= (proof_observed - decision).total_seconds()
        <= _SAME_RUN_OBSERVATION_WINDOW_SECONDS
    ):
        return False
    snapshot_hash = _text(snapshot.get("snapshot_hash"))
    matches = [
        observed
        for row_hash, observed in normalized_rows
        if snapshot_hash is not None and row_hash == snapshot_hash.lower()
    ]
    return len(matches) == 1 and matches[0] == first_observed


def _pit_evidence_usable(value: object, *, decision: datetime) -> bool:
    if not isinstance(value, Mapping):
        return False
    available = _aware_datetime(value.get("available_at"))
    if available is None or available > decision:
        return False
    first_observed = _aware_datetime(value.get("first_observed_at"))
    if first_observed is None or first_observed > decision:
        return False
    if value.get("as_of_date") is not None:
        as_of = _iso_date(value.get("as_of_date"))
        if as_of is None or as_of > decision.astimezone(CN_TZ).date():
            return False
    source = _text(value.get("source"))
    ref_id = _text(value.get("ref_id") or value.get("snapshot_hash"))
    nested = value.get("source_ref")
    if isinstance(nested, Mapping):
        source = source or _text(nested.get("source"))
        ref_id = ref_id or _text(nested.get("ref_id") or nested.get("snapshot_hash"))
    return source is not None and ref_id is not None


def _prepared_snapshot_fingerprint(value: _PreparedSnapshot) -> str:
    return _hash_material(
        {
            "fund_code": value.fund_code,
            "aggregation_key": value.aggregation_key,
            "master_key_verified": value.master_key_verified,
            "report_period": value.report_period,
            "as_of_date": value.as_of_date,
            "available_at": value.available_at,
            "first_observed_at": value.first_observed_at,
            "observation_status": value.observation_status,
            "replay_eligible": value.replay_eligible,
            "scope_kind": value.scope_kind,
            "eligible": value.eligible,
            "reason_codes": value.reason_codes,
            "disclosed_mass_percent": value.disclosed_mass_percent,
            "securities": {
                key: {
                    "label": item.label,
                    "weight_percent": item.weight_percent,
                    "industry": item.industry,
                    "listing_market": item.listing_market,
                }
                for key, item in value.securities.items()
            },
            "industry_weights": value.industry_weights,
            "market_weights": value.market_weights,
        }
    )


def _execution_reasons(
    *,
    truth_complete: bool,
    position_truth_pit_qualified: bool,
    denominator_qualified: bool,
    missing_snapshots: bool,
    snapshots_replay_qualified: bool,
) -> list[str]:
    reasons: list[str] = []
    if not truth_complete:
        reasons.append("portfolio_position_truth_incomplete")
    if not position_truth_pit_qualified:
        reasons.append("portfolio_position_truth_not_pit_qualified")
    if not denominator_qualified:
        reasons.append("whole_account_denominator_unqualified")
    if missing_snapshots:
        reasons.append("existing_fund_snapshot_coverage_incomplete")
    if not snapshots_replay_qualified:
        reasons.append("existing_fund_snapshot_not_replay_qualified")
    return reasons


def _fund_position_groups(
    positions: Sequence[Mapping[str, Any]],
    *,
    by_code: Mapping[str, _PreparedSnapshot],
    denominator: float,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for position in positions:
        code = str(position["fund_code"])
        snapshot = by_code.get(code)
        verified_master = bool(
            snapshot is not None
            and snapshot.master_key_verified
            and snapshot.aggregation_key != code
        )
        key = snapshot.aggregation_key if verified_master and snapshot is not None else code
        target = groups.setdefault(
            key,
            {
                "aggregation_key": key,
                "fund_codes": [],
                "holding_amount_yuan": 0.0,
                "verified_master_key": verified_master,
                "hard_merge_applied": False,
            },
        )
        target["fund_codes"].append(code)
        target["holding_amount_yuan"] += float(position["holding_amount"])
        target["verified_master_key"] = bool(
            target["verified_master_key"] and verified_master
        )
    output: list[dict[str, Any]] = []
    for target in groups.values():
        codes = sorted(set(target["fund_codes"]))
        amount = _round(target["holding_amount_yuan"])
        verified = target["verified_master_key"] is True
        output.append(
            {
                "aggregation_key": target["aggregation_key"],
                "fund_codes": codes,
                "holding_amount_yuan": amount,
                "portfolio_weight_percent": (
                    _round(amount / denominator * 100.0) if denominator > 0 else None
                ),
                "verified_master_key": verified,
                "hard_merge_applied": bool(verified and len(codes) > 1),
            }
        )
    return sorted(output, key=lambda item: item["aggregation_key"])


def _classification_unknown_mass(
    rows: Sequence[Mapping[str, Any]],
    *,
    known_denominator: bool,
    scope: str,
) -> float | None:
    if not known_denominator and scope != "fund_holdings_only":
        return None
    return _round(
        max(100.0 - sum(float(item["exposure_lower_bound_percent"]) for item in rows), 0.0)
    )


def _exposure_rows(values: Mapping[str, float], label_key: str) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                label_key: key,
                "exposure_lower_bound_percent": _round(value),
            }
            for key, value in values.items()
        ),
        key=lambda item: (-item["exposure_lower_bound_percent"], item[label_key]),
    )


def _base_payload(decision: datetime | None) -> dict[str, Any]:
    return {
        "schema_version": LOOKTHROUGH_RESEARCH_SCHEMA_VERSION,
        "decision_at": decision.isoformat() if decision is not None else None,
        "status": "unavailable",
        "scope": None,
        "research_qualified": False,
        "execution_qualified": False,
        "portfolio_execution_qualified": False,
        "reason_codes": [],
        "qualification": {
            "research_qualified": False,
            "execution_qualified": False,
            "reason_codes": [],
        },
        "decision_use": _decision_capabilities(
            research_eligible=False,
            risk_guard_eligible=False,
            reasons=[],
        ),
        "capabilities": {
            "portfolio_lookthrough": {"status": "not_evaluated"},
            "candidate_overlap": {"status": "not_evaluated"},
        },
        "portfolio": None,
        "existing_funds": [],
        "candidates": [],
        "policies": {
            "overlap_metric": "sum_of_minimum_disclosed_nav_weights",
            "reported_overlap_vintage_policy": "same_as_of_date_only",
            "cross_vintage_policy": "descriptive_similarity_only",
            "no_common_reported_overlap": None,
            "security_identity": "explicit_security_id_or_evidenced_listing_market_plus_code",
            "missing_identity": "retain_as_unknown_mass",
            "missing_classification": "retain_as_unknown_mass",
            "disclosed_weights_rebased": False,
            "exact_full_portfolio_overlap_allowed": False,
            "top10_no_match_wording": "no_common_in_disclosed_scope",
            "family_merge": "verified_master_key_only",
            "freshness_recomputed_at_each_decision": True,
            "historical_observation": "first_observed_at_must_not_exceed_decision_at",
            "current_same_run_observation": (
                "explicit_hash_bound_run_proof_research_only_and_not_replay_eligible"
            ),
            "position_truth_execution": "pit_provenance_required",
            "allocation_authorization": "never_granted_by_lookthrough_research",
            "raw_snapshot_holdings_for_llm": False,
        },
        "input_audit": {
            "user_position_count": 0,
            "position_truth_pit_qualified": False,
            "current_run_observation_ref": None,
            "existing_snapshot_count": 0,
            "candidate_snapshot_count": 0,
        },
        "research_hash": None,
    }


def _finish(
    payload: dict[str, Any],
    *,
    status: str,
    reasons: Sequence[str],
) -> dict[str, Any]:
    payload["status"] = status
    payload["reason_codes"] = _unique(str(item) for item in reasons if item)
    payload["research_qualified"] = status == "qualified"
    payload["execution_qualified"] = False
    prior_decision_use = payload.get("decision_use")
    prior_decision_use = (
        prior_decision_use if isinstance(prior_decision_use, Mapping) else {}
    )
    payload["decision_use"] = _decision_capabilities(
        research_eligible=status == "qualified",
        risk_guard_eligible=bool(
            status == "qualified"
            and prior_decision_use.get("concentration_risk_guard_eligible") is True
        ),
        reasons=[
            *(str(item) for item in prior_decision_use.get("reason_codes") or []),
            *payload["reason_codes"],
        ],
    )
    if status != "qualified":
        # Candidate-level lower bounds can remain descriptive in a partial
        # result, but they are never promoted to an execution authorization.
        for item in payload.get("candidates") or []:
            if isinstance(item, dict):
                item["execution_qualified"] = False
                prior = item.get("capabilities")
                prior = prior if isinstance(prior, Mapping) else {}
                item_capabilities = _decision_capabilities(
                    research_eligible=prior.get("research_eligible") is True,
                    risk_guard_eligible=False,
                    reasons=[
                        *(str(value) for value in prior.get("reason_codes") or []),
                        *payload["reason_codes"],
                    ],
                )
                item["capabilities"] = item_capabilities
                item["decision_use"] = item_capabilities
                item["concentration_risk_guard_eligible"] = False
                item["allocation_authorization_eligible"] = False
    payload["qualification"] = {
        "research_qualified": payload["research_qualified"],
        "execution_qualified": payload.get("execution_qualified") is True,
        "reason_codes": list(payload["reason_codes"]),
    }
    payload["research_hash"] = _hash_material(
        {key: value for key, value in payload.items() if key != "research_hash"}
    )
    return payload


def _validate_limits(top_exposure_limit: object, top_common_limit: object) -> list[str]:
    reasons: list[str] = []
    if (
        isinstance(top_exposure_limit, bool)
        or not isinstance(top_exposure_limit, int)
        or top_exposure_limit <= 0
    ):
        reasons.append("top_exposure_limit_invalid")
    if (
        isinstance(top_common_limit, bool)
        or not isinstance(top_common_limit, int)
        or top_common_limit <= 0
    ):
        reasons.append("top_common_limit_invalid")
    return reasons


def _aware_datetime(value: object) -> datetime | None:
    parsed: datetime | None
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


def _iso_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _fund_code(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    if re.fullmatch(r"[A-Za-z0-9._-]{1,32}", text):
        return text.upper()
    return None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _round(value: float) -> float:
    return round(float(value), 8)


def _unique(values: Sequence[str] | Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _hash_material(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "LOOKTHROUGH_RESEARCH_SCHEMA_VERSION",
    "build_fund_lookthrough_research",
    "compact_fund_lookthrough_for_llm",
    "compact_fund_lookthrough_research_for_llm",
]
