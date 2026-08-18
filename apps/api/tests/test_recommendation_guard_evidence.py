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
from app.services.recommendation_guard import (
    _resolve_deterministic_position_change,
    _weak_evidence_reasons,
    apply_recommendation_guards,
)


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    """这些用例只测「板块方向/量化证据」降级逻辑，避免真实盘中数据（网络/交易日相关）
    偶发触发 reversal/pullback 分支导致断言随机失败。"""
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


def _request(*, sector_name: str = "半导体") -> AnalysisRequest:
    profile = InvestorProfile(
        max_drawdown_percent=15,
        # 本文件验证证据守卫；默认关闭集中度干扰，专门用例会显式调低上限。
        concentration_limit_percent=100,
        expected_investment_amount=100000,
        avoid_chasing=False,
    )
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name=sector_name,
            holding_amount=10000,
        )
    ]
    return AnalysisRequest(holdings=holdings, profile=profile)


def _risk() -> RiskAssessment:
    return RiskAssessment(level="medium", weighted_return_percent=1.2, suggested_action="watch", alerts=[])


def _rec(**overrides) -> FundRecommendation:
    base = {
        "fund_code": "519674",
        "fund_name": "银河创新成长",
        "action": "分批加仓",
    }
    base.update(overrides)
    return FundRecommendation(**base)


def _facts_with_holding(sector_opportunity=None, evidence=None) -> dict:
    row = {"fund_code": "519674"}
    if sector_opportunity is not None:
        row["sector_opportunity"] = sector_opportunity
    if evidence is not None:
        row["evidence"] = evidence
    return {"holdings": [row]}


#: A（2026-08-12）之后基金侧证据弱**不再单独**拦加仓，所以要验证它的措辞，必须同时给出
#: 一个板块侧弱项——那才是这句话在生产里真正出现的场景。用 `opportunity_available=False`
#: 是最小的板块侧弱项。
_SECTOR_WEAK = {"opportunity_available": False}


@pytest.mark.parametrize(
    "components",
    [
        None,
        [],
        [{"source": "signal", "level": "低"}],
        [{"source": "factor"}],
        [None, "invalid", {"source": "risk", "level": "不足"}],
    ],
)
def test_weak_composite_without_factor_component_reports_missing_ic_coverage(
    components,
) -> None:
    evidence = {"composite": {"level": "低"}}
    if components is not None:
        evidence["components"] = components

    reasons = _weak_evidence_reasons(_SECTOR_WEAK, evidence)

    assert "IC 回测未覆盖，现有量化证据置信偏低" in reasons
    assert "量化证据背书弱" not in reasons


def test_weak_composite_with_factor_component_retains_weak_evidence_reason() -> None:
    reasons = _weak_evidence_reasons(
        _SECTOR_WEAK,
        {
            "composite": {"level": "不足"},
            "components": [
                "invalid",
                {"source": "factor", "level": "低", "basis": "主因子动量·IC偏弱"},
                {"source": "risk", "level": "中"},
            ],
        },
    )

    assert "量化证据背书弱" in reasons
    assert "IC 回测未覆盖，现有量化证据置信偏低" not in reasons


def test_fund_side_weakness_alone_no_longer_blocks_an_add() -> None:
    """A 的本体：板块侧全部站得住时，基金侧证据弱只降档、不再降为观察。

    这正是 011036 的生产形态——`entry_state=ready_to_start`、`confidence=高`、
    `opportunity_available=True`，只有基金侧那个 peer_group 常量在拦。
    """
    sector_ok = {
        "opportunity_available": True,
        "confidence": "高",
        "entry_state": "ready_to_start",
    }
    evidence = {
        "composite": {"level": "低"},
        "components": [{"source": "factor", "level": "低", "basis": "主因子动量·IC偏弱"}],
    }

    assert _weak_evidence_reasons(sector_ok, evidence) == []


