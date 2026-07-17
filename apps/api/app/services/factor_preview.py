from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import floor, isfinite
from statistics import fmean
from typing import Any


PREVIEW_SCHEMA_VERSION = "factor_preview.v1"
PREVIEW_MODEL_VERSION = "factor_ic.v2"
_RELIABLE_LEVELS = {"中", "高"}
_MIN_PEER_COUNT = 20
_MIN_FEATURE_COUNT = 2
_MIN_FEATURE_COMPLETENESS = 0.5
_MIN_RETURN_COVERAGE = 0.9


def _number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _normalized_code(value: object) -> str:
    raw = str(value or "").strip()
    return raw.zfill(6) if raw else ""


def _normalized_sector(value: object) -> str:
    return str(value or "").strip().casefold()


def _row_score(row: Mapping[str, object]) -> tuple[float | None, list[str]]:
    reliability = row.get("factor_reliability")
    percentiles = row.get("factor_percentiles")
    if not isinstance(reliability, Mapping) or not isinstance(percentiles, Mapping):
        return None, []
    keys: list[str] = []
    values: list[float] = []
    for key, detail in reliability.items():
        if not isinstance(detail, Mapping):
            continue
        if str(detail.get("level") or "").strip() not in _RELIABLE_LEVELS:
            continue
        percentile = _number(percentiles.get(key))
        if percentile is None or not 0 <= percentile <= 100:
            continue
        keys.append(str(key))
        values.append(percentile)
    return (round(fmean(values), 2), keys) if values else (None, [])


def _row_quality_reason(row: Mapping[str, object]) -> str | None:
    if row.get("descriptive_applicable") is not True:
        return "同类分类或因子特征不完整"
    peer_count = _number(row.get("peer_count"))
    feature_count = _number(row.get("feature_count"))
    completeness = _number(row.get("feature_completeness"))
    coverage = _number(row.get("target_return_coverage"))
    if peer_count is None or peer_count < _MIN_PEER_COUNT:
        return f"同类有效样本少于 {_MIN_PEER_COUNT} 只"
    if feature_count is None or feature_count < _MIN_FEATURE_COUNT:
        return "可用因子少于 2 个"
    if completeness is None or completeness < _MIN_FEATURE_COMPLETENESS:
        return "因子特征完整度不足"
    if row.get("target_feature_freshness") != "fresh":
        return "目标基金净值特征不是当前新鲜状态"
    if coverage is None or coverage < _MIN_RETURN_COVERAGE:
        return "目标基金收益序列覆盖不足 90%"
    return None


def _adjustment_for_score(score: float, maximum: float) -> float:
    if score >= 85:
        raw = 10.0
    elif score >= 70:
        raw = 5.0
    elif score <= 15:
        raw = -10.0
    elif score <= 30:
        raw = -5.0
    else:
        raw = 0.0
    return round(max(-maximum, min(maximum, raw)), 2)


def _candidate_sector_by_code(
    candidate_pool: Sequence[Mapping[str, object]] | None,
) -> dict[str, str]:
    return {
        _normalized_code(item.get("fund_code")): _normalized_sector(
            item.get("sector_label") or item.get("sector_name")
        )
        for item in candidate_pool or []
        if _normalized_code(item.get("fund_code"))
    }


def _base_contract(*, mode: str, maximum: float) -> dict[str, Any]:
    return {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "label": "量化试运行",
        "mode": mode,
        "status": "ineligible",
        "application_status": "not_applied",
        "evidence_role": "bounded_initial_tranche_modifier_only",
        "survivorship_bias": True,
        "confidence_cap": "中",
        "max_adjustment_percent": maximum,
        "proposed_adjustment_percent": 0.0,
        "applied_adjustment_percent": 0.0,
        "qualifying_factor_keys": [],
        "reasons": [],
        "guardrails": [
            "不改变买入或观察动作",
            "不突破现金、预算、集中度与交易限额",
            "不覆盖板块、资金、交易与风险守卫",
        ],
    }


