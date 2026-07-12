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

RESEARCH_MODEL_VERSION = "factor_ic.v2"
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
    for code, points in nav_panel.items():
        if len(points) < factor_lookback:
            continue
        segment = classifications.get(code, "unknown")
        navs = [point.nav for point in sorted(points, key=lambda point: point.date)][-factor_lookback:]
        raws = _raw_factors_at(navs)
        raw_by_segment.setdefault(segment, {})[code] = raws

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
        result[segment] = {
            "label": SEGMENT_LABELS.get(segment, segment),
            "eligible_count": len(rows),
            "factors": factors,
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


def _stored_stats(payload: dict[str, Any] | None) -> SimpleNamespace | None:
    if not isinstance(payload, dict):
        return None
    mean = payload.get("mean")
    std = payload.get("std")
    if mean is None or std is None:
        return None
    return SimpleNamespace(mean=float(mean), std=float(std))


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
        fund = FundFactorScore(
            fund_code=target.fund_code,
            fund_name=target.fund_name,
            in_universe=target.fund_code in classifications,
            composite_score=composite_score,
            composite_grade=_grade(composite_score),
            factors=details,
        )
        feature_count = sum(detail.percentile is not None for detail in details.values())
        scored.append(
            {
                **asdict(fund),
                "peer_group": segment,
                "peer_group_label": SEGMENT_LABELS.get(segment, segment),
                "peer_count": int(peer.get("eligible_count") or 0),
                "feature_count": feature_count,
                "feature_completeness": round(feature_count / len(FACTOR_KEYS), 2),
                "applicable": bool(segment != "unknown" and feature_count >= 2),
            }
        )
    return scored
