"""基金层退出证据：载体相对所属板块跑输 → 停加；够深或持续则减仓。

背景：确定性减仓链路的两个来源（量价背离共振、方向退出）主语都是**板块**。加仓侧有
基金证据、载体质量、用户成本三道基金层门禁，减仓侧此前一道都没有——一只持续跑输
自己板块的基金，只要板块方向还在线上，永远收不到任何信号。

口径：基金 `nav_trend.return_20d_percent` vs 板块
`mainline_regime.features.return_20d_percent`。20 日落后 ≥8pp 暂停追涨；≥12pp，或
≥8pp 且 5 日也落后 ≥4pp，升到减仓评估 −25%。

另一半兜底：板块方向证据缺失的持仓在整条退出链路上是盲区（退出的主语全是板块），
必须披露"方向退出与减仓信号对该仓不可用"，否则"系统没让我卖"会被读成"系统认为不用卖"。
"""

from __future__ import annotations

import pytest

from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile, NewsItem
from app.services.decision_guard_shared import (
    ACTION_BUCKET_PAUSE,
    ACTION_BUCKET_REDUCE,
    FUND_SECTOR_LAG_DEEP_THRESHOLD_20D,
    FUND_SECTOR_LAG_THRESHOLD_20D,
    resolve_escalation_floor,
)
from app.services.recommendation_guard import apply_recommendation_guards
from app.services.risk import RiskAssessment
from app.services.sector_opportunity_scoring import ENTRY_POLICY_VERSION_V3


def _nav_trend(return_20d: float | None, *, recent_5d: float | None = None) -> dict:
    row = {"return_20d_percent": return_20d, "trend_label": "震荡"}
    if recent_5d is not None:
        row["recent_5d_change_percent"] = recent_5d
    return row


def _sector_opportunity(
    sector_return_20d: float | None = 12.0,
    *,
    sector_return_5d: float | None = None,
    **overrides,
) -> dict:
    row = {
        "sector_label": "半导体",
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "direction_score": 76.5,
        "entry_state": "ready_to_start",
        "raw_entry_state": "ready_to_start",
        "opportunity_available": True,
        "confidence": "中",
        "track": "momentum",
        "first_tranche_scale": 1.0,
        "mainline_regime": {
            "schema_version": "mainline_regime.v1",
            "status": "confirmed",
            "features": {
                "return_20d_percent": sector_return_20d,
                **(
                    {"return_5d_percent": sector_return_5d}
                    if sector_return_5d is not None
                    else {}
                ),
            },
        },
    }
    row.update(overrides)
    return row


def _floor(*, nav_trend: dict | None, sector_opportunity: dict | None, **overrides) -> dict:
    params = {
        "sector_opportunity": sector_opportunity,
        "evidence": None,
        "market_breadth": None,
        "over_concentration": False,
        "has_unrealized_gain": False,
        "direction_exit": None,
        "nav_trend": nav_trend,
    }
    params.update(overrides)
    return resolve_escalation_floor(**params)


# --------------------------------------------------------------------------
# 触发与不触发
# --------------------------------------------------------------------------


def test_moderate_lag_pauses_the_add_without_a_reduction() -> None:
    """8–12 个百分点：停加，但不凭单窗口浅落后要求卖出。"""
    result = _floor(
        nav_trend=_nav_trend(3.5),
        sector_opportunity=_sector_opportunity(12.0),  # 落后 8.5
    )

    assert result["min_bucket"] == ACTION_BUCKET_PAUSE
    assert result["suggested_position_change_percent"] is None
    basis = str(result["basis"])
    assert "+3.50%" in basis and "+12.00%" in basis and "8.5" in basis
    assert "减仓" not in basis


def test_deep_20d_lag_escalates_to_a_reduction() -> None:
    result = _floor(
        nav_trend=_nav_trend(-2.0),
        sector_opportunity=_sector_opportunity(12.0),  # 落后 14 ≥ 12
    )

    assert result["min_bucket"] == ACTION_BUCKET_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-25.0)
    basis = str(result["basis"])
    assert "14.0" in basis and "减仓评估" in basis


def test_persistent_lag_across_both_windows_escalates() -> None:
    result = _floor(
        nav_trend=_nav_trend(3.5, recent_5d=-1.0),
        sector_opportunity=_sector_opportunity(12.0, sector_return_5d=4.0),
    )

    assert result["min_bucket"] == ACTION_BUCKET_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-25.0)
    assert "掉队持续" in str(result["basis"])


def test_moderate_lag_without_a_5d_leg_does_not_invent_persistence() -> None:
    result = _floor(
        nav_trend=_nav_trend(3.5),
        sector_opportunity=_sector_opportunity(12.0),
    )
    assert result["min_bucket"] == ACTION_BUCKET_PAUSE
    assert result["suggested_position_change_percent"] is None


