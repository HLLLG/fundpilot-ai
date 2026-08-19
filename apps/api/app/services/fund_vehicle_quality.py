from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Any

VEHICLE_QUALITY_VERSION = "fund_vehicle_quality.2026-08.v2"
HOLDING_VEHICLE_QUALITY_VERSION = "holding_vehicle_quality.2026-08.v1"
ACTIVE_QUALITY_THRESHOLD = 55.0
PASSIVE_QUALITY_THRESHOLD = 60.0

# 日报持仓行拿不到荐基候选行的身份分量（`sector_match_kind`），因此被动载体分只由
# 规模 25 + 费率 18.75 + 跟踪质量 18.75 三项组成，满分 62.5，需归一到百分制后才能
# 与 `PASSIVE_QUALITY_THRESHOLD` 比较。详见 `assess_holding_vehicle_quality`。
_HOLDING_PASSIVE_MAX_RAW = 62.5


def assess_candidate_vehicle_quality(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Attach an action-quality score that is independent from sector fit.

    Passive funds are evaluated as investment vehicles: an exact tracked-index
    or independently verified primary-sector identity, scale, recurring fee and
    tracking quality.  Sales-platform availability is deliberately excluded.
    Their absolute sector returns and sector drawdown are
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

    verified_sector_identity = str(row.get("sector_match_kind") or "") in {
        "tracking_exact",
        "primary",
    }
    status = "eligible"
    # Core profile quality and vehicle quality are independent gates. Cascading
    # a missing profile field into this status made the UI claim that both
    # gates failed even when the vehicle scored above its own threshold. The
    # recommendation scope already evaluates both gates separately.
    if passive and not verified_sector_identity:
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


def assess_holding_vehicle_quality(row: Mapping[str, Any]) -> dict[str, Any]:
    """日报持仓行的载体质量判断。**不能**直接用 `assess_candidate_vehicle_quality`。

    直接复用候选版会让**每一只持仓**都落到 `watch_only`，而且两条路径各有各的死因：

    * 主动路径 `_active_vehicle_score` 读的 5 个分量全在 `quality_score_components` 里，
      那个键只由荐基 `discovery_candidate_pool._with_quality_score` 产出，日报持仓行
      从不经过它。缺失分量被 `or 0.0` 兜底 → raw 0 → 归一后 0 分 → 低于阈值 55。
    * 被动路径的 `sector_match_kind` 在日报持仓行上同样不存在，而候选版对 passive 行
      做的是**硬门**：身份未核验就直接改写 `status="watch_only"`，分数再高也没用。

    两者都属于"证据缺失"，不是"载体更差"，按缺失判低分就是把没算出来的东西当成算出来
    的坏结果——与加仓分档里"缺失不等于更弱"同一条纪律。

    因此这里按持仓语义重新划定范围：

    * **被动持仓**给出真实评分，只用日报确有的三项证据——规模、管理费率、以及第一批挂到
      持仓行内的 `benchmark_metrics.tracking_metrics`。刻意**不移植** `sector_match_kind`
      硬门：它在荐基回答的是"这只基金是不是我想买的那个板块的合格工具"，而持仓已经在手上，
      要问的是"它跟住自己的基准了吗、规模够不够、费率合不合理"。基准身份本身另有门槛
      （`benchmark_metrics` 链路自带），不必在这里重复把关。
    * **主动持仓**明确返回 `not_applicable` 而不是低分。经理业绩证据（近 3/6 月收益、成立日、
      基金经理）在日报侧确实没有，而日报对"这只基金自身靠不靠得住"已经有 `evidence.composite`
      这一套量化证据，再造第二个基金质量分只会两处漂移——那正是第一批删掉重复实现要避免的。

    返回的是**评估结果本身**（不是改写后的行），由调用方决定挂到哪个键。
    """
    passive = _looks_passive(row)
    if not passive:
        return {
            "schema_version": HOLDING_VEHICLE_QUALITY_VERSION,
            "applicable": False,
            "status": "not_applicable",
            "method": "active_not_assessable_in_report",
            "score": None,
            "threshold": None,
            "reasons": [],
            "penalties": [],
            "note": (
                "主动管理基金的载体质量需要经理业绩证据（近3/6月收益、成立日、基金经理），"
                "日报数据链路不含这些字段；该基金自身是否靠得住请看 evidence 量化证据，"
                "此处不给分、也不得据此判断其质量偏低。"
            ),
        }

    reasons: list[str] = []
    penalties: list[str] = []
    components: dict[str, float] = {}
    for key, builder in (
        ("scale", _scale_component),
        ("fee", _fee_component),
        ("tracking_quality", _tracking_component),
    ):
        score, component_reasons, component_penalties = builder(row)
        components[key] = score
        reasons.extend(component_reasons)
        penalties.extend(component_penalties)

    raw = sum(components.values())
    normalized = min(100.0, raw / _HOLDING_PASSIVE_MAX_RAW * 100.0)
    status = "eligible" if normalized >= PASSIVE_QUALITY_THRESHOLD else "watch_only"
    return {
        "schema_version": HOLDING_VEHICLE_QUALITY_VERSION,
        "applicable": True,
        "status": status,
        "method": "passive_index_vehicle_without_identity_gate",
        "score": round(normalized, 2),
        "threshold": PASSIVE_QUALITY_THRESHOLD,
        "components": {key: round(value, 2) for key, value in components.items()},
        "sector_identity_gate_excluded": True,
        "reasons": _unique_text(reasons)[:5],
        "penalties": _unique_text(penalties)[:5],
    }


def attach_holding_vehicle_quality(
    holdings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """给每个持仓行挂上 `vehicle_quality`。

    必须在 `benchmark_metrics` 已挂到行内之后调用，否则跟踪质量恒为"样本未形成"中性分。
    """
    rows: list[dict[str, Any]] = []
    for row in holdings:
        if not isinstance(row, Mapping):
            continue
        enriched = dict(row)
        enriched["vehicle_quality"] = assess_holding_vehicle_quality(enriched)
        rows.append(enriched)
    return rows


def _identity_component(
    row: Mapping[str, Any],
) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    sector_match_kind = str(row.get("sector_match_kind") or "")
    verified_sector_identity = sector_match_kind in {"tracking_exact", "primary"}
    identity_score = 37.5 if verified_sector_identity else 0.0
    if sector_match_kind == "tracking_exact":
        reasons.append("已核验精确跟踪标的")
    elif sector_match_kind == "primary":
        reasons.append("已核验高置信主关联板块")
    return identity_score, reasons, []


def _scale_component(row: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    penalties: list[str] = []
    scale = _finite_number(row.get("fund_scale_yi"))
    if scale is None:
        score = 0.0
        penalties.append("基金规模未核验")
    elif 3.0 <= scale <= 120.0:
        score = 25.0
        reasons.append("基金规模处于稳健区间")
    elif scale > 120.0:
        score = 21.25
    elif scale >= 0.5:
        score = 17.5
    else:
        score = 0.0
        penalties.append("基金规模过小")
    return score, reasons, penalties


def _fee_component(row: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    penalties: list[str] = []
    fee = _percent_number(row.get("management_fee"))
    if fee is None:
        score = 8.75
        penalties.append("管理费率暂未核验，按中性分处理")
    elif fee <= 0.5:
        score = 18.75
        reasons.append("管理费率较低")
    elif fee <= 0.8:
        score = 15.0
    elif fee <= 1.2:
        score = 10.0
    else:
        score = 5.0
        penalties.append("管理费率偏高")
    return score, reasons, penalties


def _tracking_component(row: Mapping[str, Any]) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    penalties: list[str] = []
    benchmark = row.get("benchmark_metrics") if isinstance(row.get("benchmark_metrics"), Mapping) else {}
    tracking = benchmark.get("tracking_metrics") if isinstance(benchmark.get("tracking_metrics"), Mapping) else {}
    tracking_available = tracking.get("available") is True
    tracking_error = _finite_number(tracking.get("tracking_error_annualized_percent"))
    tracking_difference = _finite_number(tracking.get("tracking_difference_percent"))
    if tracking_available and tracking_error is not None:
        if tracking_error <= 1.0:
            score = 18.75
            reasons.append("跟踪误差较低")
        elif tracking_error <= 2.0:
            score = 15.0
        elif tracking_error <= 4.0:
            score = 10.0
        else:
            score = 5.0
            penalties.append("跟踪误差偏高")
        if tracking_difference is not None and tracking_difference < -5.0:
            score = max(0.0, score - 3.75)
            penalties.append("相对跟踪标的差异偏弱")
    else:
        score = 10.0
        penalties.append("跟踪误差尚未形成可用样本，按中性分处理")
    return score, reasons, penalties


def _passive_vehicle_score(
    row: Mapping[str, Any],
) -> tuple[float, dict[str, float], list[str], list[str]]:
    reasons: list[str] = []
    penalties: list[str] = []
    components: dict[str, float] = {}
    for key, builder in (
        ("exact_tracking_identity", _identity_component),
        ("scale", _scale_component),
        ("fee", _fee_component),
        ("tracking_quality", _tracking_component),
    ):
        score, component_reasons, component_penalties = builder(row)
        components[key] = score
        reasons.extend(component_reasons)
        penalties.extend(component_penalties)
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
