from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from math import floor, isfinite

from app.config import get_settings
from app.models import (
    DiscoveryEntryTrigger,
    DiscoveryEntryTriggerCondition,
    DiscoveryQuantPreview,
    DiscoveryRecommendation,
    EliminatedCandidate,
    InvestorProfile,
    NewsItem,
    TopicBrief,
)
from app.services.decision_guard_shared import (
    append_unique as _append_unique,
    as_float as _as_float,
    fmt_num as _fmt_num,
    humanize_evidence_text as _humanize_evidence_text,
    normalize_confidence_label as _normalize_confidence,
    pattern_label as _pattern_label,
    resolve_discovery_escalation,
    track_label as _track_label,
)
from app.services.news_citation import _collect_citable_titles, _matches_known_title
from app.services.sector_canonical import get_canonical_sector, get_intraday_canonical_sector
from app.services.sector_labels import normalize_sector_label
from app.services.discovery_sector_context import execution_qualified_fund_codes
from app.services.discovery_strategy import (
    discovery_horizon_label,
    discovery_minimum_holding_days,
    strategy_from_facts,
)
from app.services.fund_tradeability import (
    assess_tradeability_for_amount,
    build_tradeability_gate,
    compact_tradeability_for_llm,
)
from app.services.factor_preview import (
    apply_factor_preview_amount,
    build_factor_preview,
    reconcile_factor_preview,
)


@dataclass(frozen=True)
class AmountCapResult:
    """Deterministic executable ceiling for one discovery recommendation."""

    available: bool
    cap_yuan: float | None
    existing_sector_amount_yuan: float | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuantCoverageExplanation:
    """User-facing reason why a candidate has no executable v3 factor evidence."""

    reason_code: str
    point: str
    validation_note: str


_FINAL_ACTION_PROJECTION_PREFIX = "系统校验后的最终动作："
_FINAL_ACTION_PROJECTION_RE = re.compile(
    r"^系统校验后(?:的)?最终动作(?:调整为)?\s*[：:]?"
)


def _quant_coverage_explanation(
    factor_scores: Mapping[str, object],
    fund_code: str,
) -> QuantCoverageExplanation:
    """Explain the first decisive quant gate without weakening fail-closed rules."""

    status = factor_scores.get("ic_status")
    ic_status = status if isinstance(status, Mapping) else {}
    ic_state = str(ic_status.get("state") or "").strip().lower()
    ic_snapshot_usable = bool(
        ic_state == "available"
        and ic_status.get("stale") is not True
        and ic_status.get("available", True) is not False
    )
    if not ic_snapshot_usable:
        if ic_status.get("stale") is True or ic_state == "stale":
            point = "量化 IC 快照已过期，量化因子未参与本次动作加分。"
            reason_code = "factor_ic_snapshot_stale"
        else:
            point = "量化 IC 快照当前不可用，量化因子未参与本次动作加分。"
            reason_code = "factor_ic_snapshot_unavailable"
        return QuantCoverageExplanation(
            reason_code=reason_code,
            point=point,
            validation_note=(
                "这是系统级量化证据状态，不是该基金的负面信号；"
                "系统未用不可用或过期快照替代严格 v3 证据。"
            ),
        )

    model_version = str(factor_scores.get("model_version") or "").strip()
    cohort_mode = str(ic_status.get("cohort_mode") or "").strip()
    if model_version != "factor_ic.v3" or cohort_mode != "point_in_time":
        return QuantCoverageExplanation(
            reason_code="pit_v3_not_ready",
            point=(
                "PIT v3 量化模型尚未达到可执行条件，当前 v2/非 PIT 因子仅作描述性参考，"
                "未参与本次动作加分。"
            ),
            validation_note=(
                "这是系统级量化证据状态，不是该基金的负面信号；"
                "系统保留严格 v3 门槛，未用 v2/非 PIT 因子替代。"
            ),
        )

    if factor_scores.get("available") is not True:
        return QuantCoverageExplanation(
            reason_code="candidate_factor_payload_unavailable",
            point=(
                "本次候选的同类分类或净值因子输入不可用，未生成可执行 v3 因子分。"
            ),
            validation_note=(
                "候选因子输入不可用只降低置信度，不代表基金本身出现明确负面信号。"
            ),
        )

    selected_codes = {
        str(value or "").strip().zfill(6)
        for value in (factor_scores.get("selected_fund_codes") or [])
        if str(value or "").strip()
    }
    parsed_coverage_limit = _as_float(factor_scores.get("coverage_limit"))
    coverage_limit = (
        int(parsed_coverage_limit)
        if parsed_coverage_limit is not None
        and isfinite(parsed_coverage_limit)
        and parsed_coverage_limit > 0
        else 12
    )
    if selected_codes and fund_code not in selected_codes:
        return QuantCoverageExplanation(
            reason_code="candidate_outside_online_factor_budget",
            point=(
                f"该基金未进入本次前 {coverage_limit} 只线上量化候选，"
                "因此没有生成可执行 v3 因子分。"
            ),
            validation_note=(
                "线上量化候选数量受计算预算限制；未入选不等于基金出现负面量化信号。"
            ),
        )

    target_row: Mapping[str, object] | None = None
    for raw_row in factor_scores.get("holdings") or []:
        if not isinstance(raw_row, Mapping):
            continue
        row_code = str(raw_row.get("fund_code") or "").strip().zfill(6)
        if row_code == fund_code:
            target_row = raw_row
            break
    qualification = (
        target_row.get("execution_qualification")
        if isinstance(target_row, Mapping)
        else None
    )
    reason = (
        str(qualification.get("reason") or "").strip()
        if isinstance(qualification, Mapping)
        else ""
    )
    reason_text = {
        "descriptive_factor_input_not_applicable": (
            "该基金的同类分类或净值因子特征不完整，未形成可执行 v3 因子证据。"
        ),
        "target_factor_feature_not_fresh": (
            "该基金的目标净值因子特征不够新，未形成可执行 v3 因子证据。"
        ),
        "no_statistically_and_economically_qualified_factor": (
            "该基金当前没有因子同时通过统计显著性与扣费后经济显著性门槛，"
            "因此量化模型未加分。"
        ),
        "factor_ic_snapshot_not_current": (
            "该基金评分所用的量化快照不是当前可执行状态，量化模型未加分。"
        ),
    }.get(
        reason,
        "该基金本次没有形成通过严格门槛的可执行 v3 因子证据，量化模型未加分。",
    )
    return QuantCoverageExplanation(
        reason_code=reason or "fund_factor_not_execution_qualified",
        point=reason_text,
        validation_note=(
            "缺少可执行量化加分只降低置信度，不代表基金本身出现明确负面信号。"
        ),
    )


def _normalized_sector_key(value: object) -> str:
    label = normalize_sector_label(str(value or ""))
    canonical = get_intraday_canonical_sector(label) or get_canonical_sector(label)
    if canonical is not None:
        label = normalize_sector_label(canonical.label)
    return label.casefold()


def _finite_nonnegative(value: object) -> float | None:
    parsed = _as_float(value)
    if parsed is None or not isfinite(parsed) or parsed < 0:
        return None
    return float(parsed)


