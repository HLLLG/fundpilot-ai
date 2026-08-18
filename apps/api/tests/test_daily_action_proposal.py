"""日报确定性动作提议，以及离线规则引擎改作全风格风险否决。

回归背景（两件事）：

1. **LLM 是决策来源**。`analyze_pipeline.run_analysis()` 全文没有任何决策计算，`action`
   完全由模型从 `allowed_actions` 里挑，`recommendation_guard` 只会一路往下压。于是系统
   永远不会比模型更果断：一个 `entry_state=ready_to_start`、资金参与度与价格位置都过线、
   量化证据为「高」、没有任何风险触发的持仓，只要模型写了「观察」，报告就是「观察」——
   而系统自己的规则本来支持加仓。荐基不是这样：白名单与金额都由确定性链路决定。

2. **离线规则对照的开关方向是反的**。`if offline is not None and not short_term` 让
   短线/激进风格完全跳过对照（可它们各有专门构建器、会输出真实风险意见），而稳健风格
   反被无意见默认值「观察」硬封顶（`conservative_action_text` 取 min，观察 bucket 低于
   分批加仓）。

锁的契约：
- 提议只在九个正向条件全部满足时才开加仓，任何一条不满足都退回观察/风控复核；
- 提议侧比 guard 严：方向成熟度层缺席时不得主动提议加仓；
- shadow 默认不改变最终动作，只留痕；enforced 才让提议成为动作链输入；
- 无论哪个模式，既有 clamp 链继续行使否决权；
- 离线规则的风险意见对**所有**风格生效，而它的无意见「观察」不再封顶。
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
from app.services import recommendation_guard as rg
from app.services.daily_action_proposal import (
    DAILY_ACTION_PROPOSAL_SCHEMA_VERSION,
    propose_daily_action,
)
from app.services.decision_guard_shared import (
    ACTION_BUCKET_ADD,
    ACTION_BUCKET_DEEP_REDUCE,
    ACTION_BUCKET_PAUSE,
    ACTION_BUCKET_REDUCE,
    ACTION_BUCKET_WATCH,
)
from app.services.recommendation_guard import (
    _offline_action_is_a_risk_veto,
    apply_recommendation_guards,
)
from app.services.sector_opportunity_scoring import ENTRY_POLICY_VERSION_V3

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


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


@pytest.fixture
def proposal_mode(monkeypatch):
    """切换 `daily_action_proposal_mode`，其余设置保持真实默认。"""

    def _set(mode: str) -> None:
        from app.config import get_settings

        real = get_settings()
        monkeypatch.setattr(
            "app.services.recommendation_guard.get_settings",
            lambda: real.model_copy(update={"daily_action_proposal_mode": mode}),
        )

    return _set


# --- 纯函数层：八个正向条件 --------------------------------------------------


def _supporting_kwargs(**overrides) -> dict:
    base = dict(
        risk_level="medium",
        risk_suggested_action="watch",
        escalation_min_bucket=None,
        max_allowed_bucket=ACTION_BUCKET_ADD,
        entry_state="ready_to_start",
        entry_state_block_reason=None,
        sector_absence_reason=None,
        opportunity_available=True,
        weak_evidence_reasons=(),
        reversal_blocked=False,
        execution_blocked=False,
    )
    base.update(overrides)
    return base


def test_all_positive_conditions_propose_an_add() -> None:
    proposal = propose_daily_action(**_supporting_kwargs())

    assert proposal.action == "分批加仓"
    assert proposal.supports_add is True
    assert proposal.blocked_add_reasons == ()
    assert proposal.bucket == ACTION_BUCKET_ADD
    assert proposal.to_dict()["schema_version"] == DAILY_ACTION_PROPOSAL_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("override", "expected_reason"),
    [
        ({"sector_absence_reason": "本轮板块方向证据未取到"}, "sector_direction_evidence_absent"),
        ({"entry_state": None}, "entry_state_unavailable"),
        ({"entry_state_block_reason": "板块方向条件仍在形成中"}, "entry_state_not_ready"),
        ({"opportunity_available": False}, "opportunity_unavailable"),
        ({"weak_evidence_reasons": ("板块方向置信偏低",)}, "weak_evidence"),
        ({"escalation_min_bucket": ACTION_BUCKET_PAUSE}, "risk_escalation_floor"),
        ({"max_allowed_bucket": ACTION_BUCKET_WATCH}, "risk_ceiling"),
        ({"reversal_blocked": True}, "reversal_or_pullback"),
    ],
)
def test_any_missing_condition_withholds_the_add(override: dict, expected_reason: str) -> None:
    proposal = propose_daily_action(**_supporting_kwargs(**override))

    assert proposal.supports_add is False
    assert proposal.action == "观察"
    assert expected_reason in proposal.blocked_add_reasons


def test_missing_entry_state_is_stricter_than_the_guard() -> None:
    """guard 对"成熟度子层缺席"不拦（旧机会分仍能回答方向问题），但缺席时没有任何东西
    能证明"现在可以开始买"，所以提议侧必须拒绝主动开仓。"""
    from app.services.recommendation_guard import _entry_state_add_block_reason

    row_without_maturity = {"track": "momentum", "confidence": "高"}
    assert _entry_state_add_block_reason(row_without_maturity) is None, "guard 侧不拦"

    proposal = propose_daily_action(**_supporting_kwargs(entry_state=None))
    assert proposal.supports_add is False
    assert "entry_state_unavailable" in proposal.blocked_add_reasons


def test_risk_escalation_is_translated_not_recomputed() -> None:
    proposal = propose_daily_action(
        **_supporting_kwargs(escalation_min_bucket=ACTION_BUCKET_DEEP_REDUCE)
    )

    assert proposal.action == "大幅减仓评估"
    assert proposal.reason_codes == ("risk_escalation",)
    assert proposal.supports_add is False


def test_blocked_data_evidence_outranks_everything() -> None:
    proposal = propose_daily_action(
        **_supporting_kwargs(execution_blocked=True, risk_level="high")
    )

    assert proposal.action == "风控复核"
    assert proposal.reason_codes == ("decision_evidence_not_ready",)


@pytest.mark.parametrize(
    ("risk_level", "suggested", "expected"),
    [
        ("high", "watch", "风控复核"),
        ("medium", "risk_review", "风控复核"),
        ("medium", "watch", "观察"),
    ],
)
def test_baseline_reflects_portfolio_risk(risk_level, suggested, expected) -> None:
    proposal = propose_daily_action(
        **_supporting_kwargs(
            risk_level=risk_level,
            risk_suggested_action=suggested,
            opportunity_available=False,
        )
    )
    assert proposal.action == expected


# --- 集成层：shadow vs enforced ---------------------------------------------


def _request(*, holding_amount: float = 10_000):
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=holding_amount,
            )
        ],
        profile=InvestorProfile(
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
            avoid_chasing=False,
        ),
    )


def _risk(level: str = "medium", suggested: str = "watch") -> RiskAssessment:
    return RiskAssessment(
        level=level,
        weighted_return_percent=1.2,
        suggested_action=suggested,
        alerts=[],
    )


def _supporting_facts() -> dict:
    return {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": {
                    "sector_label": "半导体",
                    "score_policy_version": ENTRY_POLICY_VERSION_V3,
                    "direction_score": 76.5,
                    "entry_state": "ready_to_start",
                    "raw_entry_state": "ready_to_start",
                    "opportunity_available": True,
                    "confidence": "高",
                    "track": "momentum",
                    # `describe_sector_opportunity` 对每个 V3 行都会写这个键；`None` 的语义是
                    # "本轮没有任何入场通道授权投入"，`_first_tranche_scaled_percent` 因此对
                    # V3 行 fail-closed。1.0 = 授权满额、不缩放，让本文件只测提议与提升逻辑。
                    "first_tranche_scale": 1.0,
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


def _guard(facts, *, llm_action="观察", request=None, risk=None):
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action=llm_action,
            )
        ],
        [],
        request or _request(),
        risk or _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    return guarded[0]


def test_shadow_mode_keeps_the_model_draft_but_records_the_divergence(
    proposal_mode,
) -> None:
    proposal_mode("shadow")
    facts = _supporting_facts()

    rec = _guard(facts, llm_action="观察")

    assert rec.action == "观察", "shadow 不得改变最终动作"
    assert rec.suggested_position_change_percent is None
    audit = facts["daily_action_proposal"]
    assert audit["mode"] == "shadow"
    assert audit["divergence_count"] == 1
    assert audit["by_fund"][0]["action"] == "分批加仓"
    assert audit["by_fund"][0]["llm_action"] == "观察"
    assert audit["by_fund"][0]["final_action"] == "观察"
    assert any("动作提议灰度中，未生效" in note for note in rec.validation_notes)


def test_enforced_mode_lets_the_system_open_an_add_the_model_withheld(
    proposal_mode,
) -> None:
    """这是本次要修的核心场景：系统规则支持加仓，模型写了观察。"""
    proposal_mode("enforced")
    facts = _supporting_facts()

    rec = _guard(facts, llm_action="观察")

    assert rec.action == "分批加仓"
    # 比例仍由任务 3 的标定档位算，不是模型给的。
    assert rec.suggested_position_change_percent == 20.0
    assert facts["daily_action_proposal"]["mode"] == "enforced"
    assert facts["daily_action_proposal"]["by_fund"][0]["final_action"] == "分批加仓"
    assert any("以系统提议为准" in point for point in rec.points)


def test_enforced_mode_still_lets_every_existing_gate_veto(proposal_mode) -> None:
    """提议只是动作链的**输入**：既有 clamp 链一个字没改，仍然能否掉它。"""
    proposal_mode("enforced")
    facts = _supporting_facts()
    facts["holdings"][0]["sector_opportunity"]["opportunity_available"] = False

    rec = _guard(facts, llm_action="分批加仓")

    # 具体落到「观察」还是「暂停追涨」由 escalation 档位决定；这里要锁的是加仓被否掉。
    assert rg._action_bucket(rec.action) < ACTION_BUCKET_ADD
    assert facts["daily_action_proposal"]["by_fund"][0]["supports_add"] is False
    assert "opportunity_unavailable" in (
        facts["daily_action_proposal"]["by_fund"][0]["blocked_add_reasons"]
    )


def test_enforced_mode_respects_allowed_actions(proposal_mode) -> None:
    """动作词表仍是硬约束：提议不在表内时必须降为观察。"""
    proposal_mode("enforced")
    facts = _supporting_facts()
    facts["allowed_actions"] = ["观察", "风控复核"]

    rec = _guard(facts, llm_action="观察")

    assert rec.action == "观察"


def test_enforced_mode_respects_the_risk_ceiling(proposal_mode) -> None:
    proposal_mode("enforced")
    facts = _supporting_facts()

    rec = _guard(facts, llm_action="观察", risk=_risk(level="high"))

    assert rec.action != "分批加仓"


def test_enforced_mode_does_not_touch_a_matching_draft(proposal_mode) -> None:
    """提议与草案一致时不该产生任何分歧提示。"""
    proposal_mode("enforced")
    facts = _supporting_facts()

    rec = _guard(facts, llm_action="分批加仓")

    assert rec.action == "分批加仓"
    assert facts["daily_action_proposal"]["divergence_count"] == 0
    assert all("系统提议" not in note for note in rec.validation_notes)


def test_enforced_mode_never_relaxes_a_risk_escalation(proposal_mode) -> None:
    """风险升级方向不受影响：提议复用同一份 escalation 判定，不会把减仓改回观察。"""
    proposal_mode("enforced")
    facts = {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": {
                    "track": "momentum",
                    "confidence": "高",
                    "opportunity_available": False,
                    "pattern_label": "distribution",
                    "penalties": ["资金背离或持续流出"],
                },
                "evidence": {"composite": {"level": "不足", "score": 0.5}},
            }
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }

    rec = _guard(facts, llm_action="分批加仓")

    assert rec.action == "减仓评估"
    assert (
        facts["daily_action_proposal"]["by_fund"][0]["reason_codes"] == ["risk_escalation"]
    )


# --- 离线规则引擎：全风格风险否决，无意见不封顶 -----------------------------


@pytest.mark.parametrize(
    ("offline_action", "is_veto"),
    [
        ("观察", False),
        ("暂停追涨", True),
        ("减仓评估", True),
        ("大幅减仓评估", True),
        ("清仓评估", True),
        ("风控复核", True),
        ("分批加仓", True),
    ],
)
def test_only_a_triggered_offline_action_is_a_veto(offline_action, is_veto) -> None:
    """「观察」是离线引擎的无意见默认值，不该当结论用。"""
    assert _offline_action_is_a_risk_veto(offline_action) is is_veto


def test_offline_watch_no_longer_caps_the_add(proposal_mode, monkeypatch) -> None:
    """稳健风格此前被无意见的「观察」硬封顶，加仓永远出不来。"""
    proposal_mode("enforced")
    facts = _supporting_facts()

    monkeypatch.setattr(
        "app.services.recommendation_guard.build_offline_fund_recommendation",
        lambda *_args, **_kwargs: FundRecommendation(
            fund_code="519674", fund_name="银河创新成长", action="观察"
        ),
    )

    rec = _guard(facts, llm_action="观察", request=_request())

    assert rec.action == "分批加仓"


def test_offline_risk_opinion_vetoes_the_llm_add(proposal_mode, monkeypatch) -> None:
    """离线规则引擎给出的风险意见必须能否决模型草案的加仓。"""
    proposal_mode("enforced")
    facts = _supporting_facts()

    monkeypatch.setattr(
        "app.services.recommendation_guard.build_offline_fund_recommendation",
        lambda *_args, **_kwargs: FundRecommendation(
            fund_code="519674", fund_name="银河创新成长", action="减仓评估"
        ),
    )

    rec = _guard(facts, llm_action="分批加仓", request=_request())

    assert rec.action == "减仓评估", "离线风险否决必须生效"
    assert rec.suggested_position_change_percent == -25.0


def test_offline_veto_cannot_make_the_action_more_aggressive(
    proposal_mode, monkeypatch
) -> None:
    """对照取的是 min：离线给「分批加仓」不得把系统的观察抬成加仓。"""
    proposal_mode("enforced")
    facts = _supporting_facts()
    facts["holdings"][0]["sector_opportunity"]["entry_state"] = "forming"

    monkeypatch.setattr(
        "app.services.recommendation_guard.build_offline_fund_recommendation",
        lambda *_args, **_kwargs: FundRecommendation(
            fund_code="519674", fund_name="银河创新成长", action="分批加仓"
        ),
    )

    rec = _guard(facts, llm_action="观察")

    assert rec.action != "分批加仓"


def test_proposal_module_does_not_import_the_guard() -> None:
    """提议不得反向依赖 guard：门禁只有一处实现，提议只消费它的结论。"""
    from pathlib import Path

    from app.services import daily_action_proposal

    source = Path(daily_action_proposal.__file__).read_text(encoding="utf-8")
    assert "import recommendation_guard" not in source
    assert "from app.services.recommendation_guard" not in source


# --- 提升是收口的：绝不放松风险结论、绝不吞掉解释 ---------------------------
#
# 这两条是第一版实现真实踩过的坑：当时把提议直接当作动作链的输入，结果
#   (1) 模型提出的「减仓评估」被提议的中性基线放松成「观察」；
#   (2) `>= ACTION_BUCKET_ADD` 的 clamp 分支不再触发，"为什么不能加仓"的解释全部消失。
# 现在提议只在链尾对被动动作做一次提升，两条都被下面的用例锁住。


@pytest.mark.parametrize(
    "llm_action",
    ["减仓评估", "大幅减仓评估", "清仓评估", "风控复核"],
)
def test_enforced_mode_never_relaxes_a_model_proposed_risk_action(
    proposal_mode, llm_action: str
) -> None:
    """系统可以比模型更果断地买，但不能比模型更轻率地放松风险结论。"""
    proposal_mode("enforced")
    facts = _supporting_facts()
    facts["allowed_actions"] = [
        "观察",
        "暂停追涨",
        "分批加仓",
        "减仓评估",
        "大幅减仓评估",
        "清仓评估",
        "风控复核",
    ]

    rec = _guard(facts, llm_action=llm_action)

    assert rec.action == llm_action
    assert rg._action_bucket(rec.action) < ACTION_BUCKET_WATCH or rec.action == "风控复核"
    assert all("以系统提议为准" not in point for point in rec.points)


def test_enforced_mode_keeps_the_clamp_explanation_when_the_add_is_refused(
    proposal_mode,
) -> None:
    """模型要加仓、证据不支持时，"为什么不能加"这句解释必须还在。"""
    proposal_mode("enforced")
    facts = _supporting_facts()
    facts["holdings"][0]["sector_opportunity"]["confidence"] = "低"
    facts["holdings"][0]["sector_opportunity"]["entry_state"] = "forming"

    rec = _guard(facts, llm_action="分批加仓")

    assert rec.action != "分批加仓"
    assert any("证据不足" in point for point in rec.points)


@pytest.mark.parametrize("start_action", ["观察", "暂停追涨"])
def test_only_passive_actions_are_promotable(proposal_mode, start_action: str) -> None:
    proposal_mode("enforced")
    facts = _supporting_facts()

    rec = _guard(facts, llm_action=start_action)

    assert rec.action == "分批加仓"


def test_promotion_respects_the_offline_risk_veto(proposal_mode, monkeypatch) -> None:
    """离线规则给出的非默认动作是它真的触发了条件，比加仓保守时不得被提升覆盖。"""
    proposal_mode("enforced")
    facts = _supporting_facts()

    monkeypatch.setattr(
        "app.services.recommendation_guard.build_offline_fund_recommendation",
        lambda *_args, **_kwargs: FundRecommendation(
            fund_code="519674", fund_name="银河创新成长", action="暂停追涨"
        ),
    )

    rec = _guard(facts, llm_action="观察")

    assert rec.action != "分批加仓"


def test_promotion_is_a_noop_without_a_supporting_proposal(proposal_mode) -> None:
    proposal_mode("enforced")
    facts = _supporting_facts()
    facts["holdings"][0]["sector_opportunity"]["entry_state"] = "forming"

    rec = _guard(facts, llm_action="观察")

    assert rec.action == "观察"
    assert all("以系统提议为准" not in point for point in rec.points)
