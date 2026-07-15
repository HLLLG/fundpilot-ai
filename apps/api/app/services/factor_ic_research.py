"""Factor IC v2：分类 IC、多周期稳健统计与线上同源评分模型。"""

from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace
from typing import Any

from app.services.factor_ic_backtest import (
    SINGLE_FACTORS,
    NavPoint,
    _raw_factors_at,
    compute_factor_ic,
)
from app.services.fund_factors import (
    FACTOR_KEYS,
    FACTOR_WEIGHTS,
    FactorDetail,
    FundFactorInput,
    FundFactorScore,
    _composite_z,
    _factor_stats,
    _grade,
    _percentile_rank,
    _raw_factor,
    _zscore,
)
from app.services.fund_type_factors import (
    TYPE_FACTOR_LABELS,
    TYPE_FACTOR_SCHEMA_VERSION,
    compute_type_factor_values,
    type_factor_keys,
)

RESEARCH_MODEL_VERSION = "factor_ic.v2"
POINT_IN_TIME_RESEARCH_MODEL_VERSION = "factor_ic.v3"
EXECUTION_QUALIFICATION_METHOD = "pit_v3_statistical_and_economic"
DEFAULT_FORWARD_HORIZONS = (5, 20, 60)
DEFAULT_PRIMARY_HORIZON = 20
MIN_SEGMENT_CROSS_SECTION = 20
MIN_RELIABLE_PERIODS = 24
SEGMENT_LABELS = {
    "gp": "主动股票",
    "hh": "混合基金",
    "zq": "债券基金",
    "zs": "指数基金",
    "qdii": "QDII",
    "fof": "FOF",
    "unknown": "未分类",
}


def _segment_panels(
    nav_panel: dict[str, list[NavPoint]],
    classifications: dict[str, str],
) -> dict[str, dict[str, list[NavPoint]]]:
    panels: dict[str, dict[str, list[NavPoint]]] = {}
    for code, points in nav_panel.items():
        segment = classifications.get(code, "unknown")
        panels.setdefault(segment, {})[code] = points
    return panels


def compute_segmented_factor_ic(
    *,
    nav_panel: dict[str, list[NavPoint]],
    classifications: dict[str, str],
    rebalance_step: int,
    forward_horizons: tuple[int, ...] = DEFAULT_FORWARD_HORIZONS,
    factor_lookback: int,
) -> dict[str, dict[str, Any]]:
    segments: dict[str, dict[str, Any]] = {}
    for segment, panel in sorted(_segment_panels(nav_panel, classifications).items()):
        calendar = sorted({point.date for points in panel.values() for point in points})
        horizons: dict[str, Any] = {}
        stale_days = 7 if segment == "qdii" else 3
        for horizon in forward_horizons:
            result = compute_factor_ic(
                nav_panel=panel,
                calendar=calendar,
                rebalance_step=rebalance_step,
                forward_days=horizon,
                factor_lookback=factor_lookback,
                min_cross_section=MIN_SEGMENT_CROSS_SECTION,
                max_stale_days=stale_days,
            )
            rows = [
                {key: value for key, value in asdict(stats).items() if key != "ic_series"}
                for stats in result.factors
            ]
            qualified = {
                row["factor"]: bool(
                    row["n_periods"] >= MIN_RELIABLE_PERIODS
                    and row.get("mean_ic") is not None
                    and row.get("oos_mean_ic") is not None
                )
                for row in rows
            }
            horizons[str(horizon)] = {
                "available": result.available,
                "universe_size": result.universe_size,
                "rebalance_count": result.rebalance_count,
                "qualified": qualified,
                "factors": rows,
            }
        segments[segment] = {
            "label": SEGMENT_LABELS.get(segment, segment),
            "sampled_portfolios": len(panel),
            "horizons": horizons,
        }
    return segments