def resolve_discovery_amount_cap(
    *,
    portfolio_truth: Mapping[str, object] | None,
    holdings_slim: list[dict] | None,
    candidate_sector: str,
    allocated_by_sector: Mapping[str, float],
    allocated_total_yuan: float,
    request_budget_yuan: float,
    concentration_limit_percent: float,
    weight_denominator_yuan: float | None,
) -> AmountCapResult:
    """Resolve the hard amount cap without trusting an LLM-proposed amount.

    The cap is the minimum remaining allowance across request budget, confirmed
    cash, the request-level concentration budget, and the portfolio's existing
    plus newly allocated exposure to the same sector. Unknown cash or sector
    exposure is never interpreted as zero.
    """

    reasons: list[str] = []
    sector_key = _normalized_sector_key(candidate_sector)
    if not sector_key or sector_key in {"未分类", "未知", "unknown"}:
        reasons.append("sector_exposure_unknown")

    allocated_total = _finite_nonnegative(allocated_total_yuan)
    request_budget = _finite_nonnegative(request_budget_yuan)
    concentration_limit = _finite_nonnegative(concentration_limit_percent)
    denominator = _finite_nonnegative(weight_denominator_yuan)
    if (
        allocated_total is None
        or request_budget is None
        or concentration_limit is None
        or concentration_limit > 100
    ):
        reasons.append("invalid_amount_input")

    truth = portfolio_truth if isinstance(portfolio_truth, Mapping) else None
    cash_balance: float | None = None
    if truth is None:
        reasons.append("cash_unknown")
    else:
        cash = truth.get("cash")
        if not isinstance(cash, Mapping) or cash.get("known") is not True:
            reasons.append("cash_unknown")
        else:
            cash_balance = _finite_nonnegative(cash.get("balance_yuan"))
            if cash_balance is None:
                reasons.append("invalid_cash_balance")
        if (
            truth.get("position_complete") is not True
            or truth.get("ledger_truncated") is True
            or int(_finite_nonnegative(truth.get("pending_transaction_count")) or 0) > 0
            or int(_finite_nonnegative(truth.get("conflict_count")) or 0) > 0
        ):
            reasons.append("position_truth_incomplete")

    rows = holdings_slim if isinstance(holdings_slim, list) else None
    if rows is None:
        reasons.append("sector_exposure_unknown")
        rows = []
    existing_total = 0.0
    existing_sector_amount = 0.0
    for row in rows:
        if not isinstance(row, dict):
            reasons.append("sector_exposure_unknown")
            continue
        amount = _finite_nonnegative(row.get("holding_amount"))
        if amount is None:
            reasons.append("sector_exposure_unknown")
            continue
        existing_total += amount
        if amount <= 0:
            continue
        holding_sector = _normalized_sector_key(row.get("sector_name"))
        if not holding_sector or holding_sector in {"未分类", "未知", "unknown"}:
            reasons.append("sector_exposure_unknown")
            continue
        if holding_sector == sector_key:
            existing_sector_amount += amount

    normalized_allocations: dict[str, float] = {}
    for raw_sector, raw_amount in allocated_by_sector.items():
        allocation = _finite_nonnegative(raw_amount)
        allocation_sector = _normalized_sector_key(raw_sector)
        if allocation is None or not allocation_sector:
            reasons.append("invalid_amount_input")
            continue
        normalized_allocations[allocation_sector] = (
            normalized_allocations.get(allocation_sector, 0.0) + allocation
        )
    allocated_sector = normalized_allocations.get(sector_key, 0.0)

    if denominator is None:
        reasons.append("invalid_amount_input")
    elif denominator <= 0 and request_budget is not None:
        # A genuinely empty portfolio still needs a denominator for its first
        # allocation. The explicit request budget is the most conservative
        # transaction-scale truth available at this boundary.
        denominator = max(existing_total, request_budget)

    if reasons:
        return AmountCapResult(
            available=False,
            cap_yuan=None,
            existing_sector_amount_yuan=(
                round(existing_sector_amount, 2) if rows is not None else None
            ),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    assert allocated_total is not None
    assert request_budget is not None
    assert concentration_limit is not None
    assert denominator is not None
    assert cash_balance is not None
    limit_ratio = concentration_limit / 100
    remaining_request_budget = max(request_budget - allocated_total, 0.0)
    remaining_cash = max(cash_balance - allocated_total, 0.0)
    remaining_request_sector = max(
        request_budget * limit_ratio - allocated_sector,
        0.0,
    )
    remaining_portfolio_sector = max(
        denominator * limit_ratio - existing_sector_amount - allocated_sector,
        0.0,
    )
    cap = min(
        remaining_request_budget,
        remaining_cash,
        remaining_request_sector,
        remaining_portfolio_sector,
    )
    return AmountCapResult(
        available=True,
        cap_yuan=floor(max(cap, 0.0) * 100) / 100,
        existing_sector_amount_yuan=round(existing_sector_amount, 2),
    )


def _known_portfolio_cash_yuan(discovery_facts: dict | None) -> float | None:
    truth = (discovery_facts or {}).get("portfolio_position_truth")
    if not isinstance(truth, dict):
        return None
    cash = truth.get("cash")
    if not isinstance(cash, dict) or cash.get("known") is not True:
        # Unknown cash is not zero. The amount-cap resolver distinguishes this
        # state and fails closed instead of silently treating the request budget
        # as confirmed cash.
        return None
    value = _as_float(cash.get("balance_yuan"))
    if value is None or not isfinite(value):
        # A row claiming to be known but lacking a usable value is internally
        # inconsistent; fail closed for executable amounts.
        return 0.0
    return max(value, 0.0)


def _numeric_entry_condition(
    *,
    metric: str,
    label: str,
    current_value: float,
    operator: str,
    target_value: float,
    unit: str,
) -> DiscoveryEntryTriggerCondition:
    return DiscoveryEntryTriggerCondition(
        metric=metric,
        label=label,
        current_value=round(float(current_value), 2),
        operator=operator,
        target_value=float(target_value),
        unit=unit,
    )


def _build_opportunity_wait_trigger(
    *,
    sector_move: float | None,
    distance_from_high: float | None,
    recent_5d: float | None,
    recent_20d: float | None,
    pattern: str,
    five_day_flow: float | None,
) -> DiscoveryEntryTrigger | None:
    """Build auditable re-entry targets for an opportunity-first anti-chase wait."""

    price_conditions: list[DiscoveryEntryTriggerCondition] = []
    if sector_move is not None and sector_move >= 7.0:
        price_conditions.append(
            _numeric_entry_condition(
                metric="sector_change_1d_percent",
                label="板块当日涨幅",
                current_value=sector_move,
                operator="lt",
                target_value=7.0,
                unit="%",
            )
        )
    if (
        distance_from_high is not None
        and distance_from_high > -2.0
        and recent_5d is not None
        and recent_5d >= 6.0
    ):
        price_conditions.append(
            _numeric_entry_condition(
                metric="distance_from_high_percent",
                label="距近期高点",
                current_value=distance_from_high,
                operator="lte",
                target_value=-2.0,
                unit="%",
            )
        )
    if (
        recent_20d is not None
        and recent_20d >= 15.0
        and recent_5d is not None
        and recent_5d >= 4.0
    ):
        price_conditions.append(
            _numeric_entry_condition(
                metric="return_20d_percent",
                label="近20日涨幅",
                current_value=recent_20d,
                operator="lt",
                target_value=15.0,
                unit="%",
            )
        )

    flow_is_weak = pattern in {"distribution", "weak_outflow"} or (
        five_day_flow is not None and five_day_flow < 0
    )
    if not price_conditions or not flow_is_weak:
        return None

    conditions = list(price_conditions)
    if five_day_flow is not None and five_day_flow < 0:
        conditions.append(
            _numeric_entry_condition(
                metric="sector_main_force_5d_yi",
                label="板块5日主力",
                current_value=five_day_flow,
                operator="gte",
                target_value=0.0,
                unit="亿元",
            )
        )
    else:
        conditions.append(
            DiscoveryEntryTriggerCondition(
                metric="sector_flow_pattern",
                label="板块资金形态",
                current_text="派发/弱流出",
                target_text="转为中性或净流入",
            )
        )
    return DiscoveryEntryTrigger(
        reason_code="price_extension_with_weak_flow",
        headline="等待价格降温或资金转强",
        release_mode="any",
        conditions=conditions,
    )


def _build_risk_first_wait_trigger(
    *,
    sector_move: float | None,
    return_1y: float | None,
    distance_from_high: float | None,
    chase_threshold: float,
) -> DiscoveryEntryTrigger | None:
    """Build the first deterministic anti-chase target used by risk-first mode."""

    condition: DiscoveryEntryTriggerCondition | None = None
    reason_code = ""
    if sector_move is not None and sector_move >= chase_threshold:
        reason_code = "sector_overheated"
        condition = _numeric_entry_condition(
            metric="sector_change_1d_percent",
            label="板块当日涨幅",
            current_value=sector_move,
            operator="lt",
            target_value=chase_threshold,
            unit="%",
        )
    elif return_1y is not None and return_1y >= 100.0:
        reason_code = "annual_return_extended"
        condition = _numeric_entry_condition(
            metric="return_1y_percent",
            label="近1年涨幅",
            current_value=return_1y,
            operator="lt",
            target_value=100.0,
            unit="%",
        )
    elif distance_from_high is not None and distance_from_high > -5.0:
        reason_code = "near_recent_high"
        condition = _numeric_entry_condition(
            metric="distance_from_high_percent",
            label="距近期高点",
            current_value=distance_from_high,
            operator="lte",
            target_value=-5.0,
            unit="%",
        )
    if condition is None:
        return None
    return DiscoveryEntryTrigger(
        reason_code=reason_code,
        headline="等待短线追高风险下降",
        release_mode="all",
        conditions=[condition],
    )


def apply_discovery_guards(
    recommendations: list[DiscoveryRecommendation],
    *,
    candidate_pool: list[dict],
    held_codes: set[str],
    profile: InvestorProfile,
    budget_yuan: float,
    sector_heat: list[dict],
    discovery_facts: dict | None = None,
    market_news: list[NewsItem] | None = None,
    topic_briefs: list[TopicBrief] | None = None,
    scan_mode: str = "full_market",
) -> tuple[list[DiscoveryRecommendation], list[str], list[EliminatedCandidate]]:
    allowed_codes = {str(item.get("fund_code", "")).zfill(6) for item in candidate_pool}
    pool_by_code = {
        str(item.get("fund_code", "")).zfill(6): item for item in candidate_pool
    }
    heat_by_sector = {
        str(row.get("sector_label", "")): row.get("change_1d_percent")
        for row in sector_heat
    }
    opportunity_by_sector = _sector_opportunities_by_label(discovery_facts or {})
    titles = _collect_citable_titles(market_news or [], topic_briefs or [])
    caveats: list[str] = []
    guarded: list[DiscoveryRecommendation] = []
    eliminated: list[EliminatedCandidate] = []
    seen_recommendation_codes: set[str] = set()
    allocated_amount = 0.0
    allocated_by_sector: dict[str, float] = {}
    parsed_budget = _as_float(budget_yuan)
    requested_budget_yuan = (
        max(parsed_budget, 0.0)
        if parsed_budget is not None and isfinite(parsed_budget)
        else 0.0
    )
    known_cash_yuan = _known_portfolio_cash_yuan(discovery_facts)
    spendable_budget_yuan = (
        min(requested_budget_yuan, known_cash_yuan)
        if known_cash_yuan is not None
        else requested_budget_yuan
    )
    if known_cash_yuan == 0:
        caveats.append("已确认可用现金为 0，本次仅保留观察候选，不生成可执行买入金额。")
    elif known_cash_yuan is not None and known_cash_yuan < requested_budget_yuan:
        caveats.append(
            f"示意买入总额已按已确认可用现金 {known_cash_yuan:.2f} 元封顶。"
        )
    # M6：与日报 analysis_facts.holdings[].escalation 同一思路——把每只候选"是否触发了
    # M4 双向升级判定"的结构化结果记录下来（无论 shadow/enforced 都记录，且不管最终
    # 是否真的生效），供 shadow_escalation_digest.py 聚合复盘读取，避免正则解析 caveats
    # 文本。写回 discovery_facts（按引用传入，最终会随 FundDiscoveryReport.discovery_facts
    # 一并落库），仅在真正传入了 dict 时才写（None 表示调用方本就没打算存 facts）。
    escalation_hints: dict[str, dict] = {}
    portfolio_snapshot = (discovery_facts or {}).get("portfolio_snapshot")
    degraded_portfolio_snapshot = bool(
        isinstance(portfolio_snapshot, dict)
        and (
            portfolio_snapshot.get("stale")
            or not portfolio_snapshot.get("authoritative")
            or portfolio_snapshot.get("position_complete") is False
            or int(portfolio_snapshot.get("pending_transaction_count") or 0) > 0
        )
    )
    from app.services.decision_data_evidence import (
        contains_executable_decision_text,
        decision_evidence_allows_action,
        safe_blocked_points,
    )
    if degraded_portfolio_snapshot:
        caveats.append("持仓快照未达到权威可执行条件，本次已禁止买入动作与示意金额，仅保留观察候选。")
    evidence_blocked_codes: dict[str, list[str]] = {}
    discovery_strategy = strategy_from_facts(discovery_facts)
    opportunity_first = discovery_strategy == "opportunity_first"
    factor_scores = (discovery_facts or {}).get("candidate_factor_scores")
    settings = get_settings()
    factor_preview_mode = getattr(settings, "factor_preview_mode", "shadow")
    factor_preview_max_adjustment = getattr(
        settings,
        "factor_preview_max_adjustment_percent",
        10.0,
    )
    quant_gate_active = isinstance(factor_scores, dict)
    quant_covered_codes = set(
        execution_qualified_fund_codes(factor_scores)
        if isinstance(factor_scores, dict)
        else []
    )
    factor_ic_status = (
        factor_scores.get("ic_status") if isinstance(factor_scores, dict) else {}
    )
    factor_ic_usable = bool(
        isinstance(factor_ic_status, dict)
        and str(factor_ic_status.get("state") or "").strip().lower() == "available"
        and factor_ic_status.get("stale") is not True
        and factor_ic_status.get("available", True) is not False
        and factor_scores.get("available") is True
    )
    if quant_gate_active and not factor_ic_usable:
        quant_covered_codes = set()
    quant_blocked_codes: set[str] = set()
    quant_uncovered_codes: set[str] = set()
    quant_uncovered_reasons: dict[str, str] = {}
    profile_min_holding_days = discovery_minimum_holding_days(
        discovery_strategy,
        profile,
    )

    for rec in recommendations:
        code = rec.fund_code.strip().zfill(6)
        if code in seen_recommendation_codes:
            caveats.append(f"已忽略重复推荐 {code}（{rec.fund_name}），同一基金仅保留首条决策。")
            continue
        seen_recommendation_codes.add(code)
        if code not in allowed_codes:
            caveats.append(f"已剔除池外基金 {code}（{rec.fund_name}）。")
            continue
        if code in held_codes:
            caveats.append(f"已持有 {code}，不作为新买入推荐。")
            continue

        copy = _strip_untrusted_discovery_execution_text(rec)
        pool_item = pool_by_code.get(code, {})
        preview_contract = build_factor_preview(
            factor_scores if isinstance(factor_scores, Mapping) else None,
            code,
            mode=factor_preview_mode,
            max_adjustment_percent=factor_preview_max_adjustment,
            sector_name=copy.sector_name,
            candidate_pool=candidate_pool,
        )
        copy.quant_preview = (
            DiscoveryQuantPreview.model_validate(preview_contract)
            if preview_contract
            else None
        )
        tradeability = (
            pool_item.get("tradeability")
            if isinstance(pool_item.get("tradeability"), Mapping)
            else None
        )
        tradeability_gate = build_tradeability_gate(tradeability)
        # LLM 输出中的同名字段不可信；始终以候选事实快照覆盖。
        copy.tradeability = compact_tradeability_for_llm(tradeability)
        copy.cost_assessment = {}
        quality_gate = (
            pool_item.get("quality_gate")
            if isinstance(pool_item.get("quality_gate"), dict)
            else {}
        )
        raw_quality_status = str(quality_gate.get("status") or "").strip()
        quality_status = (
            raw_quality_status
            if raw_quality_status in {"eligible", "watch_only", "excluded"}
            else "watch_only"
        )
        if quality_status == "excluded":
            reasons = [str(item) for item in quality_gate.get("reasons") or []]
            eliminated.append(
                EliminatedCandidate(
                    fund_code=code,
                    fund_name=str(pool_item.get("fund_name") or copy.fund_name),
                    sector_name=str(pool_item.get("sector_label") or copy.sector_name),
                    reasons=reasons or ["候选质量准入未通过"],
                    basis="候选质量准入未通过，未进入最终推荐。",
                )
            )
            caveats.append(f"已剔除 {code}（{copy.fund_name}）：候选质量准入未通过。")
            continue
        evidence_allowed, evidence_reasons = decision_evidence_allows_action(
            discovery_facts,
            scope="discovery",
            fund_code=code,
        )
        execution_blocked = degraded_portfolio_snapshot or not evidence_allowed
        if execution_blocked:
            evidence_blocked_codes[code] = evidence_reasons
        normalized_action = _normalize_discovery_action(copy.action)
        if normalized_action != copy.action:
            copy.points = [
                f"已将动作「{copy.action}」规范为「{normalized_action}」。",
                *copy.points,
            ]
            copy.action = normalized_action
        copy.confidence = _normalize_confidence(copy.confidence)
        if opportunity_first:
            copy.hold_horizon = discovery_horizon_label(discovery_strategy, profile)
        if quality_status == "watch_only":
            quality_reasons = [
                str(item) for item in quality_gate.get("reasons") or [] if str(item).strip()
            ]
            if raw_quality_status not in {"watch_only", "excluded"}:
                quality_reasons.insert(0, "候选质量门禁缺失或状态不可识别")
            copy.action = "建议关注"
            copy.suggested_amount_yuan = None
            copy.amount_note = "候选质量门禁仅允许研究观察，未生成可执行买入金额"
            if copy.confidence == "高":
                copy.confidence = "中"
            reason_text = "；".join(quality_reasons[:2]) or "候选核心字段或质量条件未达准入线"
            copy.points = [
                f"质量门禁仅允许研究观察：{reason_text}。",
                *copy.points,
            ]
            copy.validation_notes = [
                *copy.validation_notes,
                "候选状态为 watch_only，系统已确定性阻断买入动作与金额。",
            ]
            caveats.append(f"{code} 未通过可执行质量门禁，已降为研究观察。")
        if quant_gate_active and code not in quant_covered_codes:
            quant_explanation = _quant_coverage_explanation(factor_scores, code)
            quant_uncovered_reasons[code] = quant_explanation.reason_code
            if opportunity_first:
                quant_uncovered_codes.add(code)
                if copy.confidence == "高":
                    copy.confidence = "中"
                copy.points = [
                    *copy.points,
                    quant_explanation.point,
                ]
                copy.validation_notes = [
                    *copy.validation_notes,
                    quant_explanation.validation_note,
                ]
            else:
                quant_blocked_codes.add(code)
                if copy.action == "分批买入":
                    copy.action = "建议关注"
                copy.suggested_amount_yuan = None
                copy.amount_note = "该候选未进入当前量化覆盖集合，未生成可执行金额"
                copy.confidence = "低"
                copy.points = [
                    quant_explanation.point,
                    "稳健筛选要求可执行 v3 量化证据，系统因此仅保留观察。",
                    *copy.points,
                ]
                copy.validation_notes = [
                    *copy.validation_notes,
                    quant_explanation.validation_note,
                ]
        if execution_blocked:
            if copy.action == "分批买入":
                copy.action = "建议关注"
            copy.suggested_amount_yuan = None
            copy.amount_note = "持仓快照过期，未生成可执行金额"
            copy.confidence = "低"
            copy.validation_notes = [
                *copy.validation_notes,
                "持仓快照过期或尚未服务端确认；组合缺口、集中度与预算只可作背景，不具备买入执行条件。",
            ]
        if pool_item:
            corrected = _align_candidate_identity(copy, pool_item)
            if corrected:
                caveats.append(f"已按候选池校正基金名称/板块：{code}。")
        opportunity = opportunity_by_sector.get(copy.sector_name)
        sector_move = heat_by_sector.get(copy.sector_name)
        nav_trend = pool_item.get("nav_trend") or {}
        if not isinstance(nav_trend, Mapping):
            nav_trend = {}
        dist_high = _as_float(nav_trend.get("distance_from_high_percent"))
        recent_5d = _as_float(nav_trend.get("recent_5d_change_percent"))
        recent_20d = _as_float(nav_trend.get("return_20d_percent"))
        if profile.avoid_chasing and copy.action in {"分批买入", "等待回调"}:
            if opportunity_first:
                pattern = str((opportunity or {}).get("pattern_label") or "")
                five_day_flow = _as_float(
                    (opportunity or {}).get("cumulative_5d_net_yi")
                )
                wait_trigger = _build_opportunity_wait_trigger(
                    sector_move=sector_move,
                    distance_from_high=dist_high,
                    recent_5d=recent_5d,
                    recent_20d=recent_20d,
                    pattern=pattern,
                    five_day_flow=five_day_flow,
                )
                if wait_trigger is not None:
                    copy.action = "等待回调"
                    copy.entry_trigger = wait_trigger
                    copy.points = list(copy.points) + [
                        "短线涨幅已经偏快且5日资金没有继续确认，先等回调或资金重新转强。"
                    ]
            else:
                chase_threshold = 6.0 if profile.decision_style == "aggressive" else 4.0
                r1y = _as_float(pool_item.get("return_1y_percent"))
                wait_trigger = _build_risk_first_wait_trigger(
                    sector_move=sector_move,
                    return_1y=r1y,
                    distance_from_high=dist_high,
                    chase_threshold=chase_threshold,
                )
                if wait_trigger is not None:
                    copy.action = "等待回调"
                    copy.entry_trigger = wait_trigger
                    if wait_trigger.reason_code == "sector_overheated":
                        copy.points = list(copy.points) + [
                            f"板块当日 {float(sector_move):+.2f}% 偏热，拒绝追高模式下建议等待回调。"
                        ]
                    elif wait_trigger.reason_code == "annual_return_extended":
                        copy.points = list(copy.points) + [
                            f"近1年涨幅 {float(r1y):+.1f}% 偏高，拒绝追高模式下建议等待回调。"
                        ]
                    else:
                        copy.points = list(copy.points) + [
                            f"净值距区间高点仅 {float(dist_high):+.1f}%，短线追高风险偏高。"
                        ]

        drawdown = _as_float(pool_item.get("max_drawdown_1y_percent"))
        drawdown_limit = _profile_drawdown_limit(profile)
        if drawdown is not None and abs(drawdown) > drawdown_limit:
            if opportunity_first:
                copy.points = _remove_legacy_drawdown_profile_comparisons(copy.points)
                copy.risks = _remove_legacy_drawdown_profile_comparisons(copy.risks)
                copy.fund_evidence = _remove_legacy_drawdown_profile_comparisons(
                    copy.fund_evidence
                )
                copy.validation_notes = _remove_legacy_drawdown_profile_comparisons(
                    copy.validation_notes
                )
                horizon_context = _horizon_drawdown_context(nav_trend)
                copy.risks = [
                    (
                        f"历史波动偏高：近1年最大回撤 {drawdown:.2f}%"
                        f"{horizon_context}；这不会单独否决当前机会，但会压低首批仓位。"
                    ),
                    *copy.risks,
                ]
                copy.validation_notes = [
                    *copy.validation_notes,
                    "账户亏损复核线与候选历史回撤已分开判断；历史回撤只参与风险提示和仓位缩放。",
                ]
            elif copy.action == "分批买入":
                copy.action = "建议关注"
                copy.points = [
                    f"近1年最大回撤 {drawdown:.2f}% 超过当前风格的候选准入线 {drawdown_limit:.1f}%，仅保留观察。",
                    *copy.points,
                ]
                copy.validation_notes = [
                    *copy.validation_notes,
                    "候选历史回撤与当前投资风格不匹配，系统已阻断买入动作。",
                ]

        # M4 双向 guard：与日报 resolve_escalation_floor 同一套"量价背离显著"入口，
        # 但荐基语义不同——负向共振时整条剔除候选池（而非降级动作文字），正向共振时
        # 允许突破常规预算上限的软约束（而非日报的仓位百分比）。两个方向都要求板块
        # 与基金质量分同时印证，只命中一个维度时交由既有的弱证据降级/常规金额上限处理。
        escalation = resolve_discovery_escalation(
            sector_opportunity=opportunity,
            pool_item=pool_item,
        )
        if escalation.get("action"):
            escalation_hints[code] = escalation
        # M6：灰度开关——shadow 模式下不真正剔除/提额，只标注"若切换 enforced 会怎样"，
        # 供 shadow_escalation_digest.py 聚合复盘（与日报 recommendation_guard.py 的
        # 灰度处理同一套开关、同一种"仅提示不生效"的语义）。
        enforced = get_settings().decision_escalation_mode == "enforced"
        if escalation.get("action") == "exclude" and not execution_blocked:
            basis = str(escalation.get("basis") or "")
            if enforced:
                caveats.append(f"已从候选池剔除 {code}（{copy.fund_name}）：{basis}。")
                eliminated.append(
                    EliminatedCandidate(
                        fund_code=code,
                        fund_name=copy.fund_name,
                        sector_name=copy.sector_name,
                        reasons=list(escalation.get("reasons") or []),
                        basis=basis,
                    )
                )
                continue
            copy.validation_notes = [
                *copy.validation_notes,
                f"【灰度提示，未生效】若启用新版守卫（enforced 模式），"
                f"{code}（{copy.fund_name}）会被系统从候选池剔除：{basis}。",
            ]

        weak_evidence_reasons = (
            _weak_evidence_reasons(pool_item, opportunity)
            if copy.action == "分批买入"
            else []
        )
        if weak_evidence_reasons:
            previous = copy.action
            copy.action = "建议关注"
            reason_text = "；".join(weak_evidence_reasons)
            note = (
                f"未达到买入证据门槛：{reason_text}。"
                "系统已将动作从「分批买入」降为「建议关注」。"
            )
            copy.points = [note, *copy.points]
            copy.validation_notes = [
                *copy.validation_notes,
                f"动作降级触发项：{reason_text}。",
            ]
            caveats.append(
                f"{code} 未达到买入证据门槛（{reason_text}），"
                f"已将动作从「{previous}」降为「建议关注」。"
            )

        if (
            escalation.get("action") == "boost"
            and not execution_blocked
            and quality_status == "eligible"
        ):
            basis = str(escalation.get("basis") or "")
            if enforced:
                copy.points = [
                    f"量价背离与基金质量共振积极，仅形成软建议提额信号，"
                    f"但仍受现金、预算和集中度硬上限约束（{basis}）。",
                    *copy.points,
                ]
                caveats.append(
                    f"{code} 证据强烈支持该方向，形成软建议金额提额信号；硬上限保持不变。"
                )
            else:
                copy.validation_notes = [
                    *copy.validation_notes,
                    f"【灰度提示，未生效】若启用新版守卫（enforced 模式），"
                    f"{code} 的建议买入金额上限会被系统提高：{basis}。",
                ]

        if copy.action != "分批买入":
            copy.suggested_amount_yuan = None
            copy.amount_note = (
                "当前为观察或等待条件，未生成可执行买入金额。"
            )

        amount = _as_float(copy.suggested_amount_yuan)
        if copy.suggested_amount_yuan is not None and (
            amount is None or not isfinite(amount) or amount <= 0
        ):
            copy.action = "建议关注"
            copy.suggested_amount_yuan = None
            copy.amount_note = "建议金额不是有效正数，系统已阻断买入并降为研究观察。"
            copy.points = ["建议金额校验未通过，未保留可执行买入动作。", *copy.points]
            copy.validation_notes = [
                *copy.validation_notes,
                "建议金额必须为有限正数；异常金额已被确定性守卫清除。",
            ]
        elif amount is not None:
            copy.suggested_amount_yuan = amount

        if copy.suggested_amount_yuan is not None and spendable_budget_yuan <= 0:
            copy.suggested_amount_yuan = None
            copy.amount_note = (
                "已确认可执行预算或可用现金为 0，本次未生成买入金额。"
            )
        if copy.suggested_amount_yuan is not None and spendable_budget_yuan > 0:
            portfolio_gap = (discovery_facts or {}).get("portfolio_gap")
            if not isinstance(portfolio_gap, dict):
                portfolio_gap = {}
            portfolio_truth = (discovery_facts or {}).get("portfolio_position_truth")
            if not isinstance(portfolio_truth, Mapping):
                portfolio_truth = None
            holdings_slim = portfolio_gap.get("holdings_slim")
            if not isinstance(holdings_slim, list):
                positions = (
                    portfolio_truth.get("positions")
                    if isinstance(portfolio_truth, Mapping)
                    else None
                )
                holdings_slim = [] if isinstance(positions, list) and not positions else None
            denominator = _as_float(portfolio_gap.get("weight_denominator_yuan"))
            if denominator is None:
                denominator = _as_float(profile.expected_investment_amount)
            if denominator is None:
                denominator = _as_float(portfolio_gap.get("total_amount"))
            if denominator is None:
                denominator = 0.0
            cap = resolve_discovery_amount_cap(
                portfolio_truth=portfolio_truth,
                holdings_slim=holdings_slim,
                candidate_sector=copy.sector_name,
                allocated_by_sector=allocated_by_sector,
                allocated_total_yuan=allocated_amount,
                request_budget_yuan=requested_budget_yuan,
                concentration_limit_percent=profile.concentration_limit_percent,
                weight_denominator_yuan=denominator,
            )
            if not cap.available:
                copy.suggested_amount_yuan = None
                copy.amount_note = _join_amount_note(
                    copy.amount_note,
                    "现金或同板块敞口无法完整核验，系统已清除可执行金额",
                )
                copy.validation_notes = [
                    *copy.validation_notes,
                    "金额硬上限缺少可核验的现金、仓位或板块敞口；未知值未按 0 处理。",
                ]
                caveats.append(f"{code} 金额硬上限无法完整核验，已阻断可执行金额。")
            else:
                hard_cap = float(cap.cap_yuan or 0.0)
                if hard_cap < 100:
                    copy.suggested_amount_yuan = None
                    copy.amount_note = _join_amount_note(
                        copy.amount_note,
                        (
                            "现金、总预算或同板块集中度剩余额度低于 100 元，"
                            "未达到最小示意执行额"
                        ),
                    )
                    caveats.append(
                        f"{code} 的确定性硬上限低于最小示意执行额，已清除金额。"
                    )
                elif copy.suggested_amount_yuan > hard_cap:
                    adjusted = float(floor(hard_cap))
                    copy.suggested_amount_yuan = adjusted
                    copy.amount_note = _join_amount_note(
                        copy.amount_note,
                        (
                            "示意金额已按现金、总预算及已有/本轮同板块"
                            f"集中度硬上限压缩至约 {adjusted:.0f} 元"
                        ),
                    )
                    caveats.append(
                        f"{code} 示意金额已按现金、总预算或同板块集中度硬上限压缩。"
                    )
                if copy.suggested_amount_yuan is not None:
                    trade_limit = _as_float(
                        tradeability_gate.get("max_purchase_yuan")
                    )
                    if (
                        trade_limit is not None
                        and isfinite(trade_limit)
                        and trade_limit >= 0
                        and copy.suggested_amount_yuan > trade_limit
                    ):
                        adjusted = float(floor(trade_limit))
                        copy.suggested_amount_yuan = adjusted if adjusted > 0 else None
                        copy.amount_note = _join_amount_note(
                            copy.amount_note,
                            f"示意金额已按该份额单日申购限额压缩至约 {adjusted:.0f} 元",
                        )
                        caveats.append(f"{code} 示意金额已按份额单日申购限额压缩。")

                if copy.suggested_amount_yuan is not None:
                    effective_cap = hard_cap
                    trade_limit = _as_float(tradeability_gate.get("max_purchase_yuan"))
                    if trade_limit is not None and isfinite(trade_limit) and trade_limit >= 0:
                        effective_cap = min(effective_cap, trade_limit)
                    preview_amount, preview_contract = apply_factor_preview_amount(
                        copy.quant_preview.model_dump() if copy.quant_preview else None,
                        amount_yuan=float(copy.suggested_amount_yuan),
                        hard_cap_yuan=effective_cap,
                    )
                    copy.quant_preview = (
                        DiscoveryQuantPreview.model_validate(preview_contract)
                        if preview_contract
                        else None
                    )
                    copy.suggested_amount_yuan = preview_amount
                    if (
                        preview_contract
                        and preview_contract.get("application_status") == "applied"
                        and preview_contract.get("applied_adjustment_percent")
                    ):
                        copy.amount_note = _join_amount_note(
                            copy.amount_note,
                            "量化试运行仅在硬上限内修正首批金额",
                        )

                if copy.suggested_amount_yuan is not None:
                    assessment = assess_tradeability_for_amount(
                        tradeability,
                        amount_yuan=copy.suggested_amount_yuan,
                        hold_horizon=(
                            f"荐基策略最短持有期 {profile_min_holding_days} 天"
                            if profile_min_holding_days is not None
                            else discovery_horizon_label(discovery_strategy, profile)
                        ),
                        minimum_holding_days=profile_min_holding_days,
                    )
                    copy.cost_assessment = assessment
                    if assessment.get("executable") is not True:
                        notes = [
                            str(item)
                            for item in assessment.get("notes") or []
                            if str(item).strip()
                        ]
                        copy.action = "建议关注"
                        copy.suggested_amount_yuan = None
                        copy.amount_note = _join_amount_note(
                            copy.amount_note,
                            "份额可交易性或持有期费用门禁未通过，已清除可执行金额",
                        )
                        copy.validation_notes = [
                            *copy.validation_notes,
                            *(notes[:3] or ["份额可交易性门禁未通过"]),
                        ]
                        caveats.append(
                            f"{code} 未通过申购状态、限额、购买起点或持有期费用门禁，已降为研究观察。"
                        )
                    else:
                        total_cost = _as_float(
                            assessment.get("estimated_total_cost_upper_bound_percent")
                        )
                        if total_cost is not None:
                            copy.amount_note = _join_amount_note(
                                copy.amount_note,
                                f"按未折扣标准费率估算的最低持有期成本上限约 {total_cost:.2f}%",
                            )
                        if assessment.get("fee_status") == "execution_verification_required":
                            copy.validation_notes = [
                                *copy.validation_notes,
                                "销售平台实际申购/赎回费仍须下单前核验，当前未宣称成本最优。",
                            ]

                if copy.suggested_amount_yuan is not None:
                    final_allocated = float(copy.suggested_amount_yuan)
                    allocated_amount += final_allocated
                    sector_key = _normalized_sector_key(copy.sector_name)
                    allocated_by_sector[sector_key] = (
                        allocated_by_sector.get(sector_key, 0.0) + final_allocated
                    )

        if copy.action == "分批买入":
            final_amount = _as_float(copy.suggested_amount_yuan)
            if final_amount is None or not isfinite(final_amount) or final_amount <= 0:
                copy.action = "建议关注"
                copy.suggested_amount_yuan = None
                copy.amount_note = _join_amount_note(
                    copy.amount_note,
                    "预算或金额未达到可执行条件，动作已降为研究观察",
                )
                copy.points = [
                    "当前没有可执行的有限正数金额，系统未保留买入指令。",
                    *copy.points,
                ]

        if copy.action == "等待回调" and not (
            copy.entry_trigger and copy.entry_trigger.conditions
        ):
            copy.action = "建议关注"
            copy.entry_trigger = None
            copy.points = [
                "等待动作缺少可验证的价格或资金触发值，系统已降为研究观察。",
                *copy.points,
            ]
            copy.validation_notes = [
                *copy.validation_notes,
                "条件动作必须由服务端确定性规则生成具体触发值；模型自由描述不构成等待条件。",
            ]
        elif copy.action != "等待回调":
            copy.entry_trigger = None

        if pool_item:
            _backfill_decision_fields(
                copy,
                pool_item,
                opportunity,
            )
        _sync_decision_path_with_final_action(copy)
        if execution_blocked:
            copy.action = "建议关注"
            copy.suggested_amount_yuan = None
            copy.amount_note = "字段级证据未达到时点可用条件，未生成可执行金额"
            copy.confidence = "低"
            copy.points = safe_blocked_points(
                copy.points,
                fallback="字段级证据未达到可执行条件，本条仅保留观察候选。",
            )
            copy.decision_path = "证据时点校验未通过，系统阻断买入动作并降为建议关注。"
            copy.sector_evidence = [
                value for value in copy.sector_evidence if not contains_executable_decision_text(value)
            ]
            copy.fund_evidence = [
                value for value in copy.fund_evidence if not contains_executable_decision_text(value)
            ]
            copy.validation_notes = [
                value for value in copy.validation_notes if not contains_executable_decision_text(value)
            ] + ["字段级证据时点校验未通过，买入动作与金额已被确定性阻断。"]
        if copy.action != "等待回调":
            copy.entry_trigger = None
        _enforce_discovery_execution_projection(copy)
        reconciled_preview = reconcile_factor_preview(
            copy.quant_preview.model_dump() if copy.quant_preview else None,
            action=copy.action,
            final_amount_yuan=_as_float(copy.suggested_amount_yuan),
        )
        copy.quant_preview = (
            DiscoveryQuantPreview.model_validate(reconciled_preview)
            if reconciled_preview
            else None
        )
        copy.news_bullish = _filter_news_titles(copy.news_bullish, titles)
        _humanize_recommendation_text(copy)
        guarded.append(copy)

    if discovery_facts is not None:
        discovery_facts["escalation_hints"] = escalation_hints
        discovery_facts["decision_escalation_mode"] = get_settings().decision_escalation_mode
        discovery_facts["factor_preview_mode"] = factor_preview_mode
        discovery_facts["data_evidence_guard"] = {
            "execution_blocked": bool(evidence_blocked_codes),
            "blocked_fund_codes": sorted(evidence_blocked_codes),
            "reasons_by_fund": evidence_blocked_codes,
            "quant_evidence_blocked_fund_codes": sorted(quant_blocked_codes),
            "quant_evidence_uncovered_fund_codes": sorted(quant_uncovered_codes),
            "quant_evidence_uncovered_reasons_by_fund": dict(
                sorted(quant_uncovered_reasons.items())
            ),
        }
    if evidence_blocked_codes and not degraded_portfolio_snapshot:
        caveats.append("部分候选的字段级证据时点不可用，已降为观察并清除买入金额。")
    if quant_blocked_codes:
        caveats.append("部分候选未进入当前量化覆盖集合，已降为观察并清除买入金额。")
    if quant_uncovered_codes:
        if "pit_v3_not_ready" in quant_uncovered_reasons.values():
            caveats.append(
                "PIT v3 量化模型尚未达到可执行条件，当前因子仅作描述性参考；"
                "系统已降低置信度，但未把系统级证据状态误判为基金负面信号。"
            )
        else:
            caveats.append(
                "部分候选暂无可执行 v3 量化因子加分；具体原因已逐只说明，"
                "系统未把证据不足误判为负面信号。"
            )

    return guarded[:5], caveats, eliminated


def _normalize_discovery_action(action: str) -> str:
    text = str(action or "").strip()
    if re.search(r"(?:不|勿|莫|禁止|避免|停止|暂停|暂缓|暂不|不宜|不适合|无需|无须).{0,4}(?:买入|加仓)", text):
        return "建议关注"
    if any(
        token in text
        for token in (
            "不建议买入",
            "不建议加仓",
            "暂不买入",
            "暂不加仓",
            "不买入",
            "不加仓",
            "停止买入",
            "停止加仓",
            "避免买入",
            "禁止买入",
        )
    ):
        return "建议关注"
    if any(token in text for token in ("回调", "暂停", "追高", "等一等", "观望")):
        return "等待回调"
    if any(token in text for token in ("分批", "买入", "加仓", "少量", "定投", "试探")):
        return "分批买入"
    return "建议关注"


def finalize_discovery_allocation_projection(
    recommendation: DiscoveryRecommendation,
) -> DiscoveryRecommendation:
    """Re-synchronize user-facing execution fields after server allocation."""

    _sync_decision_path_with_final_action(recommendation)
    _enforce_discovery_execution_projection(recommendation)
    return recommendation


def _strip_untrusted_discovery_execution_text(
    rec: DiscoveryRecommendation,
) -> DiscoveryRecommendation:
    from app.services.decision_data_evidence import (
        contains_high_risk_trade_instruction_text,
        contains_trade_instruction_text,
    )

    copy = rec.model_copy(deep=True)
    copy.points = [
        value for value in copy.points if not contains_trade_instruction_text(value)
    ]
    copy.sector_evidence = [
        value
        for value in copy.sector_evidence
        if not contains_trade_instruction_text(value)
    ]
    copy.fund_evidence = [
        value
        for value in copy.fund_evidence
        if not contains_trade_instruction_text(value)
    ]
    copy.validation_notes = [
        value
        for value in copy.validation_notes
        if not contains_trade_instruction_text(value)
    ]
    copy.risks = [
        value for value in copy.risks if not contains_trade_instruction_text(value)
    ]
    if contains_high_risk_trade_instruction_text(copy.decision_path):
        copy.decision_path = ""
    copy.amount_note = None
    copy.entry_trigger = None
    copy.quant_preview = None
    copy.suggested_position_change_percent = None
    copy.suggested_position_change_basis = ""
    return copy


def _enforce_discovery_execution_projection(rec: DiscoveryRecommendation) -> None:
    executable = rec.action == "分批买入"
    amount = _as_float(rec.suggested_amount_yuan)
    if not executable or amount is None or not isfinite(amount) or amount <= 0:
        rec.suggested_amount_yuan = None
        rec.amount_note = "最终动作非买入或金额未通过校验，系统未生成可执行金额。"
        rec.suggested_position_change_percent = None
        rec.suggested_position_change_basis = ""
    else:
        rec.suggested_amount_yuan = float(amount)
        rec.amount_note = (
            f"系统校验后的示意买入金额约 {float(amount):,.0f} 元；"
            "不得突破已确认现金、请求预算与同板块集中度硬上限。"
        )
        position = rec.suggested_position_change_percent
        if position is not None and (
            not isfinite(float(position)) or float(position) <= 0
        ):
            rec.suggested_position_change_percent = None
            rec.suggested_position_change_basis = ""
        elif position is not None:
            rec.suggested_position_change_basis = (
                "系统依据最终买入动作与确定性规则计算，非模型自由给值"
            )

    projection = f"{_FINAL_ACTION_PROJECTION_PREFIX}{rec.action}。"
    if rec.suggested_amount_yuan is not None:
        projection += f"示意金额约 {float(rec.suggested_amount_yuan):,.0f} 元。"
    business_points: list[str] = []
    seen_points: set[str] = set()
    for point in rec.points:
        text = str(point).strip()
        if not text or _FINAL_ACTION_PROJECTION_RE.match(text):
            continue
        normalized = re.sub(r"\s+", " ", text)
        if normalized in seen_points:
            continue
        seen_points.add(normalized)
        business_points.append(text)
    rec.points = [*business_points, projection]
    if not rec.decision_path:
        rec.decision_path = f"确定性守卫完成候选、证据与预算校验；最终动作：{rec.action}。"


def _profile_drawdown_limit(profile: InvestorProfile) -> float:
    base = max(float(profile.max_drawdown_percent or 0.0), 0.0)
    if profile.decision_style == "aggressive":
        return max(40.0, base * 3.0)
    return max(20.0, base * 2.0)


def _remove_legacy_drawdown_profile_comparisons(values: list[str]) -> list[str]:
    mismatch_markers = (
        "风险偏好",
        "风险承受",
        "心理压力",
        "严重不符",
        "不匹配",
        "复核线",
        "准入线",
    )
    return [
        str(value)
        for value in values
        if not (
            "最大回撤" in str(value)
            and any(marker in str(value) for marker in mismatch_markers)
        )
    ]


def _horizon_drawdown_context(nav_trend: Mapping[str, object]) -> str:
    parts: list[str] = []
    for days in (20, 60):
        value = _as_float(nav_trend.get(f"max_drawdown_{days}d_percent"))
        if value is not None:
            parts.append(f"近{days}日最大回撤 {value:.2f}%")
    return "，" + "、".join(parts) if parts else ""


def _should_downgrade_weak_evidence(
    rec: DiscoveryRecommendation,
    pool_item: dict,
    opportunity: dict | None,
) -> bool:
    if rec.action != "分批买入":
        return False
    weak_reasons = _weak_evidence_reasons(pool_item, opportunity)
    return bool(weak_reasons)


def _weak_evidence_reasons(pool_item: dict, opportunity: dict | None) -> list[str]:
    reasons: list[str] = []
    if opportunity:
        confidence = str(opportunity.get("confidence") or "").strip()
        if confidence in {"低", "不足"}:
            reasons.append(f"主方向置信度为{confidence}")
        score = _as_float(opportunity.get("score"))
        if score is not None and score < 60:
            reasons.append(f"板块机会分 {score:.2f}，低于 60")
        pattern = str(opportunity.get("pattern_label") or "")
        if pattern in {"flow_date_mismatch", "distribution", "weak_outflow"}:
            pattern_labels = {
                "flow_date_mismatch": "资金与价格时点未对齐",
                "distribution": "资金流呈高位分化",
                "weak_outflow": "资金流偏弱",
            }
            reasons.append(pattern_labels[pattern])
        five_day_flow = _as_float(opportunity.get("cumulative_5d_net_yi"))
        if five_day_flow is not None and five_day_flow < 0:
            reasons.append(f"近5日主力净流出 {abs(five_day_flow):.2f} 亿元")
    quality = _as_float(pool_item.get("fund_quality_score"))
    if quality is not None and quality < 55:
        reasons.append(f"基金质量分 {quality:.2f}，低于 55")
    fit = _as_float(pool_item.get("sector_fit_score"))
    if fit is not None and fit < 18:
        reasons.append(f"板块匹配分 {fit:.2f}，低于 18")
    penalties = " ".join(str(item) for item in pool_item.get("quality_penalties") or [])
    if "匹配置信偏低" in penalties or "板块匹配" in penalties:
        reasons.append("板块匹配置信偏低")
    return _append_unique([], reasons, limit=6)


def _sync_decision_path_with_final_action(rec: DiscoveryRecommendation) -> None:
    if not rec.decision_path:
        return
    action = rec.action
    if action in rec.decision_path and not _contains_conflicting_action(rec.decision_path, action):
        return
    if "动作" not in rec.decision_path and not _contains_conflicting_action(rec.decision_path, action):
        return
    text = _strip_conflicting_action_clause(rec.decision_path, action)
    text = text.rstrip("。；;，, ")
    rec.decision_path = f"{text}。系统校验后最终动作调整为{action}。"


def _contains_conflicting_action(text: str, final_action: str) -> bool:
    for candidate in ("分批买入", "建议关注", "等待回调", "少量买入"):
        if candidate != final_action and candidate in text:
            return True
    return False


def _strip_conflicting_action_clause(text: str, final_action: str) -> str:
    result = text
    action_terms = ("分批买入", "建议关注", "等待回调", "少量买入")
    for candidate in action_terms:
        if candidate == final_action:
            continue
        result = re.sub(rf"，?最后决定[^。；;]*{re.escape(candidate)}[^。；;]*[。；;]?", "", result)
        result = re.sub(rf"，?动作[^。；;]*{re.escape(candidate)}[^。；;]*[。；;]?", "", result)
    return result


def _join_amount_note(existing: str | None, addition: str) -> str:
    if existing:
        return f"{existing}；{addition}"
    return addition


def _sector_opportunities_by_label(facts: dict) -> dict[str, dict]:
    items = facts.get("sector_opportunities") or []
    result: dict[str, dict] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("sector_label") or "").strip()
        if label:
            result[label] = item
    return result


