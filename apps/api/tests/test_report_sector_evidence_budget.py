"""板块方向证据的预算契约，以及「整层证据缺席时不得放行加仓」。

回归背景：`analysis_facts.SECTOR_OPPORTUNITY_TIMEOUT_SECONDS` 长期写死 5.0，而它包住的
`build_holding_sector_opportunity_context` 内层最坏需要 12 s+（价格结构 8 s 并发段 +
分位分母 4 s 串行段）。于是网络稍慢就必然触发外层超时，fallback 返回 `held={}`，日报当天
彻底没有板块方向层——而且 `future.cancel()` 对已运行任务无效，被放弃的请求仍会把预算跑完，
裁掉的只是"等待"不是"开销"。

更糟的是缺席之后没有任何东西拦住加仓：`_weak_evidence_reasons` 的板块判定整块写在
`if sector_opportunity:` 里面，`None` 时直接跳过。

这里锁三条契约：
1. 外层预算必须覆盖内层声明的总预算（不能再各自漂移）；
2. 内层各阶段预算必须能被总预算截断（总预算是硬上限，不是建议值）；
3. 方向证据整层缺席时，加仓类动作必须被降级，且原因可读、可区分。
"""
from __future__ import annotations

import pytest

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services import report_sector_opportunity as sector_ctx
from app.services.analysis_facts import SECTOR_OPPORTUNITY_TIMEOUT_SECONDS
from app.services.recommendation_guard import (
    _SECTOR_DIRECTION_ABSENT_NO_SECTOR,
    _SECTOR_DIRECTION_ABSENT_UNAVAILABLE,
    _sector_direction_absence_reason,
    _weak_evidence_reasons,
    apply_recommendation_guards,
)

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    """隔离掉真实盘中数据，避免 reversal/pullback 分支抢先降级、掩盖本文件要测的分支。"""
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )


# --- 契约 1/2：预算对齐与硬上限 ---------------------------------------------


def test_outer_timeout_covers_the_inner_total_budget() -> None:
    """外层预算必须 >= 内层总预算，否则内层还在正常工作就被判超时、整层证据被丢。"""
    assert SECTOR_OPPORTUNITY_TIMEOUT_SECONDS >= (
        sector_ctx.SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS
    )


def test_inner_total_budget_is_derived_from_its_own_stages() -> None:
    """总预算必须由各阶段常量派生：手写一个数字就是此前漂移的根因。"""
    expected = (
        max(
            sector_ctx.SECTOR_FLOW_BUDGET_SECONDS,
            sector_ctx.SECTOR_DIVERGENCE_BUDGET_SECONDS,
            sector_ctx.SECTOR_POSITION_BUDGET_SECONDS,
        )
        + sector_ctx.PERCENTILE_UNIVERSE_BUDGET_SECONDS
        + sector_ctx._SCORING_MARGIN_SECONDS
    )
    assert sector_ctx.SECTOR_OPPORTUNITY_TOTAL_BUDGET_SECONDS == expected


def test_stage_budget_truncates_each_stage_to_the_remaining_total() -> None:
    budget = sector_ctx._StageBudget(1.0)

    # 阶段默认值大于剩余总预算时按剩余截断，小于时保留自己的上限。
    assert budget.stage(8.0) <= 1.0
    assert budget.stage(0.25) == pytest.approx(0.25, abs=0.05)
    assert not budget.exhausted()

    spent = sector_ctx._StageBudget(0.0)
    assert spent.exhausted()
    assert spent.stage(8.0) == 0.0


def test_exhausted_budget_skips_the_networked_position_stage() -> None:
    """预算耗尽时不得再发起联网取数——那只会让整段被判超时。"""
    calls: list[tuple] = []

    def _fetch(labels, trade_date):
        calls.append((labels, trade_date))
        return {"半导体": {}}

    assert (
        sector_ctx._fetch_sector_position_map(
            ["半导体"],
            "2026-08-07",
            _fetch,
            budget_seconds=0.0,
        )
        == {}
    )
    assert calls == []