def build_peer_distributions(
    *,
    nav_panel: dict[str, list[NavPoint]],
    classifications: dict[str, str],
    factor_lookback: int,
) -> dict[str, dict[str, Any]]:
    raw_by_segment: dict[str, dict[str, dict[str, float | None]]] = {}
    typed_by_segment: dict[str, dict[str, dict[str, float | None]]] = {}
    for code, points in nav_panel.items():
        if len(points) < factor_lookback:
            continue
        segment = classifications.get(code, "unknown")
        navs = [point.nav for point in sorted(points, key=lambda point: point.date)][-factor_lookback:]
        raws = _raw_factors_at(navs)
        raw_by_segment.setdefault(segment, {})[code] = raws
        typed_by_segment.setdefault(segment, {})[code] = compute_type_factor_values(
            segment,
            navs,
        )

    result: dict[str, dict[str, Any]] = {}
    for segment, rows in sorted(raw_by_segment.items()):
        stats_by_factor = {
            factor: _factor_stats([row.get(factor) for row in rows.values()])
            for factor in SINGLE_FACTORS
        }
        z_values: dict[str, list[float]] = {factor: [] for factor in SINGLE_FACTORS}
        composite_values: list[float] = []
        for row in rows.values():
            factor_z = {
                factor: _zscore(row.get(factor), stats_by_factor[factor])
                for factor in SINGLE_FACTORS
            }
            for factor, value in factor_z.items():
                if value is not None:
                    z_values[factor].append(value)
            weighted = {key: factor_z.get(key) for key in FACTOR_WEIGHTS}
            composite = _composite_z(weighted)
            if composite is not None:
                composite_values.append(composite)

        factors: dict[str, Any] = {}
        for factor in SINGLE_FACTORS:
            stats = stats_by_factor[factor]
            factors[factor] = {
                "mean": round(stats.mean, 8) if stats is not None else None,
                "std": round(stats.std, 8) if stats is not None else None,
                "z_values": [round(value, 6) for value in sorted(z_values[factor])],
                "valid_count": len(z_values[factor]),
            }
        factors["composite"] = {
            "z_values": [round(value, 6) for value in sorted(composite_values)],
            "valid_count": len(composite_values),
        }
        typed_factors: dict[str, Any] = {}
        typed_rows = typed_by_segment.get(segment) or {}
        for factor in type_factor_keys(segment, benchmark_available=False):
            typed_stats = _factor_stats(
                [row.get(factor) for row in typed_rows.values()]
            )
            typed_z_values = [
                value
                for row in typed_rows.values()
                if (
                    value := _zscore(row.get(factor), typed_stats)
                ) is not None
            ]
            typed_factors[factor] = {
                "label": TYPE_FACTOR_LABELS.get(factor, factor),
                "mean": round(typed_stats.mean, 8) if typed_stats is not None else None,
                "std": round(typed_stats.std, 8) if typed_stats is not None else None,
                "z_values": [round(value, 6) for value in sorted(typed_z_values)],
                "valid_count": len(typed_z_values),
                "orientation": "higher_is_better",
            }
        result[segment] = {
            "label": SEGMENT_LABELS.get(segment, segment),
            "eligible_count": len(rows),
            "factors": factors,
            "type_factor_schema": TYPE_FACTOR_SCHEMA_VERSION,
            "type_factors": typed_factors,
        }
    return result


def build_research_model(
    *,
    nav_panel: dict[str, list[NavPoint]],
    sampled_rows: list[dict],
    all_rows: list[dict],
    factor_lookback: int,
    rebalance_step: int,
    forward_horizons: tuple[int, ...] = DEFAULT_FORWARD_HORIZONS,
) -> dict[str, Any]:
    sampled_classifications = {
        str(row.get("fund_code")): str(row.get("fund_type") or "unknown")
        for row in sampled_rows
    }
    all_classifications = {
        str(row.get("fund_code")): str(row.get("fund_type") or "unknown")
        for row in all_rows
        if row.get("fund_code")
    }
    return {
        "version": RESEARCH_MODEL_VERSION,
        "cohort_mode": "current_survivors",
        "primary_horizon": DEFAULT_PRIMARY_HORIZON,
        "factor_lookback": factor_lookback,
        "forward_horizons": list(forward_horizons),
        "segments": compute_segmented_factor_ic(
            nav_panel=nav_panel,
            classifications=sampled_classifications,
            rebalance_step=rebalance_step,
            forward_horizons=forward_horizons,
            factor_lookback=factor_lookback,
        ),
        "peer_distributions": build_peer_distributions(
            nav_panel=nav_panel,
            classifications=sampled_classifications,
            factor_lookback=factor_lookback,
        ),
        "fund_classifications": all_classifications,
    }


