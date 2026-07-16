"""Point-in-time fund peer grouping and descriptive percentile ranks.

The module is deliberately pure: it performs no network, database, cache, or
clock reads.  Callers must freeze the catalogue membership and every metric's
``available_at`` timestamp before invoking it.  Missing evidence stays missing;
there is no median/zero backfill.

Peer percentiles remain descriptive even when their data-quality gate passes;
production execution tilt needs a future, dedicated PIT statistical and
economic validation contract.  A/C share classes are one portfolio family,
under-specified bond, QDII, FOF, mixed, and passive-index groups fail closed,
and only an explicitly verified frozen fund contract can authorize a
formal-excess comparison.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from typing import Any

from app.services.fund_benchmark_sector import parse_benchmark_index
from app.services.fund_universe_sampler import canonical_portfolio_name


PEER_GROUP_SCHEMA_VERSION = "fund_peer_group.v1"
PEER_RANK_SCHEMA_VERSION = "peer_rank.v2"
PEER_METRIC_REGISTRY_VERSION = "peer_metric_registry.v2"
MIN_INDEPENDENT_PEER_FAMILIES = 20
MIN_METRIC_COVERAGE = 0.80

_METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "return_3m_percent": {
        "label": "近3月收益",
        "orientation": "higher_is_better",
        "role": "performance",
    },
    "return_6m_percent": {
        "label": "近6月收益",
        "orientation": "higher_is_better",
        "role": "performance",
    },
    "return_1y_percent": {
        "label": "近1年收益",
        "orientation": "higher_is_better",
        "role": "performance",
    },
    "max_drawdown_1y_percent": {
        "label": "近1年最大回撤",
        "orientation": "higher_is_better_closer_to_zero",
        "role": "risk",
    },
    "fund_scale_yi": {
        "label": "基金规模",
        "orientation": "higher_is_larger_not_expected_return",
        "role": "capacity_context_only",
    },
    "benchmark_excess_return_1y_percent": {
        "label": "近1年正式基准超额",
        "orientation": "higher_is_better",
        "role": "active_management",
    },
    "downside_capture_1y_percent": {
        "label": "近1年下行捕获",
        "orientation": "lower_is_better",
        "role": "downside_risk",
    },
    "style_drift_score": {
        "label": "风格漂移",
        "orientation": "lower_is_better",
        "role": "process_stability",
    },
    "tracking_error_1y_percent": {
        "label": "近1年跟踪误差",
        "orientation": "lower_is_better",
        "role": "index_tracking",
    },
    "tracking_difference_1y_percent": {
        "label": "近1年跟踪差",
        "orientation": "closer_to_zero_is_better",
        "role": "index_tracking",
    },
    "modified_duration_years": {
        "label": "修正久期",
        "orientation": "higher_is_longer_rate_sensitivity_context_only",
        "role": "fixed_income_context_only",
    },
    "investment_grade_exposure_percent": {
        "label": "投资级信用暴露",
        "orientation": "higher_is_better_credit_quality",
        "role": "fixed_income_risk",
    },
    "fx_exposure_percent": {
        "label": "外汇暴露",
        "orientation": "higher_is_more_fx_exposure_context_only",
        "role": "overseas_context_only",
    },
    "region_concentration_percent": {
        "label": "地域集中度",
        "orientation": "lower_is_better",
        "role": "overseas_concentration",
    },
    "underlying_fund_overlap_percent": {
        "label": "底层基金重合度",
        "orientation": "lower_is_better",
        "role": "fof_look_through",
    },
    "look_through_expense_ratio_percent": {
        "label": "穿透综合费率",
        "orientation": "lower_is_better",
        "role": "fof_cost",
    },
    "seven_day_annualized_yield_percent": {
        "label": "七日年化收益率",
        "orientation": "higher_is_better",
        "role": "money_fund_yield",
    },
    "income_per_10k_yuan": {
        "label": "万份收益",
        "orientation": "higher_is_better",
        "role": "money_fund_yield",
    },
}

_COMMON_TOTAL_RETURN_METRICS = (
    "return_3m_percent",
    "return_6m_percent",
    "return_1y_percent",
    "max_drawdown_1y_percent",
    "fund_scale_yi",
)

# Applicability is deliberately explicit.  The registry is a declaration of
# evidence required for an honest within-type comparison, not a promise that
# every current provider already supplies every field.  Missing applicable
# evidence therefore remains visible and fails the data-quality qualification.
_TYPE_METRIC_REGISTRY: dict[str, tuple[str, ...]] = {
    "equity": _COMMON_TOTAL_RETURN_METRICS
    + (
        "benchmark_excess_return_1y_percent",
        "downside_capture_1y_percent",
        "style_drift_score",
    ),
    "mixed": _COMMON_TOTAL_RETURN_METRICS
    + (
        "benchmark_excess_return_1y_percent",
        "downside_capture_1y_percent",
        "style_drift_score",
    ),
    "bond": _COMMON_TOTAL_RETURN_METRICS
    + (
        "modified_duration_years",
        "investment_grade_exposure_percent",
    ),
    "passive_index": _COMMON_TOTAL_RETURN_METRICS
    + (
        "tracking_error_1y_percent",
        "tracking_difference_1y_percent",
    ),
    "enhanced_index": _COMMON_TOTAL_RETURN_METRICS
    + (
        "benchmark_excess_return_1y_percent",
        "tracking_error_1y_percent",
        "tracking_difference_1y_percent",
    ),
    "qdii": _COMMON_TOTAL_RETURN_METRICS
    + (
        "fx_exposure_percent",
        "region_concentration_percent",
    ),
    "fof": _COMMON_TOTAL_RETURN_METRICS
    + (
        "underlying_fund_overlap_percent",
        "look_through_expense_ratio_percent",
    ),
    "money": (
        "return_1y_percent",
        "fund_scale_yi",
        "seven_day_annualized_yield_percent",
        "income_per_10k_yuan",
    ),
    "unknown": (),
}

_TYPE_ALIASES = {
    "gp": "equity",
    "股票": "equity",
    "股票型": "equity",
    "hh": "mixed",
    "混合": "mixed",
    "混合型": "mixed",
    "zq": "bond",
    "债券": "bond",
    "债券型": "bond",
    "zs": "equity",
    "指数": "equity",
    "指数型": "equity",
    "qdii": "equity",
    "fof": "fof",
    "货币": "money",
    "货币型": "money",
}

_ASSET_ALIASES = {
    "equity": "equity",
    "stock": "equity",
    "股票": "equity",
    "bond": "bond",
    "fixed_income": "bond",
    "债券": "bond",
    "mixed": "mixed",
    "hybrid": "mixed",
    "混合": "mixed",
    "money": "money",
    "cash": "money",
    "货币": "money",
    "commodity": "commodity",
    "商品": "commodity",
    "reit": "reit",
    "reits": "reit",
    "fof": "fof",
    "fund_of_funds": "fof",
}

_STRATEGY_ALIASES = {
    "active": "active",
    "主动": "active",
    "passive": "passive_index",
    "index": "passive_index",
    "passive_index": "passive_index",
    "被动": "passive_index",
    "enhanced": "enhanced_index",
    "enhanced_index": "enhanced_index",
    "指数增强": "enhanced_index",
    "fund_of_funds": "fund_of_funds",
    "fof": "fund_of_funds",
}

_SHARE_CLASS_RE = re.compile(
    r"(?:人民币|美元(?:现汇|现钞)?|港币)?\s*[-_/]?\s*([A-Z])(?:类|份额)?$",
    re.IGNORECASE,
)


def build_fund_peer_group(
    fund: Mapping[str, Any],
    *,
    decision_at: str | datetime,
    benchmark_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic type/risk peer group for one fund.

    ``risk_exposure`` may refine the catalogue type, but is used only when its
    own ``available_at`` is timezone-aware and no later than ``decision_at``.
    """

    decision = _decision_instant(decision_at)
    spec = benchmark_spec or _mapping(fund.get("benchmark_spec"))
    benchmark = resolve_benchmark_comparison(spec, decision_at=decision)
    text = _classification_text(fund)
    normalized_type = _normalized_fund_type(fund)
    exposure, exposure_reason = _point_in_time_exposure(fund, decision)
    source_fields: list[str] = []

    asset_class = _explicit_asset_class(exposure)
    if asset_class:
        source_fields.append("risk_exposure.asset_class")
    else:
        asset_class = _asset_class_from_type_and_text(normalized_type, text)
        if asset_class != "unknown":
            source_fields.append("fund_type_or_name")

    is_qdii = _is_qdii(normalized_type, text, exposure)
    region = "overseas" if is_qdii else "domestic"
    strategy = _strategy(exposure, normalized_type, text, asset_class)
    if _explicit_strategy(exposure):
        source_fields.append("risk_exposure.management_style")
    elif strategy != "unknown":
        source_fields.append("fund_type_or_name.strategy")

    bond_subtype = _bond_subtype(text, strategy) if asset_class == "bond" else None
    mixed_subtype = (
        _mixed_subtype(text, exposure) if asset_class == "mixed" else None
    )
    qdii_subtype = (
        _qdii_subtype(asset_class, strategy, text) if is_qdii else None
    )
    qdii_region = _qdii_region(text, exposure) if is_qdii else None
    fof_subtype = _fof_subtype(text, exposure) if asset_class == "fof" else None
    risk_bucket = _risk_bucket(exposure, asset_class, mixed_subtype)
    exposure_bucket = _exposure_bucket(exposure)
    reference_code = _benchmark_reference_code(benchmark)
    if strategy in {"passive_index", "enhanced_index"} and not reference_code:
        inferred_reference = _infer_tracking_reference(fund)
        if inferred_reference is not None:
            reference_code = inferred_reference["benchmark_code"]
            benchmark = inferred_reference
            source_fields.append("fund_tracking_reference_text")

    key_parts = [region, asset_class, strategy]
    label_parts = [_region_label(region), _asset_label(asset_class), _strategy_label(strategy)]
    reasons: list[str] = []
    warnings: list[str] = []

    if asset_class == "bond":
        key_parts.append(bond_subtype or "unspecified")
        label_parts.append(_bond_label(bond_subtype))
        if bond_subtype in (None, "unspecified"):
            reasons.append("bond_subtype_unavailable")
    elif asset_class == "mixed":
        key_parts.append(mixed_subtype or "unspecified")
        label_parts.append(_mixed_label(mixed_subtype))
        if mixed_subtype in (None, "unspecified"):
            reasons.append("mixed_risk_exposure_unavailable")
    elif asset_class == "fof":
        key_parts.append(fof_subtype or "unspecified")
        label_parts.append(_fof_label(fof_subtype))
        if fof_subtype in (None, "unspecified"):
            reasons.append("fof_subtype_unavailable")

    if is_qdii:
        key_parts.extend((qdii_subtype or "unspecified", qdii_region or "unspecified"))
        label_parts.extend((_qdii_subtype_label(qdii_subtype), _qdii_region_label(qdii_region)))
        if qdii_subtype in (None, "unspecified"):
            reasons.append("qdii_underlying_asset_unavailable")
        if qdii_region in (None, "unspecified") and asset_class == "equity":
            reasons.append("qdii_region_exposure_unavailable")

    if strategy in {"passive_index", "enhanced_index"}:
        if reference_code:
            key_parts.append(f"reference-{_key_token(reference_code)}")
            label_parts.append(f"跟踪标的 {reference_code}")
        else:
            key_parts.append("reference-unspecified")
            reasons.append("index_tracking_reference_unavailable")

    if exposure_bucket:
        key_parts.append(f"exposure-{_key_token(exposure_bucket)}")
        label_parts.append(f"暴露 {exposure_bucket}")
    elif risk_bucket and asset_class in {"mixed", "fof"}:
        key_parts.append(risk_bucket)

    if asset_class == "unknown":
        reasons.append("fund_asset_class_unavailable")
    if strategy == "unknown":
        reasons.append("management_style_unavailable")
    if exposure_reason:
        warnings.append(exposure_reason)

    reasons = _unique(reasons)
    warnings = _unique(warnings)
    metric_profile = _metric_profile(
        asset_class=asset_class,
        management_style=strategy,
        region=region,
    )
    applicable_metrics = list(_TYPE_METRIC_REGISTRY[metric_profile])
    qualified = not any(
        reason
        in {
            "fund_asset_class_unavailable",
            "management_style_unavailable",
            "bond_subtype_unavailable",
            "mixed_risk_exposure_unavailable",
            "fof_subtype_unavailable",
            "qdii_underlying_asset_unavailable",
            "qdii_region_exposure_unavailable",
            "index_tracking_reference_unavailable",
        }
        for reason in reasons
    )
    confidence = (
        "high"
        if qualified and exposure and not exposure_reason
        else "medium" if qualified
        else "insufficient"
    )
    return {
        "schema_version": PEER_GROUP_SCHEMA_VERSION,
        "decision_at": decision.isoformat(),
        "fund_code": _fund_code(fund),
        "family_key": fund_family_key(fund),
        "group_key": ".".join(_key_token(part) for part in key_parts),
        "group_label": " / ".join(part for part in label_parts if part),
        "fund_type_key": normalized_type,
        "asset_class": asset_class,
        "management_style": strategy,
        "region": region,
        "bond_subtype": bond_subtype,
        "mixed_subtype": mixed_subtype,
        "qdii_subtype": qdii_subtype,
        "qdii_region": qdii_region,
        "fof_subtype": fof_subtype,
        "risk_bucket": risk_bucket,
        "exposure_bucket": exposure_bucket,
        "reference_code": reference_code,
        "classification_sources": _unique(source_fields),
        "classification_confidence": confidence,
        "metric_registry_version": PEER_METRIC_REGISTRY_VERSION,
        "metric_profile": metric_profile,
        "qualified": qualified,
        "reason": reasons[0] if reasons else None,
        "reasons": reasons,
        "warnings": warnings,
        "applicable_metrics": applicable_metrics,
        "benchmark": benchmark,
    }