def test_sector_side_weakness_still_blocks_and_lists_the_fund_reason() -> None:
    """不对称是刻意的：板块侧弱项照旧无条件拦，并把基金侧弱项作为补充列出。"""
    reasons = _weak_evidence_reasons(
        {"opportunity_available": True, "confidence": "高", "entry_state": "forming"},
        {
            "composite": {"level": "低"},
            "components": [{"source": "factor", "level": "低", "basis": "主因子动量·IC偏弱"}],
        },
    )

    assert "板块方向条件仍在形成中" in reasons
    assert "量化证据背书弱" in reasons


def test_full_guard_ignores_non_dict_evidence_components() -> None:
    facts = _facts_with_holding(
        evidence={
            "composite": {"level": "低"},
            "components": [None, "invalid", {"source": "risk", "level": "低"}],
        }
    )

    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )

    assert guarded[0].action == "观察"
    assert any("IC 回测未覆盖，现有量化证据置信偏低" in point for point in guarded[0].points)


@pytest.mark.parametrize(
    ("ic_state", "weak_reason", "participation_note", "component_count"),
    [
        (
            "unavailable",
            "IC 回测未接入，现有非 IC 证据置信偏低",
            "IC 回测未接入，IC 未参与本次结论",
            1,
        ),
        (
            "stale",
            "IC 回测已过期，现有非 IC 证据置信偏低",
            "IC 回测已过期，IC 未参与本次结论",
            1,
        ),
        ("available", "量化证据背书弱", None, 2),
    ],
)
def test_top_level_ic_status_controls_public_evidence_wording(
    ic_state: str,
    weak_reason: str,
    participation_note: str | None,
    component_count: int,
) -> None:
    facts = _facts_with_holding(
        evidence={
            "composite": {"level": "低"},
            "components": [
                {"source": "factor", "level": "低", "basis": "主因子动量·IC偏弱"},
                {"source": "signal", "level": "低", "basis": "板块信号样本偏弱"},
            ],
        }
    )
    facts["factor_scores"] = {"ic_status": {"state": ic_state}}

    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )

    rec = guarded[0]
    public_text = "\n".join(
        [*rec.points, rec.decision_path, *rec.fund_evidence, *rec.validation_notes]
    )
    assert rec.action == "观察"
    assert weak_reason in rec.points[0]
    assert f"{component_count}路已参与量化证据综合置信" in rec.decision_path
    assert any(
        f"{component_count}路已参与量化证据综合置信" in item
        for item in rec.fund_evidence
    )
    assert "三路量化证据" not in public_text
    assert weak_reason in rec.validation_notes

    if participation_note is None:
        assert "主因子动量·IC偏弱" in rec.fund_evidence
    else:
        assert participation_note in rec.decision_path
        assert participation_note in rec.fund_evidence
        assert participation_note in rec.validation_notes
        assert "主因子动量·IC偏弱" not in rec.fund_evidence
        assert "量化证据背书弱" not in public_text


