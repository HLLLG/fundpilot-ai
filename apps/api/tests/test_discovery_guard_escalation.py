"""M4：discovery_guard.py 双向语义接入（resolve_discovery_escalation 集成测试）。

与日报 M2 不同，荐基没有"清仓已持仓"的语义——负向共振时把候选整条剔除候选池
（不出现在最终 recommendations 里），正向共振时允许突破常规预算上限给更高建议金额。
"""

from __future__ import annotations

from app.models import InvestorProfile
from app.services.discovery_client import build_discovery_report_from_parsed


def _profile(*, concentration_limit_percent: float = 30) -> InvestorProfile:
    return InvestorProfile(
        decision_style="conservative",
        avoid_chasing=True,
        concentration_limit_percent=concentration_limit_percent,
        expected_investment_amount=100000,
    )


def _pool_item(*, fund_quality_score: float | None) -> dict:
    entry = {
        "fund_code": "020357",
        "fund_name": "华夏半导体材料设备ETF联接C",
        "sector_label": "半导体材料",
        "sector_fit_score": 37.12,
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
        "quality_reasons": ["近3/6月表现占优"],
        "quality_penalties": [],
        "return_3m_percent": 18.2,
        "return_6m_percent": 31.4,
        "return_1y_percent": 42.0,
        "nav_trend": {"distance_from_high_percent": -8.5, "trend_label": "回调企稳"},
        "tradeability": _verified_tradeability(),
    }
    if fund_quality_score is not None:
        entry["fund_quality_score"] = fund_quality_score
    return entry


def _verified_tradeability() -> dict:
    return {
        "schema_version": "fund_tradeability.v1",
        "fund_code": "020357",
        "data_status": "complete",
        "freshness": "fresh",
        "can_purchase": True,
        "purchase_state": "open",
        "redemption_state": "open",
        "currency": "CNY",
        "minimum_purchase_yuan": 10.0,
        "daily_purchase_limit_yuan": None,
        "daily_purchase_limit_unlimited": True,
        "revalidation_required": True,
        "standard_purchase_fee_tiers": [
            {
                "condition": "all",
                "fee_type": "percent",
                "fee_percent": 0.0,
                "source_rate": "standard_undiscounted",
            }
        ],
        "redemption_fee_tiers": [
            {"condition": ">=7d", "min_days": 7, "fee_percent": 0.0}
        ],
        "sales_service_fee_annual_percent": 0.0,
        "share_class_fee_status": "standard_upper_bound_available",
        "source_conflict": False,
        "missing_fields": [],
        "source_ids": ["pytest.tradeability"],
        "checked_at": "2026-06-10T10:00:00+08:00",
        "effective_at": "2026-06-10T10:00:00+08:00",
    }


def _rec(*, action: str = "建议关注", suggested_amount_yuan: float | None = None) -> dict:
    return {
        "fund_code": "020357",
        "fund_name": "华夏半导体材料设备ETF联接C",
        "sector_name": "半导体材料",
        "action": action,
        "suggested_amount_yuan": suggested_amount_yuan,
        "hold_horizon": "2-4周",
        "confidence": "中",
        "points": ["近3/6月表现占优"],
        "risks": ["波动较高"],
    }


def _opportunity(*, opportunity_available: bool, confidence: str = "高") -> dict:
    return {
        "sector_label": "半导体材料",
        "track": "momentum",
        "score": 86.5,
        "confidence": confidence,
        "opportunity_available": opportunity_available,
        "entry_hint": "可分批关注",
        "evidence": ["1d/5d 动量延续"],
        "penalties": ["资金背离或持续流出"] if not opportunity_available else [],
        "today_main_force_net_yi": -3.2 if not opportunity_available else 3.2,
        "cumulative_5d_net_yi": -9.8 if not opportunity_available else 9.8,
        "pattern_label": "distribution" if not opportunity_available else "price_flow_aligned_up",
    }


