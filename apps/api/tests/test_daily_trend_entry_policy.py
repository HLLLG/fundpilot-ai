"""日报：止盈线不再减仓；潜伏可试仓；趋势仍强可中途上车；结构化过热才停加。"""

from __future__ import annotations

import pytest

from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile, NewsItem
from app.services.recommendation_guard import apply_recommendation_guards
from app.services.recommendations import build_offline_fund_recommendation
from app.services.risk import RiskAssessment
from app.services.sector_opportunity_scoring import (
    ENTRY_POLICY_VERSION_V3,
    true_overheat_add_block_reason,
)

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding, **_kwargs: None,
    )


def _profile(**overrides) -> InvestorProfile:
    payload = {
        "max_drawdown_percent": 15,
        "concentration_limit_percent": 100,
        "expected_investment_amount": 100_000,
        "avoid_chasing": False,
        "round_trip_fee_percent": 3.0,
        "min_net_profit_percent": 3.0,
    }
    payload.update(overrides)
    return InvestorProfile(**payload)


def _request(holding: Holding | None = None, **profile_overrides) -> AnalysisRequest:
    row = holding or Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name="半导体",
        holding_amount=10_000.0,
        holding_return_percent=8.5,
        sector_return_percent=6.2,
    )
    return AnalysisRequest(holdings=[row], profile=_profile(**profile_overrides))


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.2,
        suggested_action="watch",
        alerts=[],
    )


def _ready_opportunity(**overrides) -> dict:
    row = {
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "sector_label": "半导体",
        "direction_score": 76.5,
        "trend_strength_score": 76.5,
        "participation_score": 48.0,
        "position_risk_score": 40.0,
        "entry_state": "ready_to_start",
        "raw_entry_state": "ready_to_start",
        "opportunity_available": True,
        "confidence": "高",
        "track": "momentum",
        "pattern_label": "price_flow_aligned_up",
        "first_tranche_scale": 0.65,
        "overheat_flags": [],
        "change_1d_percent": 6.2,
    }
    row.update(overrides)
    return row


def _facts(opportunity: dict, **holding_overrides) -> dict:
    holding = {
        "fund_code": "519674",
        "sector_opportunity": opportunity,
        "evidence": {
            "composite": {"level": "高", "score": 3.0},
            "components": [
                {
                    "role": "return_signal",
                    "source": "factor",
                    "level": "高",
                    "basis": "主因子动量",
                    "reliability": {"usable": True},
                }
            ],
        },
        "estimated_holding_return_percent": 8.5,
    }
    holding.update(holding_overrides)
    return {
        "holdings": [holding],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }


def _guard(
    facts: dict,
    request: AnalysisRequest | None = None,
    action: str = "分批加仓",
) -> FundRecommendation:
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action=action,
            )
        ],
        [],
        request or _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    return guarded[0]


def test_offline_builder_does_not_reduce_on_take_profit_line() -> None:
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name="半导体",
        holding_amount=10_000.0,
        holding_return_percent=8.5,
        sector_return_percent=6.2,
    )
    rec = build_offline_fund_recommendation(
        holding,
        10.0,
        100_000.0,
        _profile(),
        nav_trend={"pattern_label": "two_day_reversal_down"},
    )

    assert rec.action == "观察"
    assert all("止盈" not in point for point in rec.points)


def test_early_probe_keeps_a_small_daily_add() -> None:
    rec = _guard(
        _facts(
            _ready_opportunity(
                entry_state="forming",
                raw_entry_state="forming",
                probability_early_probe_eligible=True,
                first_tranche_scale=0.4,
                change_1d_percent=0.8,
            )
        )
    )

    assert rec.action == "分批加仓"
    assert rec.suggested_position_change_percent is not None
    assert rec.suggested_position_change_percent > 0


def test_mid_trend_add_is_allowed_when_trend_is_still_strong() -> None:
    rec = _guard(
        _facts(
            _ready_opportunity(
                overheat_flags=["单日涨幅超过4%，短期加速"],
                change_1d_percent=6.2,
            )
        )
    )

    assert rec.action == "分批加仓"
    assert rec.suggested_position_change_percent is not None
    assert rec.suggested_position_change_percent > 0


def test_true_overheat_pauses_the_add() -> None:
    rec = _guard(
        _facts(
            _ready_opportunity(
                overheat_flags=[
                    "单日涨幅超过4%，短期加速",
                    "近5日涨幅超过12%，短期加速",
                ]
            )
        )
    )

    assert rec.action == "暂停追涨"
    assert rec.suggested_position_change_percent is None
    assert any("结构化过热" in point for point in rec.points)


def test_avoid_chasing_pauses_on_a_single_overheat_flag() -> None:
    rec = _guard(
        _facts(_ready_opportunity(overheat_flags=["单日涨幅超过4%，短期加速"])),
        _request(avoid_chasing=True),
    )

    assert rec.action == "暂停追涨"
    assert any("拒绝追高" in point for point in rec.points)


def test_true_overheat_helper_allows_a_single_flag_by_default() -> None:
    assert (
        true_overheat_add_block_reason(
            {"overheat_flags": ["单日涨幅超过4%，短期加速"]}
        )
        is None
    )
    assert (
        true_overheat_add_block_reason(
            {
                "overheat_flags": [
                    "单日涨幅超过4%，短期加速",
                    "近5日涨幅超过12%，短期加速",
                ]
            }
        )
        is not None
    )
    assert (
        true_overheat_add_block_reason(
            {"entry_gate_inputs": {"mainline_status": "crowded"}}
        )
        is not None
    )