def build_v3_research_model(
    *,
    nav_panel: dict[str, list[NavPoint]],
    universe_snapshots: list[dict[str, Any]],
    current_all_rows: list[dict[str, Any]],
    factor_lookback: int,
    rebalance_step: int,
    forward_horizons: tuple[int, ...] = DEFAULT_FORWARD_HORIZONS,
    max_snapshot_age_days: int = 7,
    walk_forward_folds: int = 5,
    embargo_trading_days: int = 20,
) -> dict[str, Any]:
    """Build the publishable v3 model from historical point-in-time cohorts.

    Peer distributions intentionally use only the latest observable snapshot;
    historical or currently-surviving funds outside that snapshot cannot leak
    into the online comparison population.
    """
    from app.services.factor_ic_pit import (
        compute_point_in_time_segmented_ic,
        normalize_universe_snapshots,
        select_asof_snapshot,
    )

    segments, pit_coverage = compute_point_in_time_segmented_ic(
        nav_panel=nav_panel,
        snapshots=universe_snapshots,
        rebalance_step=rebalance_step,
        forward_horizons=forward_horizons,
        factor_lookback=factor_lookback,
        max_snapshot_age_days=max_snapshot_age_days,
        walk_forward_folds=walk_forward_folds,
        embargo_days=embargo_trading_days,
    )
    calendar = sorted(
        {point.date for points in nav_panel.values() for point in points}
    )
    latest = (
        select_asof_snapshot(
            normalize_universe_snapshots(universe_snapshots),
            calendar[-1],
            max_age_days=max_snapshot_age_days,
        )
        if calendar
        else None
    )
    peer_classifications = {
        member.fund_code: member.fund_type
        for member in (latest.members if latest is not None else ())
        if member.fund_code in nav_panel
    }
    peer_panel = {
        code: nav_panel[code]
        for code in peer_classifications
        if code in nav_panel
    }
    peers = build_peer_distributions(
        nav_panel=peer_panel,
        classifications=peer_classifications,
        factor_lookback=factor_lookback,
    )
    # Online targets need a type lookup for the whole current catalogue.  This
    # map is not used to form historical IC cohorts; it only routes a current
    # fund to the peer distribution learned from the latest PIT snapshot.
    classifications = {
        str(row.get("fund_code")): str(row.get("fund_type") or "unknown").lower()
        for row in current_all_rows
        if row.get("fund_code")
    }
    point_in_time = {
        **pit_coverage,
        "snapshot_id": latest.snapshot_id if latest is not None else None,
        "snapshot_date": (
            latest.snapshot_date.isoformat() if latest is not None else None
        ),
        "max_snapshot_age_days": max_snapshot_age_days,
        "walk_forward_folds": walk_forward_folds,
        "embargo_trading_days": embargo_trading_days,
        "multiple_testing": "benjamini_hochberg",
        "fdr_q_threshold": 0.10,
    }
    model = {
        "version": POINT_IN_TIME_RESEARCH_MODEL_VERSION,
        "cohort_mode": "point_in_time",
        "primary_horizon": DEFAULT_PRIMARY_HORIZON,
        "factor_lookback": factor_lookback,
        "forward_horizons": list(forward_horizons),
        "point_in_time": point_in_time,
        "pit_coverage": dict(pit_coverage),
        "validation": {
            "method": "expanding_walk_forward",
            "folds": walk_forward_folds,
            "embargo_trading_days": embargo_trading_days,
            "multiple_test": "benjamini_hochberg",
            "fdr_q_threshold": 0.10,
        },
        "economic_significance": {
            "schema_version": "factor_economic_significance.v1",
            "label_type": "peer_group_relative_total_return",
            "benchmark": "same_segment_cross_section_median",
            "point_in_time_scope": "membership_only",
            "nav_revision_pit": False,
            "entry_rule": "next_trading_day_first_available_nav",
            "entry_offset_trading_days": 1,
            "quantiles": 5,
            "cost_rates": [0.0, 0.005, 0.01],
            "qualification_cost_rate": 0.005,
            "minimum_periods": 36,
            "minimum_coverage_rate": 0.80,
            "minimum_top_net_positive_ratio": 0.55,
        },
        "segments": segments,
        "peer_distributions": peers,
        "fund_classifications": classifications,
    }
    point_in_time["publishable"] = is_v3_research_model_publishable(model)
    return model


