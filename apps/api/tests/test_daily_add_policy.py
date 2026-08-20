"""日报加仓侧门禁：加仓间隔、组合同因子去重。"""

from __future__ import annotations

import pytest

from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile, NewsItem
from app.services.recommendation_guard import (
    ADD_INTERVAL_CALENDAR_DAYS,
    _recent_buy_add_block_reason,
    _same_daily_add_risk,
    apply_recommendation_guards,
)
from app.services.risk import RiskAssessment
from app.services.sector_opportunity_scoring import ENTRY_POLICY_VERSION_V3

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )


def _request(*holdings: Holding) -> AnalysisRequest:
    if not holdings:
        holdings = (
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000.0,
            ),
        )
    return AnalysisRequest(
        holdings=list(holdings),
        profile=InvestorProfile(
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
            avoid_chasing=False,
        ),
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.2,
        suggested_action="watch",
        alerts=[],
    )


def _ready_opportunity(label: str, *, direction_score: float, change_1d: float | None = 0.4) -> dict:
    row = {
        "sector_label": label,
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "direction_score": direction_score,
        "trend_strength_score": direction_score,
        "entry_state": "ready_to_start",
        "raw_entry_state": "ready_to_start",
        "opportunity_available": True,
        "confidence": "高",
        "track": "momentum",
        "first_tranche_scale": 1.0,
    }
    if change_1d is not None:
        row["change_1d_percent"] = change_1d
    return row


def _holding_facts(
    code: str,
    *,
    label: str,
    direction_score: float,
    change_1d: float | None = 0.4,
    last_buy: str | None = None,
    as_of: str = "2026-08-19",
) -> dict:
    row = {
        "fund_code": code,
        "sector_label": label,
        "sector_opportunity": _ready_opportunity(
            label, direction_score=direction_score, change_1d=change_1d
        ),
        "evidence": {
            "composite": {"level": "高", "score": 3.0},
            "components": [{"source": "factor", "level": "高", "basis": "主因子动量"}],
        },
    }
    if last_buy is not None:
        row["recent_transactions"] = {
            "schema_version": "transaction_behavior_review.v1",
            "available": True,
            "as_of_date": as_of,
            "last_buy": {"trade_date": last_buy},
        }
    return row


def _guard(recs: list[FundRecommendation], facts: dict, request: AnalysisRequest):
    _, guarded = apply_recommendation_guards(
        recs,
        [],
        request,
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    return guarded


def test_recent_buy_without_a_pullback_pauses_the_add() -> None:
    facts = {
        "holdings": [_holding_facts("519674", label="半导体", direction_score=76.5, last_buy="2026-08-16")],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
        "session": {"effective_trade_date": "2026-08-19"},
    }
    rec = _guard(
        [FundRecommendation(fund_code="519674", fund_name="银河创新成长", action="分批加仓")],
        facts,
        _request(),
    )[0]

    assert rec.action == "暂停追涨"
    assert rec.suggested_position_change_percent is None
    assert any("未满" in point and "惩罚赎回费" in point for point in rec.points)


def test_a_down_day_counts_as_an_executable_pullback() -> None:
    facts = {
        "holdings": [
            _holding_facts(
                "519674",
                label="半导体",
                direction_score=76.5,
                change_1d=-0.8,
                last_buy="2026-08-16",
            )
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }
    rec = _guard(
        [FundRecommendation(fund_code="519674", fund_name="银河创新成长", action="分批加仓")],
        facts,
        _request(),
    )[0]

    assert rec.action == "分批加仓"
    assert rec.suggested_position_change_percent is not None


def test_interval_expires_after_the_penalty_window() -> None:
    reason = _recent_buy_add_block_reason(
        {
            "recent_transactions": {
                "available": True,
                "as_of_date": "2026-08-19",
                "last_buy": {"trade_date": "2026-08-12"},
            }
        },
        {"change_1d_percent": 1.2},
    )
    assert ADD_INTERVAL_CALENDAR_DAYS == 7
    assert reason is None


def test_missing_trade_history_does_not_invent_a_recent_buy() -> None:
    assert _recent_buy_add_block_reason({}, {"change_1d_percent": 1.2}) is None


def test_same_named_group_is_one_risk() -> None:
    assert _same_daily_add_risk("半导体", "半导体材料") is True
    assert _same_daily_add_risk("数字经济", "半导体设备") is True
    assert _same_daily_add_risk("半导体", "煤炭") is False
    assert _same_daily_add_risk("黄金", "黄金股") is False


def test_correlated_adds_keep_only_the_stronger_leg() -> None:
    facts = {
        "holdings": [
            _holding_facts("015788", label="半导体", direction_score=80.0),
            _holding_facts("020356", label="半导体材料", direction_score=62.0),
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }
    request = _request(
        Holding(fund_code="015788", fund_name="数字经济", sector_name="半导体", holding_amount=5_000),
        Holding(fund_code="020356", fund_name="半导体设备", sector_name="半导体材料", holding_amount=5_000),
    )
    guarded = _guard(
        [
            FundRecommendation(fund_code="015788", fund_name="数字经济", action="分批加仓"),
            FundRecommendation(fund_code="020356", fund_name="半导体设备", action="分批加仓"),
        ],
        facts,
        request,
    )

    by_code = {rec.fund_code: rec for rec in guarded}
    assert by_code["015788"].action == "分批加仓"
    assert by_code["015788"].suggested_position_change_percent is not None
    assert by_code["020356"].action == "暂停追涨"
    assert by_code["020356"].suggested_position_change_percent is None
    assert any("同属一笔风险暴露" in point for point in by_code["020356"].points)


def test_uncorrelated_adds_are_both_kept() -> None:
    facts = {
        "holdings": [
            _holding_facts("002610", label="黄金", direction_score=80.0),
            _holding_facts("017787", label="煤炭", direction_score=70.0),
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }
    request = _request(
        Holding(fund_code="002610", fund_name="黄金ETF", sector_name="黄金", holding_amount=5_000),
        Holding(fund_code="017787", fund_name="万家宏观", sector_name="煤炭", holding_amount=5_000),
    )
    guarded = _guard(
        [
            FundRecommendation(fund_code="002610", fund_name="黄金ETF", action="分批加仓"),
            FundRecommendation(fund_code="017787", fund_name="万家宏观", action="分批加仓"),
        ],
        facts,
        request,
    )

    assert {rec.action for rec in guarded} == {"分批加仓"}
