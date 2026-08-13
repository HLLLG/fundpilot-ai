from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from math import isfinite
from typing import Any

from app.models import DiscoveryRecommendation, FundDiscoveryReport, InvestorProfile
from app.services.deepseek_http import ProviderFailure
from app.services.provider_fallback import apply_provider_failure_to_facts
from app.services.decision_score_shadow import attach_decision_score_shadow
from app.services.discovery_allocation_service import (
    apply_deterministic_discovery_allocation,
    prepare_recommendations_for_deterministic_allocation,
)
from app.services.discovery_guard import apply_discovery_guards
from app.services.discovery_strategy import (
    discovery_horizon_label,
    strategy_from_facts,
)
from app.services.discovery_recommendation_scope import (
    MAX_DISCOVERY_RECOMMENDATIONS,
    candidates_in_recommendation_scope,
    ensure_recommendation_candidate_scope,
    reconcile_recommendations_with_scope,
)

_DISCLAIMER = "仅供参考，不构成投资建议；基金有风险，决策需结合自身承受能力。"


def build_offline_discovery_report(
    *,
    target_sectors: list[str],
    candidate_pool: list[dict],
    discovery_facts: dict,
    profile: InvestorProfile,
    focus_sectors: list[str],
    analysis_mode: str = "deep",
    provider_failure: ProviderFailure | None = None,
    attempted_model: str | None = None,
    prompt_contract: dict | None = None,
    decision_at: datetime | None = None,
) -> FundDiscoveryReport:
    from app.services.decision_data_evidence import portfolio_snapshot_caveats

    discovery_strategy = strategy_from_facts(discovery_facts)
    recommendation_scope = ensure_recommendation_candidate_scope(
        discovery_facts,
        candidate_pool,
    )
    scoped_pool = candidates_in_recommendation_scope(
        candidate_pool,
        recommendation_scope,
    )
    provider_failed = provider_failure is not None
    if provider_failure is not None:
        apply_provider_failure_to_facts(
            discovery_facts,
            failure=provider_failure,
            attempted_model=attempted_model or "unknown",
            prompt_contract=prompt_contract,
            execution_blocking=False,
        )

    # The model is an explanation layer. Candidate eligibility, action
    # promotion and the current-opportunity amount are all deterministic, so a
    # provider outage must not erase a valid server-side decision. Start from
    # the persisted whitelist and run the same guards/allocator as the model
    # path. Historical reports without a maturity whitelist keep the old,
    # research-only top-three fallback.
    scope_caveats: list[str] = []
    if recommendation_scope.get("policy_enforced") is True:
        recommendations, scope_caveats = reconcile_recommendations_with_scope(
            [],
            candidate_pool=candidate_pool,
            discovery_facts=discovery_facts,
        )
    else:
        ranked = sorted(
            [
                item
                for item in scoped_pool
                if (item.get("quality_gate") or {}).get("status") != "excluded"
            ],
            key=lambda item: (
                item.get("opportunity_score_20_60d")
                if discovery_strategy == "opportunity_first"
                and item.get("opportunity_score_20_60d") is not None
                else item.get("fund_quality_score") or -999
            ),
            reverse=True,
        )[:MAX_DISCOVERY_RECOMMENDATIONS]
        recommendations = [
            _research_recommendation(item, discovery_strategy, profile)
            for item in ranked
        ]

    portfolio_gap = (
        discovery_facts.get("portfolio_gap")
        if isinstance(discovery_facts.get("portfolio_gap"), Mapping)
        else {}
    )
    budget = _nonnegative_number(portfolio_gap.get("available_budget_yuan")) or 0.0
    held_codes = {
        str(item.get("fund_code") or "").strip().zfill(6)
        for item in portfolio_gap.get("holdings_slim") or []
        if isinstance(item, Mapping) and str(item.get("fund_code") or "").strip()
    }
    sector_heat = [
        dict(item)
        for item in discovery_facts.get("sector_heat") or []
        if isinstance(item, Mapping)
    ]
    prepared = prepare_recommendations_for_deterministic_allocation(
        recommendations,
        candidate_pool=candidate_pool,
    )
    guarded, guard_caveats, eliminated = apply_discovery_guards(
        prepared,
        candidate_pool=candidate_pool,
        held_codes=held_codes,
        profile=profile,
        budget_yuan=budget,
        sector_heat=sector_heat,
        discovery_facts=discovery_facts,
        scan_mode=str(portfolio_gap.get("scan_mode") or "full_market"),
    )
    guarded, allocation_plan, risk_context, allocation_caveats = (
        apply_deterministic_discovery_allocation(
            guarded,
            candidate_pool=candidate_pool,
            discovery_facts=discovery_facts,
            profile=profile,
            budget_yuan=budget,
            decision_at=decision_at,
        )
    )
    discovery_facts["risk_context"] = risk_context
    discovery_facts["allocation_plan"] = allocation_plan

    from app.services.decision_data_evidence import report_execution_blocked

    blocked = report_execution_blocked(discovery_facts)
    pipeline = (
        dict(discovery_facts.get("pipeline") or {})
        if isinstance(discovery_facts.get("pipeline"), Mapping)
        else {}
    )
    pipeline.update(
        {
            "execution_blocked": blocked,
            "deterministic_fallback_applied": True,
            "deterministic_action_count": sum(
                item.action == "分批买入" and item.suggested_amount_yuan is not None
                for item in guarded
            ),
        }
    )
    discovery_facts["pipeline"] = pipeline
    attach_decision_score_shadow(
        discovery_facts,
        candidate_pool,
        decision_at=decision_at,
    )
    actionable_count = sum(
        item.action == "分批买入" and item.suggested_amount_yuan is not None
        for item in guarded
    )
    if blocked:
        summary = "字段级证据时点校验未通过，本次仅保留观察候选；请刷新数据后重新扫描。"
        market_view = "当前证据只足以描述市场背景，不支持买入方向或金额判断。"
    elif actionable_count:
        summary = (
            f"系统已从当前可布局方向中筛出 {actionable_count} 只通过全部硬门槛的基金，"
            "并按本次预算、集中度与风险计算首批参考金额。模型解读不可用不影响该确定性结果。"
        )
        market_view = f"当前可布局方向来自：{', '.join(target_sectors) or '综合'}；后续加减仓由录入持仓后的日报重新分析。"
    else:
        summary = "本次没有基金同时通过方向动作边界、核心质量、载体质量与板块身份门槛，暂不生成买入金额。"
        market_view = f"今日扫描方向：{', '.join(target_sectors) or '综合'}；等待方向或基金门槛改善后再重新核验。"

    caveats = [
        _DISCLAIMER,
        (
            f"模型解读不可用（{provider_failure.category}），已使用确定性规则兜底；"
            "候选、动作和金额均来自服务端门禁与分配器。"
            if provider_failure is not None
            else "当前未调用大模型；候选、动作和金额由服务端确定性规则生成。"
        ),
        *scope_caveats,
        *guard_caveats,
        *allocation_caveats,
        *portfolio_snapshot_caveats(discovery_facts),
    ]
    report = FundDiscoveryReport(
        **({"created_at": decision_at} if decision_at is not None else {}),
        title=(
            "今日基金机会扫描（规则兜底）"
            if provider_failed
            else "今日基金机会扫描（规则计算）"
        ),
        summary=summary,
        market_view=market_view,
        focus_sectors=focus_sectors,
        target_sectors=target_sectors,
        candidate_pool=candidate_pool,
        recommendations=guarded,
        allocation_plan=allocation_plan,
        discovery_facts=discovery_facts,
        caveats=list(dict.fromkeys(item for item in caveats if item)),
        eliminated_candidates=eliminated,
        provider="offline-fallback" if provider_failed else "offline",
        analysis_mode=analysis_mode,  # type: ignore[arg-type]
    )
    return report


def _research_recommendation(
    item: Mapping[str, Any],
    discovery_strategy: str,
    profile: InvestorProfile,
) -> DiscoveryRecommendation:
    return DiscoveryRecommendation(
        fund_code=str(item.get("fund_code") or "").zfill(6),
        fund_name=str(item.get("fund_name") or ""),
        sector_name=str(item.get("sector_label") or ""),
        action="建议关注",
        hold_horizon=discovery_horizon_label(discovery_strategy, profile),
        confidence="中",
        points=[
            f"板块 {item.get('sector_label')} 纳入今日扫描。",
            f"基金质量分约 {item.get('fund_quality_score')}（若有）。",
        ],
        risks=["历史报告未启用完整方向成熟度白名单，本次只保留研究观察。"],
    )


def _nonnegative_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None
