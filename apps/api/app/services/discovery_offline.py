from __future__ import annotations

from app.models import DiscoveryRecommendation, FundDiscoveryReport, InvestorProfile

_DISCLAIMER = "仅供参考，不构成投资建议；基金有风险，决策需结合自身承受能力。"


def build_offline_discovery_report(
    *,
    target_sectors: list[str],
    candidate_pool: list[dict],
    discovery_facts: dict,
    profile: InvestorProfile,
    focus_sectors: list[str],
    analysis_mode: str = "deep",
) -> FundDiscoveryReport:
    from app.services.decision_data_evidence import portfolio_snapshot_caveats

    ranked = sorted(
        [
            item
            for item in candidate_pool
            if (item.get("quality_gate") or {}).get("status") != "excluded"
        ],
        key=lambda item: item.get("fund_quality_score") or -999,
        reverse=True,
    )[:3]
    recommendations: list[DiscoveryRecommendation] = []
    portfolio_snapshot = discovery_facts.get("portfolio_snapshot") or {}
    degraded_portfolio = bool(
        isinstance(portfolio_snapshot, dict)
        and (portfolio_snapshot.get("stale") or not portfolio_snapshot.get("authoritative"))
    )
    from app.services.decision_data_evidence import decision_evidence_allows_action

    evidence_blocked_codes: dict[str, list[str]] = {}
    budget = discovery_facts.get("portfolio_gap", {}).get("available_budget_yuan") or 0.0
    _ = budget

    for item in ranked:
        code = str(item.get("fund_code", "")).zfill(6)
        evidence_allowed, evidence_reasons = decision_evidence_allows_action(
            discovery_facts,
            scope="discovery",
            fund_code=code,
        )
        execution_blocked = degraded_portfolio or not evidence_allowed
        if execution_blocked:
            evidence_blocked_codes[code] = evidence_reasons
        recommendations.append(
            DiscoveryRecommendation(
                fund_code=code,
                fund_name=str(item.get("fund_name", "")),
                sector_name=str(item.get("sector_label", "")),
                action="建议关注",
                suggested_amount_yuan=None,
                amount_note="离线兜底仅保留观察，不生成可执行买入金额",
                hold_horizon=profile.horizon or "1-3个月",
                confidence="低" if execution_blocked else "中",
                points=[
                    f"板块 {item.get('sector_label')} 纳入今日扫描",
                    f"基金质量分约 {item.get('fund_quality_score')}（若有）",
                    "当前为离线兜底，接入 DeepSeek 后可获更完整解读",
                ],
                risks=[
                    "离线模式未调用大模型，结论仅供参考",
                    *(
                        ["持仓快照过期或尚未服务端确认，仅可观察，不具备买入条件"]
                        if execution_blocked
                        else []
                    ),
                ],
            )
        )

    discovery_facts["data_evidence_guard"] = {
        "execution_blocked": bool(evidence_blocked_codes),
        "blocked_fund_codes": sorted(evidence_blocked_codes),
        "reasons_by_fund": evidence_blocked_codes,
    }

    from app.services.decision_data_evidence import report_execution_blocked

    blocked = report_execution_blocked(discovery_facts)
    return FundDiscoveryReport(
        title="今日基金机会扫描（离线）",
        summary=(
            "字段级证据时点校验未通过，本次仅保留观察候选；请刷新数据后重新扫描。"
            if blocked
            else "未配置有效 DeepSeek API Key，已按候选池质量与板块热度给出规则化关注名单。"
        ),
        market_view=(
            "当前证据只足以描述市场背景，不支持买入方向或金额判断。"
            if blocked
            else f"关注板块：{', '.join(target_sectors) or '综合'}。"
        ),
        focus_sectors=focus_sectors,
        target_sectors=target_sectors,
        candidate_pool=candidate_pool,
        recommendations=recommendations,
        discovery_facts=discovery_facts,
        caveats=[
            _DISCLAIMER,
            "当前为离线兜底报告。",
            *portfolio_snapshot_caveats(discovery_facts),
        ],
        provider="offline",
        analysis_mode=analysis_mode,  # type: ignore[arg-type]
    )