def is_v3_research_model_publishable(model: dict[str, Any]) -> bool:
    """Cheap runner gate; the Pydantic publish contract remains authoritative."""
    point_in_time = model.get("point_in_time") or {}
    if not bool(point_in_time.get("ready")):
        return False
    if int(point_in_time.get("walk_forward_folds") or 0) != 5:
        return False
    if int(point_in_time.get("embargo_trading_days") or 0) != 20:
        return False
    if str(point_in_time.get("multiple_testing")) != "benjamini_hochberg":
        return False
    if point_in_time.get("point_in_time_scope") != "membership_only":
        return False
    if point_in_time.get("nav_revision_pit") is not False:
        return False
    if point_in_time.get("nav_publication_lag_trading_days") != {
        "default": 1,
        "qdii": 2,
    }:
        return False
    if int(point_in_time.get("execution_entry_offset_trading_days") or 0) != 1:
        return False
    primary = str(model.get("primary_horizon") or DEFAULT_PRIMARY_HORIZON)
    peers = model.get("peer_distributions") or {}
    qualified_segments = 0
    for segment_key, segment in (model.get("segments") or {}).items():
        horizon = (segment.get("horizons") or {}).get(primary) or {}
        rows = {
            str(row.get("factor")): row
            for row in horizon.get("factors") or []
            if isinstance(row, dict) and row.get("factor")
        }
        qualified = horizon.get("qualified") or {}
        economically_qualified = any(
            bool(value)
            and ((rows.get(str(factor)) or {}).get("economic_significance") or {}).get(
                "qualified"
            )
            is True
            for factor, value in qualified.items()
        )
        if economically_qualified:
            peer = peers.get(segment_key) or {}
            if int(peer.get("eligible_count") or 0) >= 20:
                qualified_segments += 1
    return (
        qualified_segments >= 4
        and len(model.get("fund_classifications") or {}) >= 5_000
    )


def _stored_stats(payload: dict[str, Any] | None) -> SimpleNamespace | None:
    if not isinstance(payload, dict):
        return None
    mean = payload.get("mean")
    std = payload.get("std")
    if mean is None or std is None:
        return None
    return SimpleNamespace(mean=float(mean), std=float(std))


def _execution_qualified_factor_keys(
    *,
    model: dict[str, Any],
    qualified: dict[str, Any],
    factor_rows: dict[str, dict[str, Any]],
    details: dict[str, FactorDetail],
    qualified_type_keys: list[str],
    typed_percentiles: dict[str, float | None],
) -> list[str]:
    """Return only target-usable factors passing both PIT qualification gates."""
    if (
        model.get("version") != POINT_IN_TIME_RESEARCH_MODEL_VERSION
        or model.get("cohort_mode") != "point_in_time"
    ):
        return []

    common = [
        key
        for key in SINGLE_FACTORS
        if qualified.get(key) is True
        and details.get(key) is not None
        and details[key].percentile is not None
        and ((factor_rows.get(key) or {}).get("economic_significance") or {}).get(
            "qualified"
        )
        is True
    ]
    typed = [
        key
        for key in qualified_type_keys
        if typed_percentiles.get(key) is not None
        and ((factor_rows.get(key) or {}).get("economic_significance") or {}).get(
            "qualified"
        )
        is True
    ]
    return sorted(set(common + typed))


