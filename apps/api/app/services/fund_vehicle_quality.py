from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Any

from app.services.fund_tradeability import build_tradeability_gate


VEHICLE_QUALITY_VERSION = "fund_vehicle_quality.2026-07.v1"
ACTIVE_QUALITY_THRESHOLD = 55.0
PASSIVE_QUALITY_THRESHOLD = 60.0


def assess_candidate_vehicle_quality(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Attach an action-quality score that is independent from sector fit.

    Passive funds are evaluated as investment vehicles: an exact tracked-index
    or independently verified primary-sector identity, tradeability, scale, fee
    and tracking quality.  Their absolute sector returns and sector drawdown are
    deliberately excluded.  Active funds retain manager-performance evidence,
    but sector fit remains a separate hard gate instead of being counted twice
    inside the quality threshold.
    """

    row = dict(raw)
    passive = _looks_passive(row)
    if passive:
        score, components, reasons, penalties = _passive_vehicle_score(row)
        method = "passive_index_vehicle"
        threshold = PASSIVE_QUALITY_THRESHOLD
    else:
        score, components, reasons, penalties = _active_vehicle_score(row)
        method = "active_manager_evidence"
        threshold = ACTIVE_QUALITY_THRESHOLD

    gate = row.get("quality_gate") if isinstance(row.get("quality_gate"), Mapping) else {}
    gate_status = str(gate.get("status") or "watch_only")
    verified_sector_identity = str(row.get("sector_match_kind") or "") in {
        "tracking_exact",
        "primary",
    }
    status = "eligible"
    if gate_status == "excluded":
        status = "excluded"
    elif gate_status != "eligible":
        status = "watch_only"
    elif passive and not verified_sector_identity:
        status = "watch_only"
        penalties.append("被动基金尚未核验为目标板块的精确跟踪标的")
    elif score < threshold:
        status = "watch_only"

    peer_group = row.get("peer_group") if isinstance(row.get("peer_group"), Mapping) else {}
    peer_rank = row.get("peer_rank") if isinstance(row.get("peer_rank"), Mapping) else {}
    peer_sample_size = _finite_number(peer_rank.get("peer_sample_size"))
    if peer_sample_size is None:
        peer_sample_size = _finite_number(peer_rank.get("sample_size"))

    assessment = {
        "schema_version": VEHICLE_QUALITY_VERSION,
        "method": method,
        "status": status,
        "score": round(score, 2),
        "threshold": threshold,
        "sector_fit_separate_gate": True,
        "absolute_sector_return_excluded": passive,
        "components": components,
        "reasons": _unique_text(reasons)[:5],
        "penalties": _unique_text(penalties)[:5],
        "peer_context": {
            "group_key": str(peer_group.get("group_key") or "") or None,
            "sample_size": int(peer_sample_size) if peer_sample_size is not None else None,
            "descriptive_only": True,
        },
    }
    row["vehicle_quality_assessment"] = assessment
    row["vehicle_quality_score"] = assessment["score"]
    row["vehicle_quality_status"] = status
    row["vehicle_quality_threshold"] = threshold
    row["vehicle_quality_method"] = method
    row["vehicle_quality_version"] = VEHICLE_QUALITY_VERSION
    return row


def assess_candidate_vehicle_quality_batch(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [assess_candidate_vehicle_quality(row) for row in rows]


def _passive_vehicle_score(
    row: Mapping[str, Any],
) -> tuple[float, dict[str, float], list[str], list[str]]:
    reasons: list[str] = []
    penalties: list[str] = []

    sector_match_kind = str(row.get("sector_match_kind") or "")
    verified_sector_identity = sector_match_kind in {"tracking_exact", "primary"}
    identity_score = 30.0 if verified_sector_identity else 0.0
    if sector_match_kind == "tracking_exact":
        reasons.append("已核验精确跟踪标的")
    elif sector_match_kind == "primary":
        reasons.append("已核验高置信主关联板块")

    tradeability = row.get("tradeability") if isinstance(row.get("tradeability"), Mapping) else None
    trade_status = str(build_tradeability_gate(tradeability).get("status") or "watch_only")
    trade_score = {"eligible": 20.0, "watch_only": 6.0, "excluded": 0.0}.get(
        trade_status,
        6.0,
    )
    if trade_status == "eligible":
        reasons.append("申赎、币种、起点与额度已核验")
    else:
        penalties.append("申赎与额度条件尚未完整通过")

    scale = _finite_number(row.get("fund_scale_yi"))
    if scale is None:
        scale_score = 0.0
        penalties.append("基金规模未核验")
    elif 3.0 <= scale <= 120.0:
        scale_score = 20.0
        reasons.append("基金规模处于稳健区间")
    elif scale > 120.0:
        scale_score = 17.0
    elif scale >= 1.0:
        scale_score = 14.0
    elif scale >= 0.5:
        scale_score = 6.0
        penalties.append("基金规模偏小")
    else:
        scale_score = 0.0
        penalties.append("基金规模过小")

    fee = _percent_number(row.get("management_fee"))
    if fee is None:
        fee_score = 7.0
        penalties.append("管理费率暂未核验，按中性分处理")
    elif fee <= 0.5:
        fee_score = 15.0
        reasons.append("管理费率较低")
    elif fee <= 0.8:
        fee_score = 12.0
    elif fee <= 1.2:
        fee_score = 8.0
    else:
        fee_score = 4.0
        penalties.append("管理费率偏高")

    benchmark = row.get("benchmark_metrics") if isinstance(row.get("benchmark_metrics"), Mapping) else {}
    tracking = benchmark.get("tracking_metrics") if isinstance(benchmark.get("tracking_metrics"), Mapping) else {}
    tracking_available = tracking.get("available") is True
    tracking_error = _finite_number(tracking.get("tracking_error_annualized_percent"))
    tracking_difference = _finite_number(tracking.get("tracking_difference_percent"))
    if tracking_available and tracking_error is not None:
        if tracking_error <= 1.0:
            tracking_score = 15.0
            reasons.append("跟踪误差较低")
        elif tracking_error <= 2.0:
            tracking_score = 12.0
        elif tracking_error <= 4.0:
            tracking_score = 8.0
        else:
            tracking_score = 4.0
            penalties.append("跟踪误差偏高")
        if tracking_difference is not None and tracking_difference < -5.0:
            tracking_score = max(0.0, tracking_score - 3.0)
            penalties.append("相对跟踪标的差异偏弱")
    else:
        tracking_score = 8.0
        penalties.append("跟踪误差尚未形成可用样本，按中性分处理")

    components = {
        "exact_tracking_identity": identity_score,
        "tradeability": trade_score,
        "scale": scale_score,
        "fee": fee_score,
        "tracking_quality": tracking_score,
    }
    return sum(components.values()), components, reasons, penalties


def _active_vehicle_score(
    row: Mapping[str, Any],
) -> tuple[float, dict[str, float], list[str], list[str]]:
    legacy = (
        row.get("quality_score_components")
        if isinstance(row.get("quality_score_components"), Mapping)
        else {}
    )
    components = {
        "manager_performance": _finite_number(legacy.get("performance")) or 0.0,
        "drawdown_control": _finite_number(legacy.get("drawdown_control")) or 0.0,
        "scale": _finite_number(legacy.get("scale")) or 0.0,
        "data_completeness": _finite_number(legacy.get("data_completeness")) or 0.0,
        "type_preference": _finite_number(legacy.get("legacy_type_preference")) or 0.0,
    }
    raw = sum(components.values())
    score = min(100.0, raw / 60.0 * 100.0)
    reasons = ["板块匹配已从基金质量门中独立计算"]
    penalties: list[str] = []
    if components["data_completeness"] < 10.0:
        penalties.append("基金核心资料覆盖不足")
    return score, components, reasons, penalties


def _looks_passive(row: Mapping[str, Any]) -> bool:
    peer_group = row.get("peer_group") if isinstance(row.get("peer_group"), Mapping) else {}
    style = str(peer_group.get("management_style") or "").strip()
    if style in {"passive_index", "enhanced_index"}:
        return True
    name = str(row.get("fund_name") or "").upper()
    fund_type = str(row.get("fund_type") or "").upper()
    return "ETF" in name or "指数" in name or "指数" in fund_type


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _percent_number(value: object) -> float | None:
    if isinstance(value, str):
        match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
        value = match.group(0) if match else None
    return _finite_number(value)


def _unique_text(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))