def resolve_benchmark_comparison(
    benchmark_spec: Mapping[str, Any] | None,
    *,
    decision_at: str | datetime,
) -> dict[str, Any]:
    """Classify a frozen benchmark as formal excess or reference-only.

    A boolean named ``formal_excess_eligible`` is not trusted on its own.  The
    immutable mapping must explicitly record ``verified_fund_contract``.  This
    makes legacy cached mappings without provenance fail closed.
    """

    decision = _decision_instant(decision_at)
    spec = dict(benchmark_spec or {})
    available_at, availability_reason = _available_instant(
        spec.get("available_at"), decision
    )
    identity_available = bool(
        spec.get("benchmark_code")
        or spec.get("benchmark_name")
        or spec.get("components")
    )
    base = {
        "schema_version": str(spec.get("schema_version") or "fund_benchmark_mapping.v1"),
        "mapping_id": _text(spec.get("mapping_id")),
        "benchmark_code": _text(spec.get("benchmark_code")),
        "benchmark_name": _text(spec.get("benchmark_name")),
        "available_at": available_at.isoformat() if available_at else None,
        "contract_verification_kind": _text(
            spec.get("contract_verification_kind")
            or spec.get("benchmark_text_source_kind")
        ),
    }
    if availability_reason:
        return {
            **base,
            "comparison_role": "unavailable",
            "formal_excess_eligible": False,
            "qualified": False,
            "reason": f"benchmark_{availability_reason}",
        }

    verification_kind = base["contract_verification_kind"]
    formal = bool(
        spec.get("schema_version") == "fund_benchmark_mapping.v1"
        and spec.get("tier") == "fund_contract_exact"
        and spec.get("benchmark_kind") == "official_contract"
        and spec.get("completeness") == "complete"
        and spec.get("formal_excess_eligible") is True
        and base["mapping_id"]
        and verification_kind == "verified_fund_contract"
    )
    if formal:
        return {
            **base,
            "comparison_role": "formal_excess",
            "formal_excess_eligible": True,
            "qualified": True,
            "reason": None,
        }
    if identity_available:
        reason = (
            "contract_source_not_verified"
            if verification_kind != "verified_fund_contract"
            else "benchmark_is_reference_not_complete_formal_contract"
        )
        return {
            **base,
            "comparison_role": "tracking_reference",
            "formal_excess_eligible": False,
            "qualified": False,
            "reason": reason,
        }
    return {
        **base,
        "comparison_role": "unavailable",
        "formal_excess_eligible": False,
        "qualified": False,
        "reason": "benchmark_identity_unavailable",
    }