def test_total_budget_is_threaded_into_every_stage(monkeypatch) -> None:
    """总预算必须真的传到各阶段，而不是只挂在签名上。"""
    seen: dict[str, float] = {}

    def _flow(_heat, _labels, *, trade_date=None, total_timeout_seconds=None):
        seen["flow"] = float(total_timeout_seconds)
        return {}

    def _divergence(_labels, *, total_timeout_seconds=None):
        seen["divergence"] = float(total_timeout_seconds)
        return {}

    monkeypatch.setattr(sector_ctx, "build_sector_flow_map_for_opportunities", _flow)
    monkeypatch.setattr(
        sector_ctx, "build_sector_divergence_map_for_opportunities", _divergence
    )

    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10_000.0,
        )
    ]
    sector_ctx.build_holding_sector_opportunity_context(
        holdings,
        trade_date="2026-08-07",
        fetch_sector_heat=lambda: [
            {"sector_label": "半导体", "heat_score": 1.0, "change_1d_percent": 1.0}
        ],
        fetch_sector_position=lambda _labels, _date: {},
        mainline_by_label={},
        mainline_meta={"available": False, "reason": "test"},
        total_budget_seconds=2.0,
    )

    # 阶段默认是 4.0，总预算 2.0 必须把它压下来。
    assert seen["flow"] <= 2.0
    assert seen["divergence"] <= 2.0


# --- 契约 3：整层缺席不得放行加仓 -------------------------------------------


def _request(*, sector_name: str | None = "半导体") -> AnalysisRequest:
    profile = InvestorProfile(
        decision_style="tactical",
        max_drawdown_percent=15,
        concentration_limit_percent=100,
        expected_investment_amount=100_000,
        avoid_chasing=False,
    )
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name=sector_name,
                holding_amount=10_000,
            )
        ],
        profile=profile,
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.2,
        suggested_action="watch",
        alerts=[],
    )


def _strong_fund_evidence() -> dict:
    """基金侧证据故意给满：证明拦住加仓的是板块层缺席，不是基金侧偏弱。"""
    return {
        "composite": {"level": "高", "score": 3.0},
        "components": [{"source": "factor", "level": "高", "basis": "主因子动量"}],
    }


def test_absence_reason_distinguishes_missing_sector_from_missing_evidence() -> None:
    holding = _request().holdings[0]
    assert (
        _sector_direction_absence_reason(None, holding)
        == _SECTOR_DIRECTION_ABSENT_UNAVAILABLE
    )
    assert (
        _sector_direction_absence_reason({}, holding)
        == _SECTOR_DIRECTION_ABSENT_UNAVAILABLE
    )

    no_sector = _request(sector_name=None).holdings[0]
    assert (
        _sector_direction_absence_reason(None, no_sector)
        == _SECTOR_DIRECTION_ABSENT_NO_SECTOR
    )

    # 有方向行时不得误报。
    assert _sector_direction_absence_reason({"track": "momentum"}, holding) is None


def test_absence_reason_is_opt_in_so_evidence_only_callers_are_unchanged() -> None:
    """`_weak_evidence_reasons` 仍然只吃证据：不传缺席原因时行为与此前一致。"""
    assert _weak_evidence_reasons(None, None) == []
    assert _weak_evidence_reasons(
        None, None, sector_absence_reason=_SECTOR_DIRECTION_ABSENT_UNAVAILABLE
    ) == [_SECTOR_DIRECTION_ABSENT_UNAVAILABLE]


@pytest.mark.parametrize(
    ("sector_name", "expected_reason"),
    [
        ("半导体", _SECTOR_DIRECTION_ABSENT_UNAVAILABLE),
        (None, _SECTOR_DIRECTION_ABSENT_NO_SECTOR),
    ],
)
def test_missing_direction_layer_downgrades_add_even_with_strong_fund_evidence(
    sector_name: str | None,
    expected_reason: str,
) -> None:
    """这是超时 fallback 的真实形状：facts 行在，但 sector_opportunity 整个不存在。"""
    facts = {
        "holdings": [{"fund_code": "519674", "evidence": _strong_fund_evidence()}],
        "sector_rotation": {"available": False, "reason": "timeout"},
    }

    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="分批加仓",
            )
        ],
        [],
        _request(sector_name=sector_name),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )

    rec = guarded[0]
    assert rec.action == "观察"
    assert rec.suggested_position_change_percent is None
    assert expected_reason in rec.points[0]


def test_missing_direction_layer_does_not_relax_risk_actions() -> None:
    """只拦加仓：减仓类动作不得因为少了一层证据反而被放松。"""
    facts = {"holdings": [{"fund_code": "519674", "evidence": _strong_fund_evidence()}]}

    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="减仓评估",
            )
        ],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )

    rec = guarded[0]
    assert rec.action == "减仓评估"
    assert rec.suggested_position_change_percent == -25.0
