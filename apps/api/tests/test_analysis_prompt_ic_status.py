from __future__ import annotations

from app.models import InvestorProfile, RiskAssessment
from app.services import analysis_prompt
from app.services.analysis_facts import build_analysis_facts


def _ic_instruction() -> str:
    instruction = getattr(analysis_prompt, "IC_EVIDENCE_INSTRUCTION", None)
    assert isinstance(instruction, str)
    assert instruction
    return instruction


def test_ic_instruction_distinguishes_available_unavailable_and_stale_states() -> None:
    instruction = _ic_instruction()

    assert "factor_scores.ic_status.state" in instruction
    assert "仅当 `available`" in instruction
    assert "factor_reliability" in instruction
    assert "IC 回测未接入，IC 未参与本次结论" in instruction
    assert "IC 回测已过期，IC 未参与本次结论" in instruction
    assert "不得称为「量化背书弱」" in instruction


def test_default_role_prompt_uses_the_shared_ic_instruction() -> None:
    instruction = _ic_instruction()

    assert analysis_prompt.DEFAULT_ROLE_PROMPT.count(instruction) == 1
    assert "因子分（`factor_scores`）须按 `factor_reliability` 各因子置信使用" not in (
        analysis_prompt.DEFAULT_ROLE_PROMPT
    )


def test_analysis_facts_persist_shared_ic_instruction_before_composite_guidance() -> None:
    instruction = _ic_instruction()
    facts = build_analysis_facts(
        [],
        RiskAssessment(
            level="medium",
            weighted_return_percent=0,
            suggested_action="watch",
            alerts=[],
        ),
        [],
        InvestorProfile(),
        session={"effective_trade_date": "2026-07-11"},
    )

    persisted = facts["instruction"]
    ic_end = persisted.index(instruction) + len(instruction)
    composite_start = persisted.index("持仓的 evidence.composite")
    assert persisted.count(instruction) == 1
    assert persisted[ic_end:composite_start] == ""
