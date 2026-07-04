from __future__ import annotations

from app.models import FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_facts import (
    _BASE_ALLOWED_ACTIONS,
    _extra_allowed_actions_for_escalation,
    build_analysis_facts,
)
from app.services.decision_guard_shared import ACTION_BUCKET_CLEAR_ALL


def test_holding_facts_separate_trade_date_from_latest_nav_date():
    holding = Holding(
        fund_code="008586",
        fund_name="AI ETF Link",
        holding_amount=9068.69,
        holding_return_percent=9.45,
        daily_return_percent=-4.62,
        daily_return_percent_source="sector_estimate",
        sector_name="AI",
        sector_return_percent=-4.62,
        sector_return_percent_source="realtime",
    )
    risk = RiskAssessment(
        level="medium",
        weighted_return_percent=1.2,
        suggested_action="watch",
        alerts=[],
    )
    facts = build_analysis_facts(
        [holding],
        risk,
        [
            FundSnapshot(
                fund_code="008586",
                fund_name="AI ETF Link",
                latest_nav=1.9347,
                nav_date="2026-06-25",
                source="akshare",
            )
        ],
        InvestorProfile(),
        session={
            "session_kind": "trading_day_after_close",
            "effective_trade_date": "2026-06-26",
        },
    )

    row = facts["holdings"][0]
    assert row["nav_date"] == "2026-06-25"
    assert row["daily_return_trade_date"] == "2026-06-26"
    assert row["daily_return_data_source"] == "sector_estimate"
    assert row["nav_date_is_current_trade_date"] is False
    assert facts["data_freshness"]["effective_trade_date"] == "2026-06-26"
    assert facts["data_freshness"]["has_stale_nav_dates"] is True


# --- M2.2: allowed_actions 动态扩展 + M2.1 escalation 挂载（build_analysis_facts 集成测试） -----


def _base_risk() -> RiskAssessment:
    return RiskAssessment(level="medium", weighted_return_percent=1.2, suggested_action="watch", alerts=[])


def test_allowed_actions_defaults_to_base_five_without_strong_signals():
    """无任何持仓触发升级信号时，allowed_actions 应只有基础 5 档（不出现「大幅减仓评估」
    「清仓评估」），符合设计"没有强证据共振时，prompt 里根本不出现这两个选项"的要求。"""
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        holding_amount=10000,
        sector_name="半导体",
    )
    facts = build_analysis_facts([holding], _base_risk(), [], InvestorProfile())
    assert facts["allowed_actions"] == ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"]
    assert "escalation" in facts["holdings"][0]
    # 没有 sector_opportunity 数据时，resolve_escalation_floor 应优雅返回 min_bucket=None。
    assert facts["holdings"][0]["escalation"]["min_bucket"] is None


def test_allowed_actions_appends_deep_reduce_and_clear_all_when_row5_triggered():
    """当某持仓的 escalation 判定命中第5档（清仓评估）时，allowed_actions 须同时追加
    「大幅减仓评估」与「清仓评估」（第5档隐含已经过了第4档，两个选项都应开放给 LLM）。"""
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        holding_amount=10000,
        sector_name="半导体",
        holding_return_percent=5.0,
    )
    profile = InvestorProfile(concentration_limit_percent=1)  # 强制 over_concentration=True
    facts = build_analysis_facts([holding], _base_risk(), [], profile)
    # 手工在 facts 计算完成后模拟"该持仓命中强量价背离+基金证据不足+情绪冰点+集中度超限+
    # 多重 penalty"的极端场景，验证 allowed_actions 会随 escalation 结果动态调整。
    facts["holdings"][0]["sector_opportunity"] = {
        "opportunity_available": False,
        "confidence": "高",
        "penalties": ["资金背离或持续流出", "单日涨幅过热"],
    }
    facts["holdings"][0]["evidence"] = {"composite": {"level": "不足"}}
    facts["market_breadth"] = {"sentiment_level": "冰点", "sentiment_level_change": -2}
    from app.services.analysis_facts import _attach_escalation_to_holdings

    _attach_escalation_to_holdings(
        facts["holdings"], market_breadth=facts["market_breadth"], profile=profile
    )
    assert facts["holdings"][0]["escalation"]["min_bucket"] == ACTION_BUCKET_CLEAR_ALL

    # 直接调用抽取出的 _extra_allowed_actions_for_escalation（M6：同一函数也是
    # shadow 灰度门控的实现，见 test_decision_escalation_mode.py 对 shadow 分支
    # 的验证），比重新跑一次完整 build_analysis_facts 更聚焦。测试环境默认切到
    # enforced（见 conftest.py::_auth_env），因此这里应返回非空列表。
    extra = _extra_allowed_actions_for_escalation(facts["holdings"])
    allowed = [*_BASE_ALLOWED_ACTIONS, *extra]
    assert "清仓评估" in allowed
    assert "大幅减仓评估" in allowed
