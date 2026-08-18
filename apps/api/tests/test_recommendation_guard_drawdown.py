"""真实峰谷最大回撤接入动作封顶。

回归背景：此前只有「成本浮亏线」是硬约束（`risk.py` 的 `PORTFOLIO_COST_BASIS_LOSS`，
代码里自己注明"不是组合历史峰值到谷值的最大回撤"）。`portfolio_risk_metrics` 早就算出
了真实峰谷回撤并挂在 `facts["risk_metrics"]`，但没有接进任何封顶逻辑——一个从高点回撤
30% 却仍略有浮盈的组合，过去不会触发任何限制。

封顶要求两个条件同时成立，两者都有独立用例：
1. 样本门槛：`risk_metrics` 可用且 confidence 为高/中（与 facts instruction 的
   「低/不足须声明样本有限、不得据此下强结论」同一口径）；
2. 当前确实处于浮亏——峰谷回撤衡量"回吐过多少"，浮亏线衡量"现在亏多少"，量纲不同，
   直接套用同一阈值几乎任何有历史的组合都会触发。

多数用例用 `tactical` 风格，目的是把"离线保守规则对照"和"当日要闻门"这两条与本主题
无关的降级路径隔离掉；另有一条 `conservative` 用例证明封顶不依赖风格。
"""

import pytest

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.recommendation_guard import (
    _portfolio_drawdown_cap_reason,
    apply_recommendation_guards,
)

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


@pytest.fixture(autouse=True)
def _no_live_intraday_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免真实盘中数据偶发触发 reversal/pullback 分支干扰断言。"""
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )


def _profile(*, prefer_dca: bool = False) -> InvestorProfile:
    return InvestorProfile(
        max_drawdown_percent=15,
        concentration_limit_percent=100,
        expected_investment_amount=100_000,
        avoid_chasing=False,
        prefer_dca=prefer_dca,
    )


def _request(
    *,
    holding_return_percent: float | None = None,
    prefer_dca: bool = False,
) -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000,
                holding_return_percent=holding_return_percent,
            )
        ],
        profile=_profile(prefer_dca=prefer_dca),
    )


def _risk(weighted_return_percent: float) -> RiskAssessment:
    """medium / watch：确保封顶不是被既有的 high 分支顺带触发的。"""
    return RiskAssessment(
        level="medium",
        suggested_action="watch",
        weighted_return_percent=weighted_return_percent,
        alerts=[],
    )


def _risk_metrics(
    *,
    max_drawdown_percent: float | None,
    confidence_level: str = "高",
    available: bool = True,
) -> dict:
    return {
        "available": available,
        "sample_days": 140,
        "max_drawdown_percent": max_drawdown_percent,
        "confidence": {"level": confidence_level, "basis": "测试"},
    }


def _facts(risk_metrics: dict | None) -> dict:
    facts: dict = {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": {
                    "score": 90,
                    "research_score": 90,
                    "track": "momentum",
                    "confidence": "高",
                    "opportunity_available": True,
                    "pattern_label": "price_flow_aligned_up",
                },
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
    if risk_metrics is not None:
        facts["risk_metrics"] = risk_metrics
    return facts


def _guard(
    facts: dict,
    risk: RiskAssessment,
    request: AnalysisRequest | None = None,
) -> FundRecommendation:
    _portfolio, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="分批加仓",
            )
        ],
        [],
        request or _request(),
        risk,
        _TODAY_NEWS,
        facts=facts,
    )
    return guarded[0]


def test_breached_peak_to_trough_drawdown_blocks_adding_while_in_loss() -> None:
    rec = _guard(
        _facts(_risk_metrics(max_drawdown_percent=-22.5)),
        _risk(-3.0),
    )

    assert rec.action == "暂停追涨"
    assert rec.suggested_position_change_percent is None
    assert any("真实峰谷回撤" in point for point in rec.points)


def test_drawdown_cap_also_applies_with_prefer_dca() -> None:
    """封顶衡量的是这个组合实际回吐过多少，与是否愿意追当日涨幅无关。"""
    request = _request(
        holding_return_percent=-8.0,
        prefer_dca=True,
    )

    rec = _guard(
        _facts(_risk_metrics(max_drawdown_percent=-22.5)),
        _risk(-8.0),
        request,
    )

    assert rec.action == "暂停追涨"


def test_profitable_portfolio_is_not_capped_by_historical_drawdown() -> None:
    """曾经 +20% 回落到 +2% 的组合峰谷回撤也会超线，但它现在并没有亏。

    若直接套用浮亏线阈值，几乎任何有历史的组合都会被封顶——这条用例锁住"不混用两个
    不同量纲"的边界。
    """
    rec = _guard(
        _facts(_risk_metrics(max_drawdown_percent=-22.5)),
        _risk(2.0),
    )

    assert rec.action == "分批加仓"
    assert rec.suggested_position_change_percent == 20.0


@pytest.mark.parametrize("confidence_level", ["低", "不足"])
def test_low_confidence_risk_metrics_cannot_drive_the_hard_guard(
    confidence_level: str,
) -> None:
    rec = _guard(
        _facts(
            _risk_metrics(
                max_drawdown_percent=-40.0,
                confidence_level=confidence_level,
            )
        ),
        _risk(-3.0),
    )

    assert rec.action == "分批加仓"


@pytest.mark.parametrize(
    "risk_metrics",
    [
        pytest.param(None, id="key_absent"),
        pytest.param(
            _risk_metrics(max_drawdown_percent=-40.0, available=False),
            id="unavailable",
        ),
        pytest.param(
            _risk_metrics(max_drawdown_percent=None),
            id="drawdown_missing",
        ),
    ],
)
def test_missing_risk_metrics_leave_the_action_unchanged(risk_metrics: dict | None) -> None:
    rec = _guard(_facts(risk_metrics), _risk(-3.0))
    assert rec.action == "分批加仓"


def test_drawdown_within_tolerance_does_not_cap() -> None:
    rec = _guard(
        _facts(_risk_metrics(max_drawdown_percent=-14.9)),
        _risk(-3.0),
    )
    assert rec.action == "分批加仓"


def test_cap_reason_states_both_measured_quantities() -> None:
    """文案必须同时给出峰谷回撤与当前浮亏，否则用户无法区分这两个数字。"""
    reason = _portfolio_drawdown_cap_reason(
        _facts(_risk_metrics(max_drawdown_percent=-22.5)),
        _risk(-3.0),
        _profile(),
    )

    assert reason is not None
    assert "-22.50%" in reason
    assert "15.0%" in reason
    assert "-3.00%" in reason
