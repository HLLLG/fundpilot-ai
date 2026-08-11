"""日报证据门禁必须分级：方向证据缺失只约束加仓，不能硬关整份日报。

回归背景（2026-08-11 14:30 线上实测，报告 79fd69280da749678aceb7a756c24dee）：

板块方向层整层超时（`sector_rotation.reason=timeout`），6 只持仓的
`holdings.{code}.sector_opportunity` 全部缺席。而盘中另外两条方向证据本就恒为 `unknown`
（官方净值未出、板块资金流日期未对齐），于是那道"三选一"的门禁三条腿同时失效——它看起来
有冗余，实际是单点。

后果不是"少了一层证据"，而是**整份日报的仓位动作被硬关**：
  - 模型当时一个加仓都没提（观察×4、风控复核、暂停追涨）；
  - 012200 的「风控复核」被降成「观察」，011036 的「暂停追涨」同样被降成「观察」；
  - 6 张卡片的 `points[0]` 塌缩成同一句常量，而前端「核心理由」渲染的正是 `points[0]`，
    所以用户看到 6 条一字不差的理由，既不知道缺的是哪类数据，也无法判断该不该再等一轮。

一个声明为 best-effort、"绝不阻塞日报"的增强项，其缺席却让风险结论被抹掉——这是语义反转。
本文件锁两条契约：
1. 方向证据缺失只拦加仓；风险动作与观察不受影响（风险动作不该因为少了证据反而被放松）；
2. 阻断文案按真实原因分化，不再所有情形共用一句。
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
from app.services.decision_data_evidence import decision_evidence_allows_action
from app.services.recommendation_guard import (
    _BLOCKED_POINT_FALLBACK_DEFAULT,
    _blocked_points_fallback,
    apply_recommendation_guards,
)

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]
_CODE = "519674"


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    """隔离真实盘中数据，避免 reversal/pullback 分支抢先降级、掩盖本文件要测的分支。"""
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )


def _observed_registry_items() -> list[dict]:
    """08-11 14:30 那次的真实形状：方向层整项缺席，两条 return 都是 unknown。"""
    return [
        {
            "fact_id": f"holdings.{_CODE}.holding_amount",
            "freshness": "fresh",
            "confidence": "high",
        },
        {
            "fact_id": f"holdings.{_CODE}.daily_return_percent",
            "freshness": "unknown",
            "confidence": "low",
        },
        {
            "fact_id": f"holdings.{_CODE}.sector_return_percent",
            "freshness": "unknown",
            "confidence": "medium",
        },
        # `sector_opportunity` 整项不存在（超时 fallback 后 analysis_facts 不再产出它）
        {
            "fact_id": f"holdings.{_CODE}.tradeability",
            "freshness": "fresh",
            "confidence": "high",
        },
        {
            "fact_id": f"holdings.{_CODE}.purchase_execution",
            "freshness": "fresh",
            "confidence": "high",
        },
        {
            "fact_id": f"holdings.{_CODE}.redemption_execution",
            "freshness": "fresh",
            "confidence": "high",
        },
    ]


def _facts_with_registry(items: list[dict] | None = None) -> dict:
    return {
        "holdings": [
            {
                "fund_code": _CODE,
                "evidence": {
                    "composite": {"level": "高", "score": 3.0},
                    "components": [
                        {"source": "factor", "level": "高", "basis": "主因子动量"}
                    ],
                },
            }
        ],
        "sector_rotation": {"available": False, "reason": "timeout"},
        "data_evidence": {
            "decision_ready": True,
            "blocking_reasons": [],
            "items": _observed_registry_items() if items is None else items,
        },
    }


# --- 契约 1：门禁分级 --------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "expected_allowed"),
    [
        ("none", True),  # 观察 / 风控复核 / 暂停追涨
        ("reduce", True),  # 风险动作不得因缺证据被放松
        ("add", False),  # 加仓仍然需要方向证据
        (None, False),  # 调用方未声明方向时保守处理
    ],
)
def test_missing_directional_evidence_only_gates_add(
    direction: str | None,
    expected_allowed: bool,
) -> None:
    allowed, reasons = decision_evidence_allows_action(
        _facts_with_registry(),
        scope="analysis",
        fund_code=_CODE,
        direction=direction,
        allow_incomplete_position_for_direction=True,
    )
    assert allowed is expected_allowed
    if expected_allowed:
        assert reasons == []
    else:
        assert reasons == ["directional_evidence_not_point_in_time_usable"]


@pytest.mark.parametrize("direction", ["none", "reduce", "add"])
def test_missing_holding_amount_still_blocks_every_direction(direction: str) -> None:
    """持仓金额未确认是硬阻断：不知道持了多少，任何量化动作都无从谈起。"""
    items = [
        item
        for item in _observed_registry_items()
        if not item["fact_id"].endswith(".holding_amount")
    ]
    allowed, reasons = decision_evidence_allows_action(
        _facts_with_registry(items),
        scope="analysis",
        fund_code=_CODE,
        direction=direction,
        allow_incomplete_position_for_direction=True,
    )
    assert allowed is False
    assert "holding_amount_not_point_in_time_usable" in reasons


def test_usable_directional_evidence_passes_the_add_gate() -> None:
    """三条腿任意一条可用就够——收窄的是"缺失时拦谁"，不是判定标准本身。"""
    items = [
        item if not item["fact_id"].endswith(".sector_return_percent")
        else {**item, "freshness": "fresh"}
        for item in _observed_registry_items()
    ]
    allowed, reasons = decision_evidence_allows_action(
        _facts_with_registry(items),
        scope="analysis",
        fund_code=_CODE,
        direction="add",
        allow_incomplete_position_for_direction=True,
    )
    assert (allowed, reasons) == (True, [])


# --- 契约 1 的端到端：风险动作不得被"缺数据"抹掉 -----------------------------


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code=_CODE,
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000,
            )
        ],
        profile=InvestorProfile(
            decision_style="tactical",
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


def _guard(action: str):
    _, guarded = apply_recommendation_guards(
        [FundRecommendation(fund_code=_CODE, fund_name="银河创新成长", action=action)],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=_facts_with_registry(),
    )
    return guarded[0]


@pytest.mark.parametrize("action", ["风控复核", "减仓评估"])
def test_risk_actions_survive_a_missing_direction_layer(action: str) -> None:
    """线上正是这里出的事：012200 的「风控复核」被降成「观察」。"""
    rec = _guard(action)
    assert rec.action == action
    assert _BLOCKED_POINT_FALLBACK_DEFAULT not in rec.points


def test_add_is_still_downgraded_when_the_direction_layer_is_missing() -> None:
    """收窄门禁不等于放松加仓：缺方向证据时加仓必须仍被降级。"""
    rec = _guard("分批加仓")
    assert rec.action == "观察"
    assert rec.suggested_position_change_percent is None


# --- 契约 2：阻断文案按原因分化 ---------------------------------------------


@pytest.mark.parametrize(
    ("reasons", "expected_fragment"),
    [
        (["directional_evidence_not_point_in_time_usable"], "板块方向证据未取到"),
        (["holding_amount_not_point_in_time_usable"], "持仓金额未确认为最新"),
        (["stale_portfolio_snapshot"], "持仓快照还是上一交易日"),
        (["non_authoritative_portfolio"], "非服务端权威快照"),
        (["holding_purchase_execution_not_point_in_time_usable"], "申购可执行状态未核实"),
        (["holding_redemption_execution_not_point_in_time_usable"], "赎回可执行状态未核实"),
    ],
)
def test_blocked_fallback_names_the_actual_reason(
    reasons: list[str],
    expected_fragment: str,
) -> None:
    text = _blocked_points_fallback(reasons)
    assert expected_fragment in text
    assert text != _BLOCKED_POINT_FALLBACK_DEFAULT


def test_blocked_fallback_flags_that_more_than_one_item_is_pending() -> None:
    text = _blocked_points_fallback(
        [
            "holding_amount_not_point_in_time_usable",
            "directional_evidence_not_point_in_time_usable",
        ]
    )
    assert "持仓金额未确认为最新" in text
    assert "另有其他数据项待确认" in text


@pytest.mark.parametrize("reasons", [[], ["some_unmapped_reason"]])
def test_blocked_fallback_falls_back_to_the_generic_sentence(reasons: list[str]) -> None:
    assert _blocked_points_fallback(reasons) == _BLOCKED_POINT_FALLBACK_DEFAULT