def score_targets_with_research_model(
    *,
    targets: list[FundFactorInput],
    model: dict[str, Any],
) -> list[dict[str, Any]]:
    classifications = model.get("fund_classifications") or {}
    peer_distributions = model.get("peer_distributions") or {}
    scored: list[dict[str, Any]] = []
    for target in targets:
        segment = str(classifications.get(target.fund_code) or "unknown")
        peer = peer_distributions.get(segment) or {}
        stored_factors = peer.get("factors") or {}
        factor_z: dict[str, float | None] = {}
        details: dict[str, FactorDetail] = {}
        for factor in FACTOR_KEYS:
            raw = _raw_factor(target, factor)
            stored = stored_factors.get(factor) or {}
            stats = _stored_stats(stored)
            z = _zscore(raw, stats)
            population = [float(value) for value in stored.get("z_values") or []]
            factor_z[factor] = z
            details[factor] = FactorDetail(
                raw=raw,
                z=z,
                percentile=_percentile_rank(z, population),
            )
        composite = _composite_z(factor_z)
        composite_population = [
            float(value)
            for value in (stored_factors.get("composite") or {}).get("z_values") or []
        ]
        composite_score = _percentile_rank(composite, composite_population)
        primary_horizon = str(model.get("primary_horizon") or DEFAULT_PRIMARY_HORIZON)
        segment_row = (model.get("segments") or {}).get(segment) or {}
        horizon_row = (segment_row.get("horizons") or {}).get(primary_horizon) or {}
        qualified = horizon_row.get("qualified") or {}
        factor_rows = {
            str(row.get("factor")): row
            for row in horizon_row.get("factors") or []
            if isinstance(row, dict) and row.get("factor")
        }
        type_model_enabled = bool(
            model.get("version") == POINT_IN_TIME_RESEARCH_MODEL_VERSION
            and model.get("cohort_mode") == "point_in_time"
            and (segment_row.get("type_factor_model") or {}).get("schema_version")
            == TYPE_FACTOR_SCHEMA_VERSION
        )
        qualified_type_keys = [
            key
            for key in type_factor_keys(segment, benchmark_available=False)
            if type_model_enabled
            and qualified.get(key) is True
            and (factor_rows.get(key) or {}).get("factor_family")
            == "fund_type_specific"
            and ((factor_rows.get(key) or {}).get("economic_significance") or {}).get(
                "qualified"
            )
            is True
        ]
        stored_type_factors = peer.get("type_factors") or {}
        typed_details: dict[str, dict[str, Any]] = {}
        typed_reliability: dict[str, dict[str, Any]] = {}
        typed_percentiles: dict[str, float | None] = {}
        typed_values = target.typed_feature_values or {}
        typed_input_valid = bool(
            target.typed_feature_meta.get("schema_version")
            == TYPE_FACTOR_SCHEMA_VERSION
            and target.typed_feature_meta.get("source") == "point_in_time_nav"
            and int(target.typed_feature_meta.get("lookback_points") or 0)
            >= factor_lookback_from_model(model)
            and target.feature_freshness != "insufficient"
        )
        for key in qualified_type_keys:
            stored = stored_type_factors.get(key) or {}
            raw = typed_values.get(key) if typed_input_valid else None
            stats = _stored_stats(stored)
            z_value = _zscore(raw, stats)
            percentile = _percentile_rank(
                z_value,
                [float(value) for value in stored.get("z_values") or []],
            )
            typed_percentiles[key] = percentile
            typed_details[key] = {
                "raw": raw,
                "z": z_value,
                "percentile": percentile,
                "label": TYPE_FACTOR_LABELS.get(key, key),
                "orientation": "higher_is_better",
            }
            row = factor_rows.get(key) or {}
            economic = row.get("economic_significance") or {}
            point_in_time_meta = model.get("point_in_time") or {}
            nav_observation_pit = bool(
                point_in_time_meta.get("point_in_time_scope")
                == "nav_observation_pit"
                and point_in_time_meta.get("nav_revision_pit") is True
            )
            typed_reliability[key] = {
                "level": (
                    "高"
                    if percentile is not None and nav_observation_pit
                    else "中" if percentile is not None else "不足"
                ),
                "orientation": "higher_is_better",
                "basis": (
                    f"PIT同类未来{primary_horizon}日已通过统计与经济门槛"
                    f"（IC {float(row.get('mean_ic') or 0):+.3f}，"
                    f"净成本正收益率 {float(economic.get('top_net_positive_ratio') or 0):.0%}）"
                    + (
                        ""
                        if nav_observation_pit
                        else "；当前仅成员PIT、NAV修订时点未冻结，最高中等置信"
                    )
                    if percentile is not None
                    else "目标基金的同源时点 NAV 特征不完整"
                ),
                "qualified": percentile is not None,
                "economic_significance": economic,
            }
        valid_typed_percentiles = [
            float(value) for value in typed_percentiles.values() if value is not None
        ]
        typed_complete = bool(
            qualified_type_keys
            and len(valid_typed_percentiles) == len(qualified_type_keys)
            and typed_input_valid
        )
        typed_score = (
            sum(valid_typed_percentiles) / len(valid_typed_percentiles)
            if typed_complete
            else None
        )
        feature_count = sum(
            detail.percentile is not None for detail in details.values()
        )
        descriptive_applicable = bool(
            segment != "unknown"
            and feature_count >= 2
            and target.feature_freshness != "insufficient"
        )
        execution_factor_keys = _execution_qualified_factor_keys(
            model=model,
            qualified=qualified,
            factor_rows=factor_rows,
            details=details,
            qualified_type_keys=qualified_type_keys,
            typed_percentiles=typed_percentiles,
        )
        execution_qualified = bool(
            descriptive_applicable
            and target.feature_freshness == "fresh"
            and execution_factor_keys
        )
        if not descriptive_applicable:
            execution_reason = "descriptive_factor_input_not_applicable"
        elif target.feature_freshness != "fresh":
            execution_reason = "target_factor_feature_not_fresh"
        elif not execution_factor_keys:
            execution_reason = "no_statistically_and_economically_qualified_factor"
        else:
            execution_reason = None
        # 仅 PIT v3 + 经济门槛合格 + 目标时点特征完整时，类型因子才进入线上分数。
        base_composite_score = composite_score
        if typed_score is not None and composite_score is not None:
            composite_score = round(composite_score * 0.70 + typed_score * 0.30, 1)
        fund = FundFactorScore(
            fund_code=target.fund_code,
            fund_name=target.fund_name,
            in_universe=target.fund_code in classifications,
            composite_score=composite_score,
            composite_grade=_grade(composite_score),
            factors=details,
        )
        scored.append(
            {
                **asdict(fund),
                "peer_group": segment,
                "peer_group_label": SEGMENT_LABELS.get(segment, segment),
                "peer_count": int(peer.get("eligible_count") or 0),
                "feature_count": feature_count,
                "feature_completeness": round(feature_count / len(FACTOR_KEYS), 2),
                # `applicable` remains descriptive for backward compatibility.
                # It must not be used as an execution whitelist.
                "applicable": descriptive_applicable,
                "descriptive_applicable": descriptive_applicable,
                "execution_qualified": execution_qualified,
                "execution_qualified_factor_keys": execution_factor_keys,
                "execution_qualification": {
                    "status": "qualified" if execution_qualified else "insufficient",
                    "method": EXECUTION_QUALIFICATION_METHOD,
                    "primary_horizon_days": primary_horizon,
                    "reason": execution_reason,
                },
                "base_composite_score": base_composite_score,
                "typed_factor_schema": TYPE_FACTOR_SCHEMA_VERSION,
                "typed_factor_candidates": qualified_type_keys,
                "typed_factor_details": typed_details,
                "typed_factor_percentiles": typed_percentiles,
                "typed_factor_reliability": typed_reliability,
                "typed_feature_completeness": (
                    round(len(valid_typed_percentiles) / len(qualified_type_keys), 2)
                    if qualified_type_keys
                    else 0.0
                ),
                "typed_factor_applicable": typed_complete,
                "typed_factor_score": round(typed_score, 1) if typed_score is not None else None,
                "typed_factor_basis": (
                    "仅使用通过 PIT IC、FDR、walk-forward 与净成本经济门槛的类型因子"
                    if typed_complete
                    else "没有合格类型因子，或目标基金时点 NAV 特征不完整；未参与线上评分"
                ),
                "target_feature_as_of": target.feature_as_of,
                "target_feature_observed_at": target.feature_observed_at,
                "target_feature_source": target.feature_source,
                "target_return_coverage": target.return_coverage,
                "target_nav_age_trading_days": target.nav_age_trading_days,
                "target_feature_freshness": target.feature_freshness,
                "target_feature_max_age_trading_days": (
                    target.feature_max_age_trading_days
                ),
            }
        )
    return scored


def factor_lookback_from_model(model: dict[str, Any]) -> int:
    """线上模型固定使用与离线同口径的 NAV lookback，缺失时保守取 250。"""
    try:
        value = int(
            model.get("factor_lookback")
            or (model.get("params") or {}).get("factor_lookback")
            or 250
        )
    except (TypeError, ValueError):
        value = 250
    return max(250, value)