@pytest.mark.parametrize(
    ("ic_state", "weak_reason", "participation_note"),
    [
        (
            "unavailable",
            "IC 回测未接入，现有非 IC 证据置信偏低",
            "IC 回测未接入，IC 未参与本次结论",
        ),
        (
            "stale",
            "IC 回测已过期，现有非 IC 证据置信偏低",
            "IC 回测已过期，IC 未参与本次结论",
        ),
    ],
)
def test_prefilled_model_fields_are_sanitized_and_ic_notes_deduplicated(
    ic_state: str,
    weak_reason: str,
    participation_note: str,
) -> None:
    factor_basis = "主因子动量·IC偏弱"
    signal_basis = "板块信号样本偏弱"
    facts = _facts_with_holding(
        evidence={
            "composite": {"level": "低"},
            "components": [
                {"source": "factor", "level": "低", "basis": factor_basis},
                {"source": "signal", "level": "低", "basis": signal_basis},
            ],
        }
    )
    facts["factor_scores"] = {"ic_status": {"state": ic_state}}
    model_rec = _rec(
        points=["三路量化证据综合置信偏低，量化证据背书弱。"],
        decision_path=(
            f"三路量化证据综合置信低，量化背书弱，{factor_basis}，"
            f"动作定为分批加仓；{participation_note}；{participation_note}。"
        ),
        fund_evidence=[
            "三路量化证据综合置信：低",
            factor_basis,
            signal_basis,
            participation_note,
            participation_note,
        ],
        validation_notes=[
            "量化证据背书弱",
            factor_basis,
            participation_note,
            participation_note,
            "模型补充备注",
        ],
    )

    _, guarded = apply_recommendation_guards(
        [model_rec],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )

    rec = guarded[0]
    public_text = "\n".join(
        [*rec.points, rec.decision_path, *rec.fund_evidence, *rec.validation_notes]
    )
    assert rec.action == "观察"
    assert "观察" in rec.decision_path
    assert "分批加仓" not in rec.decision_path
    assert "三路量化证据" not in public_text
    assert "量化证据背书弱" not in public_text
    assert "量化背书弱" not in public_text
    assert factor_basis not in public_text
    assert "1路已参与量化证据" in public_text
    assert weak_reason in public_text
    assert signal_basis in rec.fund_evidence
    assert rec.decision_path.count(participation_note) == 1
    assert rec.fund_evidence.count(participation_note) == 1
    assert rec.validation_notes.count(participation_note) == 1
    assert rec.validation_notes[:2] == [weak_reason, participation_note]
    assert "模型补充备注" in rec.validation_notes


def test_available_ic_with_malformed_factor_uses_uncovered_wording() -> None:
    facts = _facts_with_holding(
        evidence={
            "composite": {"level": "不足"},
            "components": [
                {"source": "factor"},
                {"source": "signal", "level": "不足", "basis": "板块样本不足"},
            ],
        }
    )
    facts["factor_scores"] = {"ic_status": {"state": "available"}}

    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )

    rec = guarded[0]
    public_text = "\n".join(
        [*rec.points, rec.decision_path, *rec.fund_evidence, *rec.validation_notes]
    )
    assert "IC 回测未覆盖，现有量化证据置信偏低" in rec.points[0]
    assert "1路已参与量化证据综合置信" in rec.decision_path
    assert "IC 回测未覆盖，IC 未参与本次结论" in public_text
    assert "量化证据背书弱" not in public_text


def test_weak_sector_opportunity_downgrades_add_action() -> None:
    facts = _facts_with_holding(
        sector_opportunity={
            "track": "momentum",
            "confidence": "低",
            "opportunity_available": False,
            "pattern_label": "distribution",
        }
    )
    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "观察"
    assert any("证据不足" in point for point in rec.points)


def test_strong_evidence_keeps_add_action_and_backfills_fields() -> None:
    facts = _facts_with_holding(
        sector_opportunity={
            "track": "momentum",
            "confidence": "高",
            "opportunity_available": True,
            "pattern_label": "price_flow_aligned_up",
            "today_main_force_net_yi": 6.0,
            "cumulative_5d_net_yi": 12.0,
            "evidence": ["今日主力净流入"],
        },
        evidence={
            "composite": {"level": "高", "score": 3.0},
            "components": [{"source": "factor", "level": "高", "basis": "主因子动量(百分位80)"}],
            "summary": "主因子动量(百分位80)",
        },
    )
    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "分批加仓"
    assert rec.decision_path
    assert "半导体" in rec.decision_path
    assert rec.sector_evidence
    assert rec.fund_evidence
    assert rec.risks


@pytest.mark.parametrize(
    ("score", "expected_percent"),
    [
        (49.99, 5.0),
        (50.0, 10.0),
        (70.0, 15.0),
        (85.0, 20.0),
        (999.0, 20.0),
    ],
)
def test_add_percentage_tracks_opportunity_score_without_style_cap(
    score: float,
    expected_percent: float,
) -> None:
    facts = _facts_with_holding(
        sector_opportunity={
            "score": score,
            "track": "momentum",
            "confidence": "高",
            "opportunity_available": True,
            "pattern_label": "price_flow_aligned_up",
        },
        evidence={
            "composite": {"level": "高", "score": 3.0},
            "components": [
                {"source": "factor", "level": "高", "basis": "主因子动量"}
            ],
        },
    )

    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )

    rec = guarded[0]
    assert rec.action == "分批加仓"
    assert rec.suggested_position_change_percent == expected_percent
    assert rec.estimated_position_change_amount_yuan == pytest.approx(
        10_000 * expected_percent / 100
    )
    assert "板块机会分" in rec.suggested_position_change_basis