def build_peer_rank(
    target: Mapping[str, Any],
    universe: Iterable[Mapping[str, Any]],
    *,
    decision_at: str | datetime,
    benchmark_spec: Mapping[str, Any] | None = None,
    minimum_peer_count: int = MIN_INDEPENDENT_PEER_FAMILIES,
    minimum_metric_coverage: float = MIN_METRIC_COVERAGE,
) -> dict[str, Any]:
    """Rank one target against independent, point-in-time peer families.

    Percentiles may be present when a group is too small, but ``qualified`` and
    ``execution_tilt_eligible`` remain false.  Callers must never treat a
    descriptive percentile as an execution whitelist.
    """

    if minimum_peer_count < 1:
        raise ValueError("minimum_peer_count must be positive")
    if not 0 < minimum_metric_coverage <= 1:
        raise ValueError("minimum_metric_coverage must be in (0, 1]")
    decision = _decision_instant(decision_at)
    target_group = build_fund_peer_group(
        target,
        decision_at=decision,
        benchmark_spec=benchmark_spec,
    )
    target_family = target_group["family_key"]
    target_membership, target_membership_reason = _membership_instant(target, decision)
    rows = [dict(row) for row in universe]
    point_in_time_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    future_or_unknown_membership = 0
    for row in rows:
        _, membership_reason = _membership_instant(row, decision)
        if membership_reason:
            future_or_unknown_membership += 1
            continue
        point_in_time_rows.append(row)
        group = build_fund_peer_group(row, decision_at=decision)
        if group["group_key"] == target_group["group_key"]:
            group_rows.append(row)

    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in group_rows:
        by_family[fund_family_key(row)].append(row)
    target_family_member_count = len(by_family.pop(target_family, []))
    peer_family_count = len(by_family)
    target_share_class = _share_class(target)
    metric_profile = str(target_group.get("metric_profile") or "unknown")
    applicable_fields = set(target_group.get("applicable_metrics") or [])
    metrics: dict[str, dict[str, Any]] = {}
    all_metric_qualified = bool(applicable_fields)

    for field, definition in _METRIC_DEFINITIONS.items():
        applicable = field in applicable_fields
        if not applicable:
            reason = f"metric_not_applicable_to_{metric_profile}"
            metrics[field] = {
                **definition,
                "applicable": False,
                "applicability": "not_applicable",
                "available": False,
                "availability": "not_applicable",
                "value": None,
                "value_available_at": None,
                "value_as_of": None,
                "value_source": None,
                "independent_peer_family_count": peer_family_count,
                "sample_count": 0,
                "coverage_rate": None,
                "percentile": None,
                "qualified": False,
                "qualification_required": False,
                "reason": reason,
                "reasons": [reason],
                "peer_sample_hash": _canonical_hash(
                    {
                        "peer_group_key": target_group["group_key"],
                        "metric_profile": metric_profile,
                        "field": field,
                        "samples": [],
                    }
                ),
            }
            continue

        target_evidence = _metric_evidence(target, field, decision)
        peer_values: list[tuple[str, str | None, dict[str, Any]]] = []
        for family_key, members in sorted(by_family.items()):
            selected = _select_family_metric_evidence(
                members,
                field=field,
                decision=decision,
                preferred_share_class=target_share_class,
            )
            if selected is not None:
                peer_values.append(
                    (family_key, _fund_code(selected[0]), selected[1])
                )
        sample_count = len(peer_values)
        coverage = sample_count / peer_family_count if peer_family_count else 0.0
        percentile = None
        if target_evidence.get("status") == "available" and sample_count:
            percentile = _oriented_percentile(
                float(target_evidence["value"]),
                [float(item[2]["value"]) for item in peer_values],
                orientation=str(definition["orientation"]),
            )

        reasons: list[str] = []
        if target_evidence.get("status") != "available":
            reasons.append(f"target_{target_evidence.get('reason') or 'metric_unavailable'}")
        if peer_family_count < minimum_peer_count:
            reasons.append("independent_peer_family_count_below_minimum")
        if sample_count < minimum_peer_count:
            reasons.append("metric_sample_count_below_minimum")
        if coverage < minimum_metric_coverage:
            reasons.append("metric_coverage_below_minimum")
        qualified = not reasons
        all_metric_qualified = all_metric_qualified and qualified
        sample_material = [
            {
                "family_key": family_key,
                "fund_code": code,
                "value": evidence["value"],
                "available_at": evidence["available_at"],
            }
            for family_key, code, evidence in peer_values
        ]
        metrics[field] = {
            **definition,
            "applicable": True,
            "applicability": "applicable",
            "available": target_evidence.get("status") == "available",
            "availability": (
                "available"
                if target_evidence.get("status") == "available"
                else "unavailable"
            ),
            "value": target_evidence.get("value"),
            "value_available_at": target_evidence.get("available_at"),
            "value_as_of": target_evidence.get("as_of"),
            "value_source": target_evidence.get("source"),
            "independent_peer_family_count": peer_family_count,
            "sample_count": sample_count,
            "coverage_rate": round(coverage, 4),
            "percentile": percentile,
            "qualified": qualified,
            "qualification_required": True,
            "reason": reasons[0] if reasons else None,
            "reasons": reasons,
            "peer_sample_hash": _canonical_hash(
                {
                    "peer_group_key": target_group["group_key"],
                    "metric_profile": metric_profile,
                    "field": field,
                    "samples": sample_material,
                }
            ),
        }

    overall_reasons = list(target_group.get("reasons") or [])
    if target_membership_reason:
        overall_reasons.append(f"target_{target_membership_reason}")
    if not all_metric_qualified:
        overall_reasons.append("one_or_more_peer_metrics_not_qualified")
    overall_reasons = _unique(overall_reasons)
    peer_data_qualified = bool(
        target_group.get("qualified")
        and target_membership is not None
        and all_metric_qualified
    )
    descriptive_count = sum(
        item.get("applicable") is True and item.get("percentile") is not None
        for item in metrics.values()
    )
    performance_percentiles = [
        float(item["percentile"])
        for item in metrics.values()
        if item.get("applicable") is True
        and item.get("role") == "performance"
        and item.get("percentile") is not None
    ]
    descriptive_performance_percentile = (
        round(sum(performance_percentiles) / len(performance_percentiles), 2)
        if performance_percentiles
        else None
    )
    status = (
        "qualified"
        if peer_data_qualified
        else "descriptive_only" if descriptive_count else "insufficient"
    )
    execution_tilt_gate = {
        "status": "blocked",
        "eligible": False,
        "required_method": "peer_rank_pit_statistical_and_economic",
        "reason": (
            "peer_rank_data_not_qualified"
            if not peer_data_qualified
            else "peer_rank_predictive_qualification_unavailable"
        ),
    }
    return {
        "schema_version": PEER_RANK_SCHEMA_VERSION,
        "decision_at": decision.isoformat(),
        "target_fund_code": _fund_code(target),
        "target_family_key": target_family,
        "peer_group": target_group,
        "metric_registry_version": PEER_METRIC_REGISTRY_VERSION,
        "metric_profile": metric_profile,
        "status": status,
        # `qualified` is a data-quality/comparability statement only.  It must
        # never be used as an execution whitelist (the same distinction as
        # factor `descriptive_applicable` vs `execution_qualified`).
        "qualified": peer_data_qualified,
        "research_shadow_rerank_eligible": peer_data_qualified,
        "execution_tilt_eligible": False,
        "execution_tilt_gate": execution_tilt_gate,
        "reason": overall_reasons[0] if overall_reasons else None,
        "reasons": overall_reasons,
        "qualification_policy": {
            "minimum_independent_peer_families": minimum_peer_count,
            "minimum_metric_coverage": minimum_metric_coverage,
            "required_metrics": sorted(applicable_fields),
            "registered_metrics": list(_METRIC_DEFINITIONS),
            "applicability_policy": "explicit_type_registry_fail_closed",
            "missing_value_policy": "never_impute",
            "share_family_policy": (
                "one_observation_per_family_matching_target_share_class_then_A"
            ),
            "capacity_metric_role": "context_and_risk_gate_not_expected_return",
            "execution_semantics": (
                "descriptive_only_until_dedicated_pit_statistical_and_"
                "economic_validation"
            ),
        },
        "universe": {
            "raw_member_count": len(rows),
            "point_in_time_member_count": len(point_in_time_rows),
            "membership_unavailable_or_future_count": future_or_unknown_membership,
            "group_share_class_count": len(group_rows),
            "independent_peer_family_count": peer_family_count,
            "target_family_share_class_count_excluded": target_family_member_count,
            "duplicate_share_class_count": max(
                0,
                len(group_rows)
                - peer_family_count
                - (1 if target_family_member_count else 0),
            ),
        },
        "metrics": metrics,
        "applicable_metric_count": len(applicable_fields),
        "available_applicable_metric_count": sum(
            item.get("applicable") is True and item.get("available") is True
            for item in metrics.values()
        ),
        "not_applicable_metric_count": sum(
            item.get("applicable") is False for item in metrics.values()
        ),
        "descriptive_percentile_count": descriptive_count,
        "descriptive_performance_percentile": descriptive_performance_percentile,
        "descriptive_performance_semantics": (
            "equal_weight_available_performance_dimensions_not_execution_signal"
        ),
        "qualified_metric_count": sum(
            item.get("applicable") is True and item.get("qualified") is True
            for item in metrics.values()
        ),
        "target_metric_coverage_rate": round(
            sum(
                item.get("applicable") is True and item.get("available") is True
                for item in metrics.values()
            )
            / len(applicable_fields),
            4,
        )
        if applicable_fields
        else 0.0,
        "benchmark": target_group["benchmark"],
    }