def test_lag_inside_the_threshold_does_not_trigger() -> None:
    result = _floor(
        nav_trend=_nav_trend(4.2),
        sector_opportunity=_sector_opportunity(12.0),  # 落后 7.8 < 8
    )
    assert result["min_bucket"] is None


def test_threshold_is_exclusive_at_the_boundary() -> None:
    """恰好等于阈值即触发（lag <= -8）。"""
    result = _floor(
        nav_trend=_nav_trend(4.0),
        sector_opportunity=_sector_opportunity(4.0 + FUND_SECTOR_LAG_THRESHOLD_20D),
    )
    assert result["min_bucket"] == ACTION_BUCKET_PAUSE


def test_deep_threshold_escalates_at_the_boundary() -> None:
    result = _floor(
        nav_trend=_nav_trend(0.0),
        sector_opportunity=_sector_opportunity(FUND_SECTOR_LAG_DEEP_THRESHOLD_20D),
    )
    assert result["min_bucket"] == ACTION_BUCKET_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-25.0)


@pytest.mark.parametrize(
    "nav_trend, sector_opportunity",
    [
        (None, _sector_opportunity(12.0)),
        (_nav_trend(None), _sector_opportunity(12.0)),
        (_nav_trend(-2.0), None),
        (_nav_trend(-2.0), _sector_opportunity(None)),
        # 旧口径行没有主线快照：没有 20 日收益轴，不硬凑。
        (_nav_trend(-2.0), _sector_opportunity(12.0, mainline_regime=None)),
    ],
)
def test_missing_either_leg_does_not_trigger(nav_trend, sector_opportunity) -> None:
    """"不知道"不等于"在跑输"——与浮亏门禁对 None 的处理同一纪律。"""
    result = _floor(nav_trend=nav_trend, sector_opportunity=sector_opportunity)
    assert result["min_bucket"] is None


def test_outperforming_fund_is_untouched() -> None:
    result = _floor(
        nav_trend=_nav_trend(20.0),
        sector_opportunity=_sector_opportunity(12.0),
    )
    assert result["min_bucket"] is None


# --------------------------------------------------------------------------
# 与既有来源的合并
# --------------------------------------------------------------------------


def test_direction_exit_reduce_wins_over_the_lag_pause() -> None:
    """更保守的来源胜出，理由两边都保留。"""
    direction_exit = {
        "min_bucket": ACTION_BUCKET_REDUCE,
        "reasons": ["方向「半导体」趋势强度 41.0 已跌破退出线 52"],
        "suggested_position_change_percent": -25.0,
    }
    result = _floor(
        nav_trend=_nav_trend(-2.0),
        sector_opportunity=_sector_opportunity(12.0),
        direction_exit=direction_exit,
    )

    assert result["min_bucket"] == ACTION_BUCKET_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-25.0)
    reasons = "；".join(str(item) for item in result["reasons"])
    assert "跌破退出线" in reasons and "载体未跟上方向" in reasons


def test_lag_alone_still_surfaces_when_no_other_source_triggers() -> None:
    result = _floor(
        nav_trend=_nav_trend(3.5),
        sector_opportunity=_sector_opportunity(12.0),
        direction_exit={"min_bucket": None, "reasons": []},
    )
    assert result["min_bucket"] == ACTION_BUCKET_PAUSE


# --------------------------------------------------------------------------
# 集成：guard 链里生效 + 板块缺失兜底披露
# --------------------------------------------------------------------------

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    """与 test_daily_action_proposal 同款：guard 会对每只持仓拉盘中信号，测试保持离线。"""
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000.0,
            )
        ],
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


def _guard(facts, *, llm_action: str) -> FundRecommendation:
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action=llm_action,
            )
        ],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    return guarded[0]


def test_guard_reduces_an_llm_add_on_a_deeply_lagging_fund() -> None:
    facts = {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": _sector_opportunity(12.0),
                "nav_trend": _nav_trend(-2.0),
                "evidence": {
                    "composite": {"level": "高", "score": 3.0},
                    "components": [
                        {"source": "factor", "level": "高", "basis": "主因子动量"}
                    ],
                },
            }
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }

    rec = _guard(facts, llm_action="分批加仓")

    assert rec.action == "减仓评估"
    assert rec.suggested_position_change_percent == pytest.approx(-25.0)
    assert any("载体未跟上方向" in point for point in rec.points)


def test_missing_sector_direction_gets_the_blind_spot_disclosure() -> None:
    """方向证据缺失的持仓要明说"退出链路对它不可用"，不许静默。"""
    facts = {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": None,
                "nav_trend": _nav_trend(-2.0),
            }
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }

    rec = _guard(facts, llm_action="观察")

    assert any(
        "方向退出与确定性减仓信号对该持仓不可用" in note
        for note in rec.validation_notes
    )