def build_factor_preview(
    factor_scores: Mapping[str, object] | None,
    fund_code: str,
    *,
    mode: str,
    max_adjustment_percent: float,
    sector_name: str = "",
    candidate_pool: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any] | None:
    """Build a fail-closed v2 preview contract without changing a decision."""

    normalized_mode = mode if mode in {"off", "shadow", "enforced"} else "off"
    maximum = max(0.0, min(10.0, float(max_adjustment_percent)))
    if normalized_mode == "off":
        return None
    result = _base_contract(mode=normalized_mode, maximum=maximum)
    if not isinstance(factor_scores, Mapping):
        result["reasons"] = ["本次没有可复算的因子快照"]
        return result

    ic_status = factor_scores.get("ic_status")
    if not isinstance(ic_status, Mapping):
        result["reasons"] = ["因子快照状态缺失"]
        return result
    result.update(
        {
            "model_version": factor_scores.get("model_version"),
            "cohort_mode": ic_status.get("cohort_mode"),
            "snapshot_id": ic_status.get("snapshot_id"),
            "data_as_of": ic_status.get("run_date"),
        }
    )
    if factor_scores.get("available") is not True:
        result["reasons"] = ["本次因子评分不可用"]
        return result
    if (
        str(ic_status.get("state") or "").strip().lower() != "available"
        or ic_status.get("available", True) is False
        or ic_status.get("stale") is True
    ):
        result["reasons"] = ["因子快照不是当前可用状态"]
        return result
    if factor_scores.get("model_version") != PREVIEW_MODEL_VERSION:
        result["reasons"] = ["该试运行仅适用于 current-survivors v2；正式 PIT v3 走独立门禁"]
        return result
    if ic_status.get("cohort_mode") != "current_survivors":
        result["reasons"] = ["样本口径不是 current-survivors v2"]
        return result
    if not ic_status.get("snapshot_id") or not ic_status.get("run_date"):
        result["reasons"] = ["因子快照缺少可审计的版本或数据日期"]
        return result

    code = _normalized_code(fund_code)
    selected_codes = {
        _normalized_code(value) for value in factor_scores.get("selected_fund_codes") or []
    }
    if code not in selected_codes:
        result["reasons"] = ["未进入本次线上因子计算范围"]
        return result
    rows = [row for row in factor_scores.get("holdings") or [] if isinstance(row, Mapping)]
    row = next((item for item in rows if _normalized_code(item.get("fund_code")) == code), None)
    if row is None:
        result["reasons"] = ["本基金缺少可复算的因子明细"]
        return result
    quality_reason = _row_quality_reason(row)
    if quality_reason:
        result["reasons"] = [quality_reason]
        return result

    score, reliable_keys = _row_score(row)
    if score is None:
        result["reasons"] = ["没有达到中等置信的可复算因子"]
        return result
    result.update(
        {
            "status": "eligible",
            "peer_group": row.get("peer_group"),
            "preview_score": score,
            "qualifying_factor_keys": reliable_keys,
            "proposed_adjustment_percent": _adjustment_for_score(score, maximum),
            "reasons": ["当前存续样本仅作受限灰度，含幸存者偏差"],
        }
    )

    sector_by_code = _candidate_sector_by_code(candidate_pool)
    target_sector = _normalized_sector(sector_name) or sector_by_code.get(code, "")
    peer_scores: list[tuple[str, float]] = []
    if target_sector:
        for item in rows:
            item_code = _normalized_code(item.get("fund_code"))
            if item_code not in selected_codes or sector_by_code.get(item_code) != target_sector:
                continue
            if _row_quality_reason(item):
                continue
            item_score, _ = _row_score(item)
            if item_score is not None:
                peer_scores.append((item_code, item_score))
    peer_scores.sort(key=lambda value: (-value[1], value[0]))
    if peer_scores:
        result["sector_rank"] = next(
            index for index, value in enumerate(peer_scores, start=1) if value[0] == code
        )
        result["sector_sample_size"] = len(peer_scores)
        result["rank_scope"] = "current_candidate_pool_sector"
    return result


def apply_factor_preview_amount(
    preview: dict[str, Any] | None,
    *,
    amount_yuan: float,
    hard_cap_yuan: float,
) -> tuple[float, dict[str, Any] | None]:
    if not preview or preview.get("status") != "eligible":
        return amount_yuan, preview
    base = float(amount_yuan)
    hard_cap = max(0.0, float(hard_cap_yuan))
    proposed_percent = _number(preview.get("proposed_adjustment_percent")) or 0.0
    projected = float(floor(base * (1 + proposed_percent / 100.0)))
    projected = max(100.0, min(float(floor(hard_cap)), projected))
    preview["base_amount_yuan"] = base
    preview["projected_amount_yuan"] = projected
    if preview.get("mode") != "enforced":
        preview["application_status"] = "shadow_only"
        preview["adjusted_amount_yuan"] = base
        preview["applied_adjustment_percent"] = 0.0
        return base, preview

    if projected == base:
        preview["application_status"] = "not_applied"
        preview["adjusted_amount_yuan"] = base
        preview["applied_adjustment_percent"] = 0.0
        return base, preview

    preview["application_status"] = "applied"
    preview["adjusted_amount_yuan"] = projected
    preview["applied_adjustment_percent"] = round((projected / base - 1) * 100, 2)
    return projected, preview


def reconcile_factor_preview(
    preview: dict[str, Any] | None,
    *,
    action: str,
    final_amount_yuan: float | None,
) -> dict[str, Any] | None:
    if not preview:
        return None
    if action != "分批买入" or final_amount_yuan is None or final_amount_yuan <= 0:
        preview["application_status"] = "not_applied"
        preview["adjusted_amount_yuan"] = None
        preview["applied_adjustment_percent"] = 0.0
        return preview
    if preview.get("application_status") == "applied":
        preview["adjusted_amount_yuan"] = float(final_amount_yuan)
        base = _number(preview.get("base_amount_yuan"))
        if base and base > 0:
            preview["applied_adjustment_percent"] = round(
                (float(final_amount_yuan) / base - 1) * 100,
                2,
            )
    return preview