def fund_family_key(fund: Mapping[str, Any]) -> str:
    """Return a deterministic A/C-share family key without external lookups."""

    share_family = _mapping(fund.get("share_family"))
    explicit = _text(
        share_family.get("family_key")
        or fund.get("share_family_id")
        or fund.get("canonical_portfolio_key")
    )
    if explicit:
        return f"explicit:{explicit.casefold()}"
    name = canonical_portfolio_name(str(fund.get("fund_name") or ""))
    name = re.sub(r"[\s·•（）()\-_/]", "", name).casefold()
    company = re.sub(
        r"[\s·•（）()\-_/]",
        "",
        str(fund.get("fund_company") or fund.get("management_company") or ""),
    ).casefold()
    if name:
        return f"name:{company}:{name}"
    code = _fund_code(fund)
    return f"code:{code or 'unknown'}"


def _select_family_metric_evidence(
    members: list[dict[str, Any]],
    *,
    field: str,
    decision: datetime,
    preferred_share_class: str | None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for row in sorted(
        members,
        key=lambda item: _share_class_priority(item, preferred_share_class),
    ):
        evidence = _metric_evidence(row, field, decision)
        if evidence.get("status") == "available":
            return row, evidence
    return None


def _metric_evidence(
    row: Mapping[str, Any], field: str, decision: datetime
) -> dict[str, Any]:
    if field == "benchmark_excess_return_1y_percent":
        comparison = resolve_benchmark_comparison(
            _mapping(row.get("benchmark_spec")),
            decision_at=decision,
        )
        if not (
            comparison.get("comparison_role") == "formal_excess"
            and comparison.get("formal_excess_eligible") is True
            and comparison.get("qualified") is True
        ):
            return {
                "status": "unavailable",
                "reason": "formal_benchmark_required",
            }
    raw_value: object = None
    available_raw: object = None
    as_of: object = None
    source: object = None
    found = False
    for container_name in ("peer_metric_evidence", "metric_evidence", "metrics"):
        container = _mapping(row.get(container_name))
        evidence = container.get(field)
        if isinstance(evidence, Mapping):
            raw_value = evidence.get("value")
            available_raw = evidence.get("available_at")
            as_of = evidence.get("as_of")
            source = evidence.get("source")
            found = True
            break

    metadata = _mapping(row.get("metadata"))
    if not found and field in metadata:
        raw_value = metadata.get(field)
        available_raw = metadata.get(f"{field}_available_at") or metadata.get(
            "snapshot_available_at"
        )
        as_of = metadata.get(f"{field}_as_of")
        if field == "fund_scale_yi":
            as_of = as_of or metadata.get("fund_scale_as_of")
        elif field != "fund_scale_yi":
            as_of = as_of or metadata.get("nav_date")
        source = metadata.get(f"{field}_source")
        if field == "fund_scale_yi":
            source = source or metadata.get("fund_scale_source")
        source = source or row.get("source")
        found = True
    if not found and field in row:
        raw_value = row.get(field)
        available_raw = (
            row.get(f"{field}_available_at")
            or row.get("snapshot_available_at")
            or row.get("candidate_universe_available_at")
            or row.get("available_at")
        )
        as_of = row.get(f"{field}_as_of")
        if field == "fund_scale_yi":
            as_of = as_of or row.get("fund_scale_as_of")
        elif field != "fund_scale_yi":
            as_of = as_of or row.get("nav_date")
        source = row.get(f"{field}_source")
        if field == "fund_scale_yi":
            source = source or row.get("fund_scale_source")
        source = source or row.get("source")
        found = True
    if not found or raw_value in (None, ""):
        return {"status": "unavailable", "reason": "metric_value_missing"}

    available_at, availability_reason = _available_instant(available_raw, decision)
    if availability_reason:
        return {"status": "unavailable", "reason": availability_reason}
    as_of_date, as_of_reason = _as_of_date(as_of, decision)
    if as_of_reason:
        return {"status": "unavailable", "reason": as_of_reason}
    value, value_reason = _metric_value(field, raw_value)
    if value_reason:
        return {"status": "unavailable", "reason": value_reason}
    return {
        "status": "available",
        "reason": None,
        "value": value,
        "available_at": available_at.isoformat() if available_at else None,
        "as_of": as_of_date.isoformat() if as_of_date else None,
        "source": _text(source),
    }


def _metric_value(field: str, value: object) -> tuple[float | None, str | None]:
    if isinstance(value, bool):
        return None, "metric_value_invalid"
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, "metric_value_invalid"
    if not math.isfinite(parsed):
        return None, "metric_value_invalid"
    if field.startswith("return_") and not -100.0 <= parsed <= 10_000.0:
        return None, "return_value_out_of_range"
    if field == "max_drawdown_1y_percent" and not -100.0 <= parsed <= 0.0:
        return None, "drawdown_must_be_signed_non_positive_percent"
    if field == "fund_scale_yi" and parsed <= 0:
        return None, "fund_scale_must_be_positive"
    if field in {
        "style_drift_score",
        "investment_grade_exposure_percent",
        "fx_exposure_percent",
        "region_concentration_percent",
        "underlying_fund_overlap_percent",
        "look_through_expense_ratio_percent",
    } and not 0.0 <= parsed <= 100.0:
        return None, "bounded_percent_value_out_of_range"
    if field in {
        "downside_capture_1y_percent",
        "tracking_error_1y_percent",
    } and not 0.0 <= parsed <= 10_000.0:
        return None, "non_negative_percent_value_out_of_range"
    if field == "modified_duration_years" and not 0.0 <= parsed <= 100.0:
        return None, "duration_value_out_of_range"
    if field == "tracking_difference_1y_percent" and not -100.0 <= parsed <= 100.0:
        return None, "tracking_difference_value_out_of_range"
    if field == "benchmark_excess_return_1y_percent" and not -100.0 <= parsed <= 10_000.0:
        return None, "benchmark_excess_value_out_of_range"
    if field == "seven_day_annualized_yield_percent" and not -100.0 <= parsed <= 10_000.0:
        return None, "money_yield_value_out_of_range"
    if field == "income_per_10k_yuan" and parsed < 0.0:
        return None, "income_per_10k_must_be_non_negative"
    return parsed, None


def _membership_instant(
    row: Mapping[str, Any], decision: datetime
) -> tuple[datetime | None, str | None]:
    metadata = _mapping(row.get("metadata"))
    raw = (
        row.get("membership_available_at")
        or row.get("available_at")
        or row.get("snapshot_available_at")
        or row.get("candidate_universe_available_at")
        or metadata.get("snapshot_available_at")
    )
    instant, reason = _available_instant(raw, decision)
    if reason == "available_at_missing_or_invalid":
        return None, "membership_available_at_missing_or_invalid"
    if reason == "available_after_decision_at":
        return None, "membership_available_after_decision_at"
    return instant, reason


def _available_instant(
    value: object, decision: datetime
) -> tuple[datetime | None, str | None]:
    instant = _parse_instant(value)
    if instant is None:
        return None, "available_at_missing_or_invalid"
    if instant > decision:
        return None, "available_after_decision_at"
    return instant, None


def _as_of_date(
    value: object, decision: datetime
) -> tuple[date | None, str | None]:
    if value in (None, ""):
        return None, None
    text = str(value).strip()
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None, "metric_as_of_invalid"
    if parsed > decision.date():
        return None, "metric_as_of_after_decision_at"
    return parsed, None


def _point_in_time_exposure(
    fund: Mapping[str, Any], decision: datetime
) -> tuple[dict[str, Any], str | None]:
    raw = _mapping(fund.get("risk_exposure"))
    if not raw:
        return {}, None
    _, reason = _available_instant(raw.get("available_at"), decision)
    if reason:
        return {}, f"risk_exposure_{reason}"
    return raw, None


def _classification_text(fund: Mapping[str, Any]) -> str:
    values = (
        fund.get("fund_type"),
        fund.get("fund_category"),
        fund.get("fund_name"),
        fund.get("investment_style"),
    )
    return " ".join(str(value or "").strip() for value in values).casefold()


def _normalized_fund_type(fund: Mapping[str, Any]) -> str:
    raw = str(fund.get("fund_type") or fund.get("fund_category") or "").strip()
    lowered = raw.casefold()
    if lowered in _TYPE_ALIASES:
        return lowered
    for token in ("qdii", "fof", "货币", "混合", "债券", "股票", "指数"):
        if token in lowered:
            return token
    return "unknown"


def _explicit_asset_class(exposure: Mapping[str, Any]) -> str | None:
    raw = str(exposure.get("asset_class") or "").strip().casefold()
    return _ASSET_ALIASES.get(raw)


def _asset_class_from_type_and_text(normalized_type: str, text: str) -> str:
    if "商品" in text or any(token in text for token in ("黄金", "原油", "commodity")):
        return "commodity"
    if "reits" in text or "reit" in text:
        return "reit"
    # QDII is a region wrapper, not an asset class.  Likewise, an index fund
    # can track bonds rather than equities.  Inspect the underlying asset before
    # applying the broad qdii/zs aliases.
    if normalized_type == "qdii":
        if "债" in text:
            return "bond"
        if "混合" in text:
            return "mixed"
        if "fof" in text or "基金中基金" in text:
            return "fof"
        return "equity"
    if normalized_type in {"zs", "指数"} and "债" in text:
        return "bond"
    if normalized_type in _TYPE_ALIASES:
        return _TYPE_ALIASES[normalized_type]
    if "货币" in text:
        return "money"
    if "债" in text:
        return "bond"
    if "混合" in text:
        return "mixed"
    if any(token in text for token in ("股票", "指数", "etf")):
        return "equity"
    return "unknown"


def _is_qdii(
    normalized_type: str, text: str, exposure: Mapping[str, Any]
) -> bool:
    overseas = exposure.get("overseas_percent")
    try:
        explicit_overseas = float(overseas) >= 50 if overseas is not None else False
    except (TypeError, ValueError):
        explicit_overseas = False
    return normalized_type == "qdii" or "qdii" in text or explicit_overseas


def _explicit_strategy(exposure: Mapping[str, Any]) -> str | None:
    raw = str(exposure.get("management_style") or "").strip().casefold()
    return _STRATEGY_ALIASES.get(raw)


def _strategy(
    exposure: Mapping[str, Any],
    normalized_type: str,
    text: str,
    asset_class: str,
) -> str:
    explicit = _explicit_strategy(exposure)
    if explicit:
        return explicit
    if asset_class == "fof" or normalized_type == "fof":
        return "fund_of_funds"
    if "指数增强" in text or "增强指数" in text:
        return "enhanced_index"
    if normalized_type in {"zs", "指数"} or any(
        token in text for token in ("指数", "etf", "联接")
    ):
        return "passive_index"
    if asset_class != "unknown":
        return "active"
    return "unknown"


def _bond_subtype(text: str, strategy: str) -> str:
    if "可转债" in text or re.search(r"(?<!纯)转债", text):
        return "convertible"
    if strategy in {"passive_index", "enhanced_index"}:
        return "bond_index"
    if any(token in text for token in ("超短债", "中短债", "短债")):
        return "short_duration"
    if "一级债" in text:
        return "primary_bond"
    if "二级债" in text:
        return "secondary_bond"
    if any(token in text for token in ("高收益债", "high yield")):
        return "high_yield"
    if any(token in text for token in ("投资级债", "investment grade")):
        return "investment_grade"
    if any(token in text for token in ("纯债", "利率债", "信用债", "中长期债")):
        return "pure_bond"
    return "unspecified"


def _mixed_subtype(text: str, exposure: Mapping[str, Any]) -> str:
    equity = _finite_percent(exposure.get("equity_percent"))
    bond = _finite_percent(exposure.get("bond_percent"))
    if equity is not None and equity >= 60:
        return "equity_biased"
    if bond is not None and bond >= 60:
        return "bond_biased"
    if equity is not None and bond is not None:
        return "balanced"
    if "偏股" in text:
        return "equity_biased"
    if "偏债" in text:
        return "bond_biased"
    if "平衡" in text:
        return "balanced"
    if "灵活" in text:
        return "flexible_allocation"
    return "unspecified"


def _qdii_subtype(asset_class: str, strategy: str, text: str) -> str:
    if asset_class == "commodity":
        return "commodity"
    if asset_class == "reit":
        return "reit"
    if asset_class == "bond":
        return "bond"
    if asset_class == "fof":
        return "fof"
    if asset_class == "mixed":
        return "mixed"
    if asset_class == "equity":
        return "equity_index" if strategy != "active" else "equity_active"
    if any(token in text for token in ("黄金", "原油", "商品")):
        return "commodity"
    return "unspecified"


def _qdii_region(text: str, exposure: Mapping[str, Any]) -> str:
    explicit = _text(exposure.get("region") or exposure.get("region_bucket"))
    if explicit:
        return _key_token(explicit)
    checks = (
        (("美国", "美股", "纳斯达克", "标普"), "united_states"),
        (("香港", "港股", "恒生"), "hong_kong"),
        (("日本", "日经"), "japan"),
        (("欧洲", "德国", "法国"), "europe"),
        (("亚洲", "亚太"), "asia_pacific"),
        (("新兴市场",), "emerging_markets"),
        (("全球", "世界"), "global"),
    )
    for tokens, result in checks:
        if any(token in text for token in tokens):
            return result
    return "unspecified"


def _fof_subtype(text: str, exposure: Mapping[str, Any]) -> str:
    explicit = _text(exposure.get("fof_subtype"))
    if explicit:
        return _key_token(explicit)
    if "目标日期" in text or re.search(r"20\d{2}", text):
        return "target_date"
    if "目标风险" in text or "稳健" in text or "进取" in text:
        return "target_risk"
    if "偏股" in text or "股票" in text:
        return "equity_fof"
    if "偏债" in text or "债券" in text:
        return "bond_fof"
    if "混合" in text:
        return "mixed_fof"
    return "unspecified"


def _risk_bucket(
    exposure: Mapping[str, Any], asset_class: str, mixed_subtype: str | None
) -> str | None:
    equity = _finite_percent(exposure.get("equity_percent"))
    if equity is not None:
        if equity >= 80:
            return "equity_80_plus"
        if equity >= 60:
            return "equity_60_80"
        if equity >= 30:
            return "equity_30_60"
        return "equity_below_30"
    if asset_class == "mixed" and mixed_subtype:
        return mixed_subtype
    return None


def _exposure_bucket(exposure: Mapping[str, Any]) -> str | None:
    return _text(
        exposure.get("exposure_bucket")
        or exposure.get("style_bucket")
        or exposure.get("primary_sector")
    )


def _benchmark_reference_code(benchmark: Mapping[str, Any]) -> str | None:
    if benchmark.get("comparison_role") == "unavailable":
        return None
    return _text(benchmark.get("benchmark_code"))


def _infer_tracking_reference(fund: Mapping[str, Any]) -> dict[str, Any] | None:
    """Infer a passive fund's exact tracking identity for peer grouping only.

    The source remains explicitly research-only and can never authorize a
    formal excess-return claim. Longest-name matching keeps similarly named
    indices separate.
    """

    for raw in (
        fund.get("tracking_reference_text"),
        fund.get("benchmark_text"),
        fund.get("fund_name"),
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        match = parse_benchmark_index(text)
        if match is None:
            continue
        return {
            "schema_version": "fund_benchmark_mapping.v1",
            "mapping_id": None,
            "benchmark_code": match.index_code,
            "benchmark_name": match.index_name or match.index_code,
            "available_at": None,
            "contract_verification_kind": "research_tracking_identity",
            "comparison_role": "tracking_reference",
            "formal_excess_eligible": False,
            "qualified": False,
            "reason": "research_tracking_identity_only",
        }
    return None


def _metric_profile(
    *,
    asset_class: str,
    management_style: str,
    region: str,
) -> str:
    if region == "overseas":
        return "qdii"
    if asset_class == "fof":
        return "fof"
    if asset_class == "money":
        return "money"
    if management_style == "passive_index":
        return "passive_index"
    if management_style == "enhanced_index":
        return "enhanced_index"
    if asset_class == "equity":
        return "equity"
    if asset_class == "mixed":
        return "mixed"
    if asset_class == "bond":
        return "bond"
    return "unknown"


def _oriented_percentile(
    target: float,
    peers: list[float],
    *,
    orientation: str,
) -> float:
    if orientation == "closer_to_zero_is_better":
        target_score = -abs(target)
        peer_scores = [-abs(value) for value in peers]
    elif orientation.startswith("lower_is_better"):
        target_score = -target
        peer_scores = [-value for value in peers]
    else:
        target_score = target
        peer_scores = peers
    less = sum(value < target_score for value in peer_scores)
    equal = sum(value == target_score for value in peer_scores)
    return round((less + equal * 0.5) / len(peers) * 100.0, 1)


def _share_class(fund: Mapping[str, Any]) -> str | None:
    explicit = _text(fund.get("share_class"))
    if explicit:
        return explicit.upper()
    normalized = re.sub(r"\s+", "", str(fund.get("fund_name") or ""))
    match = _SHARE_CLASS_RE.search(normalized)
    return match.group(1).upper() if match else None


def _share_class_priority(
    fund: Mapping[str, Any], preferred_share_class: str | None
) -> tuple[int, str, str]:
    share_class = _share_class(fund)
    priority = (
        0
        if preferred_share_class and share_class == preferred_share_class
        else 1 if share_class == "A" else 2
    )
    return (
        priority,
        str(fund.get("established_date") or "9999-12-31"),
        _fund_code(fund) or "999999",
    )


def _decision_instant(value: str | datetime) -> datetime:
    instant = value if isinstance(value, datetime) else _parse_instant(value)
    if instant is None or instant.tzinfo is None:
        raise ValueError("decision_at must be an ISO timestamp with timezone")
    return instant.astimezone(timezone.utc)


def _parse_instant(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite_percent(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and 0 <= parsed <= 100 else None


def _fund_code(fund: Mapping[str, Any]) -> str | None:
    raw = str(fund.get("fund_code") or "").strip()
    return raw.zfill(6) if raw.isdigit() and 1 <= len(raw) <= 6 else None


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _key_token(value: object) -> str:
    token = str(value or "unknown").strip().casefold()
    token = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "-", token).strip("-")
    return token or "unknown"


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _region_label(value: str) -> str:
    return {"domestic": "境内", "overseas": "QDII/境外"}.get(value, value)


def _asset_label(value: str) -> str:
    return {
        "equity": "权益",
        "bond": "债券",
        "mixed": "混合",
        "money": "货币",
        "commodity": "商品",
        "reit": "REIT",
        "fof": "FOF",
        "unknown": "未分类",
    }.get(value, value)


def _strategy_label(value: str) -> str:
    return {
        "active": "主动",
        "passive_index": "被动指数",
        "enhanced_index": "指数增强",
        "fund_of_funds": "基金中基金",
        "unknown": "策略未知",
    }.get(value, value)


def _bond_label(value: str | None) -> str:
    return {
        "convertible": "可转债",
        "bond_index": "债券指数",
        "short_duration": "短久期债券",
        "primary_bond": "一级债基",
        "secondary_bond": "二级债基",
        "high_yield": "高收益债",
        "investment_grade": "投资级债",
        "pure_bond": "纯债",
        "unspecified": "债券细分未知",
        None: "债券细分未知",
    }[value]


def _mixed_label(value: str | None) -> str:
    return {
        "equity_biased": "偏股混合",
        "bond_biased": "偏债混合",
        "balanced": "平衡混合",
        "flexible_allocation": "灵活配置",
        "unspecified": "混合风险暴露未知",
        None: "混合风险暴露未知",
    }[value]


def _fof_label(value: str | None) -> str:
    return {
        "target_date": "目标日期 FOF",
        "target_risk": "目标风险 FOF",
        "equity_fof": "权益 FOF",
        "bond_fof": "债券 FOF",
        "mixed_fof": "混合 FOF",
        "unspecified": "FOF 细分未知",
        None: "FOF 细分未知",
    }.get(value, str(value or "FOF 细分未知"))


def _qdii_subtype_label(value: str | None) -> str:
    return {
        "equity_index": "境外权益指数",
        "equity_active": "境外主动权益",
        "bond": "境外债券",
        "commodity": "境外商品",
        "reit": "境外 REIT",
        "fof": "境外 FOF",
        "mixed": "境外混合",
        "unspecified": "境外底层资产未知",
        None: "境外底层资产未知",
    }[value]


def _qdii_region_label(value: str | None) -> str:
    return {
        "united_states": "美国",
        "hong_kong": "中国香港",
        "japan": "日本",
        "europe": "欧洲",
        "asia_pacific": "亚太",
        "emerging_markets": "新兴市场",
        "global": "全球",
        "unspecified": "地域暴露未知",
        None: "地域暴露未知",
    }.get(value, str(value or "地域暴露未知"))


__all__ = [
    "MIN_INDEPENDENT_PEER_FAMILIES",
    "MIN_METRIC_COVERAGE",
    "PEER_GROUP_SCHEMA_VERSION",
    "PEER_METRIC_REGISTRY_VERSION",
    "PEER_RANK_SCHEMA_VERSION",
    "build_fund_peer_group",
    "build_peer_rank",
    "fund_family_key",
    "resolve_benchmark_comparison",
]