def _align_candidate_identity(rec: DiscoveryRecommendation, pool_item: dict) -> bool:
    expected_name = str(pool_item.get("fund_name") or "").strip()
    expected_sector = str(
        pool_item.get("sector_label") or pool_item.get("sector_name") or ""
    ).strip()
    corrected = False
    if expected_name and rec.fund_name != expected_name:
        rec.fund_name = expected_name
        corrected = True
    if expected_sector and rec.sector_name != expected_sector:
        rec.sector_name = expected_sector
        corrected = True
    return corrected


def _backfill_decision_fields(
    rec: DiscoveryRecommendation,
    pool_item: dict,
    opportunity: dict | None,
) -> None:
    if not rec.decision_path:
        rec.decision_path = _build_decision_path(rec, pool_item, opportunity)
    if not rec.sector_evidence:
        rec.sector_evidence = _append_unique([], _build_sector_evidence(opportunity), limit=4)
    if not rec.fund_evidence:
        rec.fund_evidence = _append_unique([], _build_fund_evidence(pool_item), limit=4)
    if not rec.validation_notes:
        rec.validation_notes = _append_unique(
            [],
            _build_validation_notes(pool_item, opportunity),
            limit=4,
        )


def _humanize_recommendation_text(rec: DiscoveryRecommendation) -> None:
    rec.decision_path = _humanize_evidence_text(rec.decision_path)
    rec.amount_note = _humanize_evidence_text(rec.amount_note) if rec.amount_note else rec.amount_note
    rec.sector_evidence = [_humanize_evidence_text(item) for item in rec.sector_evidence]
    rec.fund_evidence = [_humanize_evidence_text(item) for item in rec.fund_evidence]
    rec.validation_notes = [_humanize_evidence_text(item) for item in rec.validation_notes]
    rec.points = [_humanize_evidence_text(item) for item in rec.points]
    rec.risks = [_humanize_evidence_text(item) for item in rec.risks]