def _strong_fund_evidence() -> dict:
    """基金自身正向量化支持为「高」——用满板块档位的前置条件。

    这些用例考察的是板块档位选择本身，所以显式给出强证据，把「基金侧下调一级」这条
    独立维度隔离出去（由 `test_fund_evidence_steps_the_sector_tier_down` 覆盖）。
    """
    return {"composite": {"level": "高", "score": 3.0}}


def _usable_medium_fund_evidence() -> dict:
    """「证据**可用**但偏弱」：可靠性放行（中），因此降一档是真的"基金更弱"。

    必须带 `components[].reliability.usable=True`——`_fund_evidence_is_usable` 认的是
    这个标记，不是 `composite.level`。只写 `composite.level=中` 在新口径下是构造不出来的
    数据（中档只可能由一条可靠性放行的分量产生），也正是本次修复要区分的那条边界。
    """
    return {
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
    }


def _unusable_reliability_fund_evidence() -> dict:
    """线上当前的真实形态：因子分量在，但同类 IC 不可靠 → 该路没有产出可用结论。"""
    return {
        "composite": {"level": "不足", "score": 0},
        "components": [
            {
                "source": "factor",
                "role": "return_signal",
                "level": "不足",
                "direction": "unknown",
                "reliability": {
                    "level": "低",
                    "scope": "peer_group",
                    "usable": False,
                    "basis": "指数基金未来20日 IC +0.043，样本外/区间稳定性不足",
                },
            }
        ],
    }


def test_conservative_profile_does_not_cap_opportunity_percentage() -> None:
    request = _request()

    percent, basis, note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity={"score": 85},
        evidence=_strong_fund_evidence(),
    )

    assert percent == 20
    assert "强机会档 20%" in basis
    assert note is None


@pytest.mark.parametrize(
    ("sector_opportunity", "expected_percent"),
    [
        (None, 5.0),
        ({}, 5.0),
        ({"score": 85.0, "research_score": 49.99}, 5.0),
        ({"score": 85.0, "research_score": 70.0}, 15.0),
    ],
)
def test_add_percentage_prefers_research_score_and_falls_back_safely(
    sector_opportunity: dict | None,
    expected_percent: float,
) -> None:
    request = _request()

    percent, _, note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity=sector_opportunity,
        evidence=_strong_fund_evidence(),
    )

    assert percent == expected_percent
    assert note is None


@pytest.mark.parametrize(
    ("evidence", "expected_percent", "expected_basis_fragment"),
    [
        pytest.param(_strong_fund_evidence(), 20.0, None, id="high_keeps_sector_tier"),
        pytest.param(
            _usable_medium_fund_evidence(),
            15.0,
            "基金自身正向量化支持中，档位下调至 15%",
            id="usable_medium_steps_down_one_tier",
        ),
        # 以下三种都属于「这一路没有产出可用结论」，按本仓既有原则
        # （证据缺失 ≠ 基金更弱）**不动档位**。此前三者都降一档，而因子可靠性在
        # current_survivors cohort 下天花板只有「中」、这里要求「高」才满档，
        # 于是变成对所有持仓恒定降一档——一个不携带信息的全局保守系数。
        pytest.param(None, 20.0, None, id="missing_evidence_keeps_tier"),
        pytest.param(
            {"composite": {"level": "不足", "score": 0}},
            20.0,
            None,
            id="insufficient_keeps_tier",
        ),
        pytest.param(
            _unusable_reliability_fund_evidence(),
            20.0,
            None,
            id="unusable_reliability_keeps_tier",
        ),
    ],
)
def test_fund_evidence_steps_the_sector_tier_down(
    evidence: dict | None,
    expected_percent: float,
    expected_basis_fragment: str | None,
) -> None:
    """同一板块档位下，基金自身证据决定是否降一级——这是此前完全缺失的区分维度。"""
    request = _request()

    percent, basis, note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity={"research_score": 90.0},
        evidence=evidence,
    )

    assert percent == expected_percent
    assert note is None
    # 板块依据始终保留，便于用户看出"档位从哪来、又为什么被调低"。
    assert "强机会档 20%" in basis
    if expected_basis_fragment is None:
        assert "档位下调" not in basis
    else:
        assert expected_basis_fragment in basis


