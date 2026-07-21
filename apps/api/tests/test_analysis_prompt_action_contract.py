from __future__ import annotations

import pytest

from app.config import refresh_settings
from app.models import AnalysisRequest, Holding, RiskAssessment
from app.services.analysis_runtime import AnalysisRuntime
from app.services.analysis_facts import build_allowed_actions
from app.services.analysis_payload import OUTPUT_REQUIREMENTS_SYSTEM
from app.services.analysis_prompt import DEFAULT_ROLE_PROMPT
from app.services.decision_guard_shared import (
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
)
from app.services.deepseek_client import FETCH_MARKET_NEWS_TOOL, _system_prompt
from app.services.discovery_client import DiscoveryClient
from app.services.recommendation_guard import normalize_action_text


BASE_ACTIONS = ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("不加仓", "暂停追涨"),
        ("暂不加仓", "暂停追涨"),
        ("禁止买入", "暂停追涨"),
        ("不建议加仓", "暂停追涨"),
        ("停止定投", "暂停追涨"),
        ("不要分批买入", "暂停追涨"),
        ("不减仓", "观察"),
        ("暂不减仓", "观察"),
        ("禁止减仓", "观察"),
        ("不清仓", "观察"),
    ],
)
def test_negated_action_phrases_never_reverse_into_an_executable_trade(
    raw: str, expected: str
) -> None:
    assert normalize_action_text(raw) == expected


def test_prompt_contract_uses_session_and_allowed_actions_without_fixed_clock_or_count() -> None:
    tool_description = FETCH_MARKET_NEWS_TOOL["function"]["description"]

    for text in (DEFAULT_ROLE_PROMPT, OUTPUT_REQUIREMENTS_SYSTEM, tool_description):
        assert "14:30" not in text
        assert "15:00" not in text
        assert "五选一" not in text

    assert "analysis_facts.session" in DEFAULT_ROLE_PROMPT
    assert "allowed_actions" in DEFAULT_ROLE_PROMPT
    assert "唯一合法" in OUTPUT_REQUIREMENTS_SYSTEM
    assert "amount_yuan 必须始终为 null" in OUTPUT_REQUIREMENTS_SYSTEM
    assert "suggested_position_change_percent 由服务端确定性规则生成" in OUTPUT_REQUIREMENTS_SYSTEM
    assert "不阻断百分比方向建议" in OUTPUT_REQUIREMENTS_SYSTEM


def test_daily_and_discovery_system_prompts_share_the_session_clock() -> None:
    session = {
        "calendar_date": "2026-07-14",
        "local_datetime": "2026-07-14 00:05",
        "session_kind": "non_trading_day",
    }

    daily = _system_prompt(False, session=session)
    discovery = DiscoveryClient()._system_prompt(False, session=session)

    assert "2026-07-14 00:05" in daily
    assert "2026-07-14 00:05" in discovery


def test_build_allowed_actions_returns_only_base_actions_without_escalation() -> None:
    per_fund = [{"fund_code": "000001", "escalation": {"min_bucket": None}}]

    assert build_allowed_actions(per_fund) == BASE_ACTIONS


def test_build_allowed_actions_exposes_enforced_escalation_actions(monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "enforced")
    refresh_settings()
    try:
        clear_all = [
            {
                "fund_code": "000001",
                "escalation": {"min_bucket": ACTION_BUCKET_CLEAR_ALL},
            }
        ]
        deep_reduce = [
            {
                "fund_code": "000001",
                "escalation": {"min_bucket": ACTION_BUCKET_DEEP_REDUCE},
            }
        ]

        assert build_allowed_actions(clear_all) == [
            *BASE_ACTIONS,
            "清仓评估",
            "大幅减仓评估",
        ]
        assert build_allowed_actions(deep_reduce) == [*BASE_ACTIONS, "大幅减仓评估"]
    finally:
        refresh_settings()


def test_build_allowed_actions_hides_escalation_extensions_in_shadow(monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "shadow")
    refresh_settings()
    try:
        per_fund = [
            {
                "fund_code": "000001",
                "escalation": {"min_bucket": ACTION_BUCKET_CLEAR_ALL},
            }
        ]
        assert build_allowed_actions(per_fund) == BASE_ACTIONS
    finally:
        refresh_settings()


def test_deep_judge_cannot_inject_an_action_missing_from_allowed_actions(
    monkeypatch,
) -> None:
    from app.services import report_judge

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "x" * 40)
    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "enforced")
    refresh_settings()
    request = AnalysisRequest(
        holdings=[Holding(fund_code="000001", fund_name="甲基金", holding_amount=1_000)]
    )
    risk = RiskAssessment(
        level="medium", suggested_action="watch", weighted_return_percent=0, alerts=[]
    )
    facts = {
        "holdings": [{"fund_code": "000001", "weight_percent": 10}],
        "allowed_actions": BASE_ACTIONS,
    }
    draft = {
        "title": "draft",
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "甲基金", "action": "观察"}
        ],
    }
    reviewed = {
        "title": "reviewed",
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "甲基金", "action": "清仓评估"}
        ],
    }
    monkeypatch.setattr(
        report_judge,
        "_llm_judge_with_budget",
        lambda *_args, **_kwargs: (reviewed, False),
    )
    runtime = AnalysisRuntime(
        mode="deep",
        model="test",
        news_enabled=False,
        news_max_topics=0,
        news_tool_max_rounds=0,
    )

    result, meta = report_judge.judge_parsed_report(
        draft, request, risk, [], runtime, facts=facts
    )

    assert meta["llm_judge_applied"] is True
    assert result["fund_recommendations"][0]["action"] == "观察"