def _build_decision_path(
    rec: DiscoveryRecommendation,
    pool_item: dict,
    opportunity: dict | None,
) -> str:
    sector = rec.sector_name or str(pool_item.get("sector_label") or "")
    quality = pool_item.get("fund_quality_score")
    fit = pool_item.get("sector_fit_score")
    if opportunity:
        track = opportunity.get("track") or "unknown"
        score = opportunity.get("score")
        if quality is not None and fit is not None:
            return (
                f"先判断板块方向：{sector}（{_track_label(track)}，机会分 {_fmt_num(score)}），"
                f"再在该方向内选择基金质量分 {_fmt_num(quality)}、"
                f"板块匹配分 {_fmt_num(fit)} 的候选基金，动作定为{rec.action}。"
            )
        return (
            f"先判断板块方向：{sector}（{_track_label(track)}，机会分 {_fmt_num(score)}），"
            f"再从候选池内选择匹配基金，动作定为{rec.action}。"
        )
    if quality is not None and fit is not None:
        return (
            f"先判断板块方向：{sector}，再选择基金质量分 {_fmt_num(quality)}、"
            f"板块匹配分 {_fmt_num(fit)} 的候选基金，动作定为{rec.action}。"
        )
    return f"先判断板块方向：{sector}，再从候选池内选择匹配基金，动作定为{rec.action}。"