def test_fund_evidence_never_raises_the_sector_tier() -> None:
    """量化证据只能增加置信度，不得作为提额依据：最低档不会因证据强而上调。"""
    request = _request()

    percent, _basis, _note = _resolve_deterministic_position_change(
        "分批加仓",
        holding=request.holdings[0],
        profile=request.profile,
        weight_denominator=100_000,
        sector_opportunity={"research_score": 10.0},
        evidence=_strong_fund_evidence(),
    )

    assert percent == 5.0


def test_unconfirmed_share_ledger_keeps_direction_and_uses_estimated_percentage() -> None:
    facts = _facts_with_holding(
        sector_opportunity={
            "score": 80,
            "track": "momentum",
            "confidence": "高",
            "opportunity_available": True,
            "pattern_label": "price_flow_aligned_up",
            "today_main_force_net_yi": 6.0,
            "cumulative_5d_net_yi": 12.0,
        },
        evidence={
            "composite": {"level": "高", "score": 3.0},
            "components": [
                {"source": "factor", "level": "高", "basis": "主因子动量(百分位80)"}
            ],
        },
    )
    facts["portfolio_snapshot"] = {
        "stale": False,
        "authoritative": True,
        "position_complete": False,
        "pending_transaction_count": 0,
    }
    facts["data_evidence"] = {
        "decision_ready": False,
        "blocking_reasons": ["incomplete_or_unsettled_position_ledger"],
        "items": [
            {
                "fact_id": "holdings.519674.holding_amount",
                "freshness": "fresh",
                "confidence": "high",
            },
            {
                "fact_id": "holdings.519674.sector_opportunity",
                "freshness": "fresh",
                "confidence": "high",
            },
        ],
    }

    _, guarded = apply_recommendation_guards(
        [
            _rec(
                amount_yuan=1000,
                suggested_position_change_percent=10,
                confidence="高",
            )
        ],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )

    rec = guarded[0]
    assert rec.action == "分批加仓"
    assert rec.amount_yuan is None
    assert rec.amount_note is None
    assert rec.suggested_position_change_percent == 15
    assert rec.estimated_position_change_amount_yuan == 1500
    assert "相对当前估算持仓" in rec.suggested_position_change_basis
    assert rec.confidence == "高"
    assert facts["data_evidence_guard"]["execution_blocked"] is False
    assert "sizing_blocked" not in facts["data_evidence_guard"]
    assert all("等数据更新后再判断" not in point for point in rec.points)


def test_missing_facts_row_does_not_crash_and_still_backfills_generic_fields() -> None:
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=None,
    )
    rec = guarded[0]
    assert rec.decision_path
    assert rec.confidence == "中"


def test_confidence_is_normalized_to_known_labels() -> None:
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察", confidence="非常高")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=None,
    )
    assert guarded[0].confidence == "中"


def test_humanizes_internal_field_names_in_llm_provided_decision_path() -> None:
    rec = _rec(
        action="观察",
        decision_path="板块 track=momentum confidence=高，fund_quality_score=61.5",
    )
    _, guarded = apply_recommendation_guards(
        [rec],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=None,
    )
    text = guarded[0].decision_path
    assert "track=" not in text
    assert "fund_quality_score=" not in text
    assert "顺势观察" in text
    assert "基金质量分 61.5" in text


