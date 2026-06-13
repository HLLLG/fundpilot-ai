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
    ranked = sorted(
        candidate_pool,
        key=lambda item: item.get("return_1y_percent") or -999,
        reverse=True,
    )[:3]
    recommendations: list[DiscoveryRecommendation] = []
    budget = discovery_facts.get("portfolio_gap", {}).get("available_budget_yuan") or 0.0
    per_fund = round(max(budget, profile.expected_investment_amount or 30000) * 0.15, 0)

    for item in ranked:
        recommendations.append(
            DiscoveryRecommendation(
                fund_code=str(item.get("fund_code", "")).zfill(6),
                fund_name=str(item.get("fund_name", "")),
                sector_name=str(item.get("sector_label", "")),
                action="建议关注",
                suggested_amount_yuan=per_fund if per_fund >= 100 else None,
                amount_note="离线规则示意金额，请结合预算调整" if per_fund >= 100 else None,
                hold_horizon=profile.horizon or "1-3个月",
                confidence="中",
                points=[
                    f"板块 {item.get('sector_label')} 纳入今日扫描",
                    f"近1年收益约 {item.get('return_1y_percent')}%（若有）",
                    "当前为离线兜底，接入 DeepSeek 后可获更完整解读",
                ],
                risks=["离线模式未调用大模型，结论仅供参考"],
            )
        )

    return FundDiscoveryReport(
        title="今日基金机会扫描（离线）",
        summary="未配置有效 DeepSeek API Key，已按候选池质量与板块热度给出规则化关注名单。",
        market_view=f"关注板块：{', '.join(target_sectors) or '综合'}。",
        focus_sectors=focus_sectors,
        target_sectors=target_sectors,
        candidate_pool=candidate_pool,
        recommendations=recommendations,
        discovery_facts=discovery_facts,
        caveats=[_DISCLAIMER, "当前为离线兜底报告。"],
        provider="offline",
        analysis_mode=analysis_mode,  # type: ignore[arg-type]
    )