def _build_sector_evidence(opportunity: dict | None) -> list[str]:
    if not opportunity:
        return []
    evidence: list[str] = []
    score = opportunity.get("score")
    track = opportunity.get("track")
    confidence = opportunity.get("confidence")
    if score is not None:
        text = f"机会分 {_fmt_num(score)}"
        if track:
            text += f"，{_track_label(track)}"
        if confidence:
            text += f"，置信度{confidence}"
        evidence.append(text)
    today_flow = opportunity.get("today_main_force_net_yi")
    five_day_flow = opportunity.get("cumulative_5d_net_yi")
    if today_flow is not None or five_day_flow is not None:
        parts = []
        if today_flow is not None:
            parts.append(f"今日主力净流入 {_fmt_num(today_flow)} 亿")
        if five_day_flow is not None:
            parts.append(f"5日主力净流入 {_fmt_num(five_day_flow)} 亿")
        evidence.append("，".join(parts))
    pattern = opportunity.get("pattern_label")
    if pattern:
        evidence.append(f"资金/价格信号：{_pattern_label(str(pattern))}")
    evidence.extend(str(item) for item in opportunity.get("evidence") or [] if str(item).strip())
    return evidence


def _build_fund_evidence(pool_item: dict) -> list[str]:
    evidence: list[str] = []
    quality = pool_item.get("fund_quality_score")
    fit = pool_item.get("sector_fit_score")
    if quality is not None or fit is not None:
        parts = []
        if quality is not None:
            parts.append(f"基金质量分 {_fmt_num(quality)}")
        if fit is not None:
            parts.append(f"板块匹配分 {_fmt_num(fit)}")
        evidence.append("，".join(parts))
    reasons = pool_item.get("quality_reasons") or []
    if reasons:
        evidence.append("质量理由：" + "；".join(str(item) for item in reasons[:3]))
    returns = []
    for key, label in (
        ("return_3m_percent", "近3月"),
        ("return_6m_percent", "近6月"),
        ("return_1y_percent", "近1年"),
    ):
        value = pool_item.get(key)
        if value is not None:
            returns.append(f"{label}{_fmt_num(value)}%")
    if returns:
        evidence.append("阶段收益：" + "，".join(returns))
    return evidence


def _build_validation_notes(pool_item: dict, opportunity: dict | None) -> list[str]:
    notes = [
        str(item)
        for item in pool_item.get("quality_penalties") or []
        if str(item).strip()
    ]
    if opportunity:
        notes.extend(
            str(item)
            for item in opportunity.get("penalties") or []
            if str(item).strip()
        )
    if pool_item.get("fund_quality_score") is None:
        notes.append("候选池缺少基金质量分，置信度需保守")
    return notes


def _filter_news_titles(headlines: list[str], known_titles: list[str]) -> list[str]:
    cleaned: list[str] = []
    for headline in headlines:
        text = headline.strip()
        if not text:
            continue
        if known_titles and not _matches_known_title(text, known_titles):
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:3]