# --- M2: 双向 guard 升级（resolve_escalation_floor 接入 apply_recommendation_guards） ----


def _strong_divergence_opportunity(**overrides) -> dict:
    base = {
        "track": "momentum",
        "confidence": "高",  # M1.4 修复后，量价背离显著时才会出现「高」
        "opportunity_available": False,
        "pattern_label": "distribution",
        "penalties": ["资金背离或持续流出"],
    }
    base.update(overrides)
    return base


def test_llm_watch_gets_upgraded_to_pause_when_divergence_strong_and_evidence_ok() -> None:
    """本次升级要修的核心场景：LLM 本来就给"观察"（不是"分批加仓"），旧的单向 guard
    完全不会动它；新的双向 guard 在量价背离显著证据下应把它上调为更保守的动作。"""
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(),
        evidence={"composite": {"level": "高", "score": 3.0}},
    )
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "暂停追涨"
    assert any("上调" in point for point in rec.points)


def test_llm_watch_gets_upgraded_to_reduce_when_fund_evidence_also_weak() -> None:
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(),
        evidence={"composite": {"level": "不足", "score": 0.5}},
    )
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "减仓评估"
    assert rec.suggested_position_change_percent == -25.0
    assert rec.suggested_position_change_basis


def test_risk_enhanced_reduction_uses_one_third_and_matching_estimate() -> None:
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(),
        evidence={"composite": {"level": "不足", "score": 0.5}},
    )
    facts["holdings"][0]["estimated_holding_return_percent"] = 8.0

    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )

    rec = guarded[0]
    assert rec.action == "减仓评估"
    assert rec.suggested_position_change_percent == pytest.approx(-(100 / 3))
    assert rec.estimated_position_change_amount_yuan == 3333.33


def test_llm_add_action_gets_upgraded_past_the_normal_downgrade_to_reduce() -> None:
    """LLM 给"分批加仓"时，旧逻辑只会把它降到"观察"（弱证据分支）；新逻辑在证据
    极强时应继续往下拉到"减仓评估"，而不是停在"观察"就不动了。"""
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(),
        evidence={"composite": {"level": "低", "score": 1.0}},
    )
    _, guarded = apply_recommendation_guards(
        [_rec(action="分批加仓")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "减仓评估"


def test_escalation_does_not_downgrade_below_llm_action_when_evidence_is_fine() -> None:
    """证据不强（confidence 非「高」）时，不应触发额外升级——LLM 给"观察"应保持"观察"
    （除非其他既有 guard 分支介入，此用例特意避开那些分支）。"""
    facts = _facts_with_holding(
        sector_opportunity={
            "track": "setup",
            "confidence": "中",
            "opportunity_available": True,
            "pattern_label": "accumulation",
        },
        evidence={"composite": {"level": "中", "score": 2.0}},
    )
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "观察"
    assert rec.suggested_position_change_percent is None


def test_escalation_backfills_position_change_fields_only_when_triggered() -> None:
    """未触发升级时，suggested_position_change_percent/basis 保持模型默认值
    （不会被意外污染成非 None）。"""
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=None,
    )
    rec = guarded[0]
    assert rec.suggested_position_change_percent is None
    assert rec.suggested_position_change_basis == ""


def test_deep_reduce_action_produces_matching_default_risk_text() -> None:
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(penalties=[]),
        evidence={"composite": {"level": "不足", "score": 0.0}},
    )
    market_breadth = {
        "sentiment_level": "冰点",
        "sentiment_level_change": -2,
        "decision_eligible": True,
        "freshness_status": "fresh",
        "stale": False,
    }
    request = _request()
    # 手工构造真实组合市值口径下的集中度超限场景。
    request.profile.concentration_limit_percent = 5
    facts["market_breadth"] = market_breadth
    facts["holdings"][0]["over_concentration"] = True
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        request,
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "大幅减仓评估"
    assert any("恢复原仓位" in risk for risk in rec.risks)