def _run(
    *,
    pool_item: dict,
    opportunity: dict,
    rec_kwargs: dict | None = None,
    profile: InvestorProfile | None = None,
    budget_yuan: float = 50000,
):
    resolved_profile = profile or _profile()
    parsed = {
        "title": "机会扫描",
        "summary": "半导体材料方向。",
        "recommendations": [_rec(**(rec_kwargs or {}))],
        "caveats": [],
    }
    facts = {
        "portfolio_snapshot": {
            "stale": False,
            "authoritative": True,
            "position_complete": True,
            "pending_transaction_count": 0,
        },
        "portfolio_position_truth": {
            "position_complete": True,
            "cash": {"known": True, "balance_yuan": budget_yuan},
            "positions": [],
        },
        "portfolio_gap": {
            "available_budget_yuan": budget_yuan,
            "total_amount": 0,
            "weight_denominator_yuan": (
                resolved_profile.expected_investment_amount or 0
            ),
            "holdings_slim": [],
        },
        "sector_opportunities": [opportunity],
    }
    return build_discovery_report_from_parsed(
        parsed,
        target_sectors=["半导体材料"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=[pool_item],
        discovery_facts=facts,
        profile=resolved_profile,
        held_codes=set(),
        budget_yuan=budget_yuan,
        sector_heat=[{"sector_label": "半导体材料", "change_1d_percent": 1.0}],
        analysis_mode="fast",
    )


def test_excludes_candidate_when_negative_resonance() -> None:
    """量价背离显著 + 板块不构成机会 + 基金质量分也偏低：应被整条剔除，不出现在
    最终 recommendations 列表里，caveats 须说明剔除原因。"""
    report = _run(
        pool_item=_pool_item(fund_quality_score=40.0),
        opportunity=_opportunity(opportunity_available=False),
    )
    assert report.recommendations == []
    assert any("已从候选池剔除" in line for line in report.caveats)
    assert any("020357" in line for line in report.caveats)


def test_does_not_exclude_when_fund_quality_strong_despite_weak_sector() -> None:
    """板块弱但基金质量分本身够高时不剔除——只是被既有弱证据逻辑降级为「建议关注」
    （若原本就是「建议关注」则保持不变），而不是从候选池消失。"""
    report = _run(
        pool_item=_pool_item(fund_quality_score=70.0),
        opportunity=_opportunity(opportunity_available=False),
    )
    assert len(report.recommendations) == 1
    assert report.recommendations[0].fund_code == "020357"
    assert not any("已从候选池剔除" in line for line in report.caveats)


def test_boost_keeps_amount_below_hard_cap_when_positive_resonance() -> None:
    """积极共振只能提高软建议目标，不能突破预算集中度硬上限。"""
    report = _run(
        pool_item=_pool_item(fund_quality_score=80.0),
        opportunity=_opportunity(opportunity_available=True),
        rec_kwargs={"action": "分批买入", "suggested_amount_yuan": 18000},
    )
    assert len(report.recommendations) == 1
    rec = report.recommendations[0]
    assert rec.suggested_amount_yuan == 12500
    assert any("软建议金额" in line for line in report.caveats)
    assert any("量价背离与基金质量共振积极" in point for point in rec.points)


def test_does_not_boost_when_fund_quality_only_moderate() -> None:
    """板块强但基金质量分不够高（<75）时不提额——金额仍受常规集中度上限约束。"""
    report = _run(
        pool_item=_pool_item(fund_quality_score=60.0),
        opportunity=_opportunity(opportunity_available=True),
        rec_kwargs={"action": "分批买入", "suggested_amount_yuan": 18000},
    )
    assert len(report.recommendations) == 1
    rec = report.recommendations[0]
    # LLM boost 不再改金额；保守策略统一首批比例为预算的 25%。
    assert rec.suggested_amount_yuan == 12500
    assert not any("已提高建议金额上限" in line for line in report.caveats)


def test_no_escalation_when_confidence_not_high() -> None:
    """confidence 非「高」时（量价背离证据不够强），既不剔除也不提额，走既有逻辑。"""
    report = _run(
        pool_item=_pool_item(fund_quality_score=40.0),
        opportunity=_opportunity(opportunity_available=False, confidence="中"),
    )
    assert len(report.recommendations) == 1
    assert not any("已从候选池剔除" in line for line in report.caveats)
