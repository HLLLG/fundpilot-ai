"""Deterministic point-in-time peer baselines for discovery strategy evaluation."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

from app.services.benchmark_fee_evaluation import resolve_user_assumption_fee
from app.services.fund_factor_nav import build_total_return_index


SELECTION_BASELINE_CONTRACT_VERSION = "selection_baselines.v1"


def freeze_candidate_baselines(
    *,
    report: Mapping[str, Any],
    facts: Mapping[str, Any],
    recommendation: Mapping[str, Any],
) -> dict[str, Any]:
    """Select peer arms only from the candidate snapshot known at decision time."""

    raw_pool = report.get("candidate_pool") or facts.get("candidate_pool") or []
    pool = [dict(row) for row in raw_pool if isinstance(row, Mapping)]
    target_code = _fund_code(recommendation.get("fund_code"))
    target_sector = str(
        recommendation.get("sector_name")
        or recommendation.get("sector_label")
        or ""
    ).strip()
    candidates = [
        row
        for row in pool
        if _fund_code(row.get("fund_code")) not in {None, target_code}
        and _eligible_candidate(row)
        and (
            not target_sector
            or str(row.get("sector_label") or row.get("sector_name") or "").strip()
            == target_sector
        )
    ]
    scope = {
        "candidate_snapshot_count": len(pool),
        "eligible_peer_count": len(candidates),
        "sector": target_sector or None,
        "target_fund_code": target_code,
    }
    quality = _select_max(
        candidates,
        key=lambda row: _finite_float(row.get("fund_quality_score")),
    )
    fee = _select_min(candidates, key=_candidate_cost_percent)
    seed_material = f"{report.get('id') or ''}:{target_code or ''}:{target_sector}"
    random_peer = (
        min(
            candidates,
            key=lambda row: hashlib.sha256(
                f"{seed_material}:{_fund_code(row.get('fund_code'))}".encode("utf-8")
            ).hexdigest(),
        )
        if candidates
        else None
    )
    return {
        "schema_version": SELECTION_BASELINE_CONTRACT_VERSION,
        "frozen_at": report.get("created_at"),
        "scope": scope,
        "quality_only_peer": _baseline_spec(
            quality,
            selection_basis="highest_fund_quality_score",
            score=(
                _finite_float(quality.get("fund_quality_score"))
                if quality is not None
                else None
            ),
            unavailable_reason="eligible_same_sector_quality_peer_unavailable",
        ),
        "low_fee_peer": _baseline_spec(
            fee,
            selection_basis="lowest_frozen_cost_upper_bound_percent",
            score=_candidate_cost_percent(fee) if fee is not None else None,
            unavailable_reason="eligible_same_sector_cost_peer_unavailable",
        ),
        "seeded_random_peer": _baseline_spec(
            random_peer,
            selection_basis="sha256_seeded_peer_selection",
            score=None,
            unavailable_reason="eligible_same_sector_random_peer_unavailable",
            extra={"seed_hash": hashlib.sha256(seed_material.encode("utf-8")).hexdigest()},
        ),
    }


def evaluate_candidate_baselines(
    specs: object,
    *,
    execution_date: str,
    horizon: int,
    target_net_return_percent: float | None,
    fetch_nav,
    trading_days: int,
    fee_policy: object,
) -> dict[str, Any]:
    frozen = dict(specs) if isinstance(specs, Mapping) else {}
    fee = resolve_user_assumption_fee(fee_policy)
    results: dict[str, Any] = {
        "schema_version": SELECTION_BASELINE_CONTRACT_VERSION,
        "horizon_trading_days": horizon,
        "target_metric": "positive_net_total_return_percent",
        "comparators": {},
    }
    for name in ("quality_only_peer", "low_fee_peer", "seeded_random_peer"):
        spec = frozen.get(name)
        row = dict(spec) if isinstance(spec, Mapping) else {}
        result = {
            "status": "unavailable",
            "fund_code": row.get("fund_code"),
            "fund_name": row.get("fund_name"),
            "selection_basis": row.get("selection_basis"),
            "gross_total_return_percent": None,
            "net_total_return_percent": None,
            "target_net_value_add_percent": None,
            "unavailable_reason": row.get("unavailable_reason") or "baseline_not_frozen",
        }
        if row.get("status") != "selected" or not row.get("fund_code"):
            results["comparators"][name] = result
            continue
        if not fee["available"]:
            result["unavailable_reason"] = "fee_assumption_not_frozen"
            results["comparators"][name] = result
            continue
        try:
            payload = fetch_nav(str(row["fund_code"]), trading_days=trading_days)
        except Exception as exc:  # provider state is recorded, not promoted to endpoint failure
            result["unavailable_reason"] = f"nav_provider_error:{type(exc).__name__}"
            results["comparators"][name] = result
            continue
        raw_rows = None
        if isinstance(payload, Mapping):
            raw_rows = payload.get("data") or payload.get("rows")
        series = build_total_return_index(raw_rows if isinstance(raw_rows, list) else [])
        points = series.points
        baseline_index = next(
            (index for index, (day, _value) in enumerate(points) if day >= execution_date),
            None,
        )
        if baseline_index is None or baseline_index + horizon >= len(points):
            result["unavailable_reason"] = "baseline_or_target_total_return_unavailable"
            results["comparators"][name] = result
            continue
        baseline_date, baseline = points[baseline_index]
        target_date, target = points[baseline_index + horizon]
        gross = round((target / baseline - 1.0) * 100.0, 4)
        net = round(gross - float(fee["rate_percent"]), 4)
        target_value_add = (
            round(float(target_net_return_percent) - net, 4)
            if target_net_return_percent is not None
            else None
        )
        result.update(
            {
                "status": "mature",
                "baseline_date": baseline_date,
                "target_date": target_date,
                "gross_total_return_percent": gross,
                "net_total_return_percent": net,
                "target_net_value_add_percent": target_value_add,
                "target_outperformed": (
                    target_value_add > 0 if target_value_add is not None else None
                ),
                "fee": fee,
                "unavailable_reason": None,
            }
        )
        results["comparators"][name] = result
    return results


def summarize_candidate_baselines(rows: Iterable[object]) -> dict[str, Any]:
    evaluations = [dict(row) for row in rows if isinstance(row, Mapping)]
    summary: dict[str, Any] = {
        "schema_version": SELECTION_BASELINE_CONTRACT_VERSION,
        "comparison_metric": "target_net_value_add_percent",
        "comparators": {},
    }
    for name in ("quality_only_peer", "low_fee_peer", "seeded_random_peer"):
        comparator_rows = [
            dict(comparator)
            for evaluation in evaluations
            if isinstance(
                comparator := (evaluation.get("comparators") or {}).get(name),
                Mapping,
            )
        ]
        mature = [row for row in comparator_rows if row.get("status") == "mature"]
        comparable = [
            row
            for row in mature
            if _finite_float(row.get("target_net_value_add_percent")) is not None
        ]
        wins = sum(1 for row in comparable if row.get("target_outperformed") is True)
        values = [
            value
            for row in comparable
            if (value := _finite_float(row.get("target_net_value_add_percent")))
            is not None
        ]
        summary["comparators"][name] = {
            "eligible_count": len(comparator_rows),
            "mature_count": len(mature),
            "comparable_count": len(comparable),
            "coverage_percent": (
                round(len(comparable) / len(comparator_rows) * 100.0, 1)
                if comparator_rows
                else None
            ),
            "target_outperform_count": wins,
            "target_outperform_rate_percent": (
                round(wins / len(comparable) * 100.0, 1) if comparable else None
            ),
            "average_target_net_value_add_percent": (
                round(sum(values) / len(values), 4) if values else None
            ),
        }
    return summary


def _baseline_spec(
    row: dict[str, Any] | None,
    *,
    selection_basis: str,
    score: float | None,
    unavailable_reason: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if row is None:
        return {
            "status": "unavailable",
            "selection_basis": selection_basis,
            "fund_code": None,
            "fund_name": None,
            "selection_score": None,
            "unavailable_reason": unavailable_reason,
            **dict(extra or {}),
        }
    return {
        "status": "selected",
        "selection_basis": selection_basis,
        "fund_code": _fund_code(row.get("fund_code")),
        "fund_name": str(row.get("fund_name") or "").strip(),
        "fund_type": str(row.get("fund_type") or "").strip() or None,
        "sector": str(row.get("sector_label") or row.get("sector_name") or "").strip()
        or None,
        "selection_score": score,
        "candidate_snapshot_hash": hashlib.sha256(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest(),
        "unavailable_reason": None,
        **dict(extra or {}),
    }


def _eligible_candidate(row: Mapping[str, Any]) -> bool:
    gate = row.get("quality_gate")
    status = str(gate.get("status") or "") if isinstance(gate, Mapping) else ""
    return status not in {"excluded", "watch_only"}


def _candidate_cost_percent(row: Mapping[str, Any] | None) -> float | None:
    if not isinstance(row, Mapping):
        return None
    containers = [
        row,
        row.get("cost_assessment"),
        row.get("tradeability"),
    ]
    keys = (
        "estimated_total_cost_upper_bound_percent",
        "standard_cost_upper_bound_percent",
        "total_cost_upper_bound_percent",
    )
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in keys:
            value = _finite_float(container.get(key))
            if value is not None and value >= 0:
                return value
    family = row.get("share_family")
    if isinstance(family, Mapping):
        costs = family.get("member_cost_upper_bound_percent")
        code = _fund_code(row.get("fund_code"))
        if isinstance(costs, Mapping) and code:
            value = _finite_float(costs.get(code))
            if value is not None and value >= 0:
                return value
    return None


def _select_max(rows: list[dict[str, Any]], *, key) -> dict[str, Any] | None:
    scored = [(score, row) for row in rows if (score := key(row)) is not None]
    return max(scored, key=lambda item: (item[0], _fund_code(item[1].get("fund_code")) or ""))[1] if scored else None


def _select_min(rows: list[dict[str, Any]], *, key) -> dict[str, Any] | None:
    scored = [(score, row) for row in rows if (score := key(row)) is not None]
    return min(scored, key=lambda item: (item[0], _fund_code(item[1].get("fund_code")) or ""))[1] if scored else None


def _fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    normalized = text.zfill(6)
    return normalized if len(normalized) == 6 and normalized != "000000" else None


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
