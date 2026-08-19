"""日报持仓的载体质量判断（`fund_vehicle_quality.assess_holding_vehicle_quality`）。

这个文件的重点不是"分数算得对不对"，而是**证据缺失不得被当成载体更差**。荐基的
`assess_candidate_vehicle_quality` 直接用在日报持仓行上会让每一只持仓都落到
`watch_only`：主动基金因为 `quality_score_components` 不存在而拿 0 分，被动基金因为
`sector_match_kind` 不存在而撞上硬门。下面的用例把这两条都钉住。
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
from app.services.fund_vehicle_quality import (
    PASSIVE_QUALITY_THRESHOLD,
    assess_candidate_vehicle_quality,
    assess_holding_vehicle_quality,
    attach_holding_vehicle_quality,
)
from app.services.recommendation_guard import (
    _resolve_deterministic_position_change,
    apply_recommendation_guards,
)


def _tracking(*, available: bool = True, error: float = 0.8, difference: float | None = None) -> dict:
    metrics: dict = {"applicable": True, "available": available}
    if error is not None:
        metrics["tracking_error_annualized_percent"] = error
    if difference is not None:
        metrics["tracking_difference_percent"] = difference
    return {"tracking_metrics": metrics}


def _passive_row(**overrides) -> dict:
    row = {
        "fund_code": "510300",
        "fund_name": "华泰柏瑞沪深300ETF",
        "fund_type": "指数型",
        "fund_scale_yi": 50.0,
        "management_fee": "0.15%",
        "benchmark_metrics": _tracking(),
    }
    row.update(overrides)
    return row


def _active_row(**overrides) -> dict:
    row = {
        "fund_code": "519674",
        "fund_name": "银河创新成长",
        "fund_type": "混合型",
        "fund_scale_yi": 50.0,
        "management_fee": "1.50%",
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- #
# 评估器本身
# --------------------------------------------------------------------------- #


def test_active_holding_is_not_applicable_instead_of_low_score() -> None:
    """主动持仓拿到 not_applicable，而不是 0 分 + watch_only。"""
    assessment = assess_holding_vehicle_quality(_active_row())

    assert assessment["applicable"] is False
    assert assessment["status"] == "not_applicable"
    assert assessment["score"] is None
    assert assessment["threshold"] is None
    # 必须显式告知不得据此判低，否则 LLM 会把"没有评分"讲成"质量不佳"。
    assert "evidence" in assessment["note"]
    assert "不得" in assessment["note"]


def test_candidate_assessor_would_have_marked_the_same_active_row_watch_only() -> None:
    """反证：这就是不能直接复用荐基评估器的原因。"""
    candidate = assess_candidate_vehicle_quality(_active_row())

    assert candidate["vehicle_quality_status"] == "watch_only"
    assert candidate["vehicle_quality_score"] == 0.0


def test_candidate_assessor_would_have_marked_the_same_passive_row_watch_only() -> None:
    """被动行的死因不同：缺 sector_match_kind，撞的是硬门而不是分数。"""
    candidate = assess_candidate_vehicle_quality(_passive_row())

    assert candidate["vehicle_quality_status"] == "watch_only"
    assert any("精确跟踪标的" in text for text in candidate["vehicle_quality_assessment"]["penalties"])


def test_passive_holding_without_sector_match_kind_can_still_be_eligible() -> None:
    """日报版刻意不移植板块身份硬门——持仓已经在手上，要问的是它跟没跟住基准。"""
    assessment = assess_holding_vehicle_quality(_passive_row())

    assert assessment["applicable"] is True
    assert assessment["status"] == "eligible"
    assert assessment["score"] == 100.0
    assert assessment["sector_identity_gate_excluded"] is True
    assert set(assessment["components"]) == {"scale", "fee", "tracking_quality"}
    assert "跟踪误差较低" in assessment["reasons"]
    assert not any("精确跟踪标的" in text for text in assessment["penalties"])


def test_weak_passive_vehicle_is_watch_only() -> None:
    assessment = assess_holding_vehicle_quality(
        _passive_row(
            fund_scale_yi=0.3,
            management_fee="1.50%",
            benchmark_metrics=_tracking(error=6.0),
        )
    )

    assert assessment["status"] == "watch_only"
    assert assessment["score"] < PASSIVE_QUALITY_THRESHOLD
    assert "基金规模过小" in assessment["penalties"]
    assert "管理费率偏高" in assessment["penalties"]
    assert "跟踪误差偏高" in assessment["penalties"]


def test_threshold_boundary_counts_as_eligible() -> None:
    """规模 17.5 + 费率 10 + 跟踪中性 10 = 37.5/62.5 = 60.0，正好压在阈值上。"""
    assessment = assess_holding_vehicle_quality(
        _passive_row(fund_scale_yi=2.0, management_fee="1.00%", benchmark_metrics={})
    )

    assert assessment["score"] == PASSIVE_QUALITY_THRESHOLD
    assert assessment["status"] == "eligible"


def test_missing_benchmark_metrics_scores_tracking_neutral_with_penalty() -> None:
    """载体质量必须排在基准挂载之后；提前调用时这条惩罚就是信号。"""
    assessment = assess_holding_vehicle_quality(_passive_row(benchmark_metrics=None))

    assert assessment["components"]["tracking_quality"] == 10.0
    assert any("样本" in text for text in assessment["penalties"])


def test_negative_tracking_difference_further_reduces_tracking_score() -> None:
    assessment = assess_holding_vehicle_quality(
        _passive_row(benchmark_metrics=_tracking(error=0.8, difference=-8.0))
    )

    assert assessment["components"]["tracking_quality"] == 15.0
    assert "相对跟踪标的差异偏弱" in assessment["penalties"]


def test_unverified_scale_is_penalised_but_does_not_crash() -> None:
    assessment = assess_holding_vehicle_quality(_passive_row(fund_scale_yi=None))

    assert assessment["components"]["scale"] == 0.0
    assert "基金规模未核验" in assessment["penalties"]


def test_attach_writes_one_key_per_row_and_skips_non_mappings() -> None:
    rows = attach_holding_vehicle_quality([_passive_row(), _active_row(), None, "junk"])

    assert len(rows) == 2
    assert rows[0]["vehicle_quality"]["status"] == "eligible"
    assert rows[1]["vehicle_quality"]["status"] == "not_applicable"
    # 原行字段保留，不被覆盖。
    assert rows[0]["fund_code"] == "510300"


# --------------------------------------------------------------------------- #
# 接入加仓分档
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )


def _request() -> AnalysisRequest:
    profile = InvestorProfile(
        max_drawdown_percent=15,
        concentration_limit_percent=100,
        expected_investment_amount=100000,
        avoid_chasing=False,
    )
    holdings = [
        Holding(
            fund_code="510300",
            fund_name="华泰柏瑞沪深300ETF",
            sector_name="半导体",
            holding_amount=10000,
        )
    ]
    return AnalysisRequest(holdings=holdings, profile=profile)


def _strong_fund_evidence() -> dict:
    return {"composite": {"level": "高", "score": 3.0}}


def _position(vehicle_quality: dict | None, evidence: dict | None = None):
    request = _request()
    return _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity={"score": 85},
        evidence=evidence if evidence is not None else _strong_fund_evidence(),
        vehicle_quality=vehicle_quality,
    )


@pytest.mark.parametrize(
    ("vehicle_quality", "case"),
    [
        (None, "missing"),
        ({"applicable": False, "status": "not_applicable"}, "active_not_applicable"),
        ({"applicable": True, "status": "eligible", "score": 100.0}, "eligible"),
        ({"status": "watch_only"}, "watch_only_without_applicable_flag"),
        ("junk", "non_dict"),
    ],
)
def test_vehicle_quality_that_carries_no_verdict_keeps_the_sector_tier(
    vehicle_quality,
    case: str,
) -> None:
    """只有 applicable=True 且 watch_only 才下调；缺失/不适用/合格一律不动档位。

    `active_not_applicable` 是本文件最重要的一条：主动持仓在日报侧永远拿不到载体评分，
    如果它也被下调，等于所有主动基金的加仓都被无证据地砍一档。
    """
    percent, basis, note = _position(vehicle_quality)

    assert percent == 20.0, case
    assert "载体质量" not in basis, case
    assert note is None, case


def test_watch_only_vehicle_blocks_the_add() -> None:
    percent, _basis, note = _position(
        {
            "applicable": True,
            "status": "watch_only",
            "score": 16.0,
            "penalties": ["跟踪误差偏高", "基金规模过小", "管理费率偏高"],
        }
    )

    assert percent is None
    assert note is not None
    assert "被动载体质量未达标" in note
    assert "跟踪误差偏高、基金规模过小" in note
    assert "管理费率偏高" not in note


def test_weak_vehicle_blocks_even_when_fund_evidence_is_only_medium() -> None:
    """载体不合格是硬拦：不再与基金证据各降一档后仍给出加仓比例。"""
    percent, _basis, note = _position(
        {"applicable": True, "status": "watch_only", "penalties": ["跟踪误差偏高"]},
        evidence={
            "composite": {"level": "中", "score": 2.0},
            "components": [
                {
                    "source": "factor",
                    "role": "return_signal",
                    "level": "中",
                    "direction": "positive",
                    "reliability": {"level": "中", "scope": "peer_group", "usable": True},
                }
            ],
        },
    )

    assert percent is None
    assert note is not None
    assert "被动载体质量未达标" in note


def test_watch_only_blocks_even_at_the_lowest_sector_tier() -> None:
    request = _request()
    percent, _basis, note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity=None,
        evidence=_strong_fund_evidence(),
        vehicle_quality={"applicable": True, "status": "watch_only"},
    )

    assert percent is None
    assert note is not None
    assert "被动载体质量未达标" in note


def test_full_guard_reads_vehicle_quality_from_facts_row() -> None:
    facts = {
        "holdings": [
            {
                "fund_code": "510300",
                "sector_opportunity": {
                    "score": 85,
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
                "vehicle_quality": {
                    "applicable": True,
                    "status": "watch_only",
                    "penalties": ["跟踪误差偏高"],
                },
            }
        ]
    }
    rec = FundRecommendation(
        fund_code="510300",
        fund_name="华泰柏瑞沪深300ETF",
        action="分批加仓",
    )

    _, guarded = apply_recommendation_guards(
        [rec],
        [],
        _request(),
        RiskAssessment(
            level="medium",
            weighted_return_percent=1.2,
            suggested_action="watch",
            alerts=[],
        ),
        [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)],
        facts=facts,
    )

    assert guarded[0].action == "观察"
    assert guarded[0].suggested_position_change_percent is None
    assert any("被动载体质量未达标" in point for point in guarded[0].points)


# --------------------------------------------------------------------------- #
# 「基金依据」栏展示
# --------------------------------------------------------------------------- #


def _guard_with_vehicle(vehicle_quality: dict | None, *, action: str = "观察"):
    facts = {
        "holdings": [
            {
                "fund_code": "510300",
                "sector_opportunity": {"score": 60, "confidence": "中"},
                "evidence": {
                    "composite": {"level": "中", "score": 2.0},
                    "components": [
                        {"source": "factor", "level": "中", "basis": "主因子动量"}
                    ],
                },
                **({"vehicle_quality": vehicle_quality} if vehicle_quality else {}),
            }
        ]
    }
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="510300",
                fund_name="华泰柏瑞沪深300ETF",
                action=action,
            )
        ],
        [],
        _request(),
        RiskAssessment(
            level="medium",
            weighted_return_percent=1.2,
            suggested_action="watch",
            alerts=[],
        ),
        [],
        facts=facts,
    )
    return guarded[0]


def test_watch_only_vehicle_surfaces_in_fund_evidence_even_without_an_add_action() -> None:
    """动作不是加仓时，档位分档那条路走不到，用户仍需知道这只工具本身有短板。"""
    rec = _guard_with_vehicle(
        {
            "applicable": True,
            "status": "watch_only",
            "penalties": ["跟踪误差偏高", "基金规模过小"],
        }
    )

    assert rec.action == "观察"
    assert any("被动载体质量未达标" in text for text in rec.fund_evidence)
    assert any("跟踪误差偏高" in text for text in rec.fund_evidence)


def test_eligible_vehicle_is_reported_as_a_positive_fund_side_datum() -> None:
    rec = _guard_with_vehicle(
        {
            "applicable": True,
            "status": "eligible",
            "reasons": ["跟踪误差较低", "基金规模处于稳健区间"],
        }
    )

    assert any("被动载体质量合格" in text for text in rec.fund_evidence)
    assert any("跟踪误差较低" in text for text in rec.fund_evidence)


@pytest.mark.parametrize(
    ("vehicle_quality", "case"),
    [
        (None, "missing"),
        ({"applicable": False, "status": "not_applicable"}, "active_not_applicable"),
    ],
)
def test_no_verdict_adds_no_fund_evidence_line(vehicle_quality, case: str) -> None:
    """主动持仓不该背上一句"载体质量不适用"——那是噪声，还容易被读成缺陷。"""
    rec = _guard_with_vehicle(vehicle_quality)

    assert not any("载体质量" in text for text in rec.fund_evidence), case
    # 原有的量化证据依据不受影响。
    assert rec.fund_evidence
