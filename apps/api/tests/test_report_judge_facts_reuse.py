"""F1 回归：judge_parsed_report 复用上游 facts，不再调用 build_analysis_facts。"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

from app.config import get_settings, refresh_settings
from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_runtime import resolve_analysis_runtime


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10000,
            )
        ],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=30,
            expected_investment_amount=100000,
        ),
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.0,
        suggested_action="watch",
        alerts=[],
    )


def _fake_facts() -> dict:
    return {
        "readonly": True,
        "instruction": "fake",
        "portfolio": {
            "weighted_return_percent": 1.0,
            "risk_level": "medium",
            "suggested_action": "watch",
            "concentration_limit_percent": 30,
        },
        "holdings": [
            {"fund_code": "519674", "weight_percent": 50.0}
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
        "alerts": [],
        "news": {},
    }


def test_judge_does_not_call_build_analysis_facts():
    """当调用方传入 facts 时，judge 不应再调用 build_analysis_facts。"""
    from app.services import report_judge

    parsed = {
        "title": "test",
        "summary": "ok",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "银河创新成长", "action": "观察"}
        ],
        "caveats": [],
    }
    snapshots = [FundSnapshot(fund_code="519674", fund_name="银河创新成长", source="test")]
    runtime = resolve_analysis_runtime(get_settings(), "fast")

    with patch(
        "app.services.report_judge.build_analysis_facts",
        side_effect=AssertionError("build_analysis_facts 不应再被调用"),
    ):
        out, meta = report_judge.judge_parsed_report(
            parsed, _request(), _risk(), snapshots, runtime, facts=_fake_facts()
        )

    assert out["fund_recommendations"][0]["fund_code"] == "519674"
    assert meta["rule_judge"] is True


def test_rule_judge_respects_concentration_using_provided_facts():
    """超集中度的持仓建议加仓 → 应被改写为减仓评估，且不重算 facts。"""
    from app.services import report_judge

    facts = _fake_facts()
    facts["holdings"] = [{"fund_code": "519674", "weight_percent": 80.0}]  # 超 30%

    parsed = {
        "title": "test",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "x", "action": "分批加仓"}
        ],
    }
    snapshots = [FundSnapshot(fund_code="519674", fund_name="x", source="test")]
    runtime = resolve_analysis_runtime(get_settings(), "fast")

    with patch(
        "app.services.report_judge.build_analysis_facts",
        side_effect=AssertionError("不应再调"),
    ):
        out, _meta = report_judge.judge_parsed_report(
            parsed, _request(), _risk(), snapshots, runtime, facts=facts
        )

    assert out["fund_recommendations"][0]["action"] == "减仓评估"


def test_fast_mode_judge_never_attempts_llm_review():
    """M3.1：fast 模式下 judge_parsed_report 必须完全不进入 LLM 审校分支
    （runtime.mode != "deep" 直接短路返回规则审校结果），meta 里三个 llm_judge_*
    标记均应为 False，确认 fast 模式"零新增 LLM 调用"这一产品定位没有被 M2 的双向
    guard 逻辑破坏（M2 的 resolve_escalation_floor 全程纯 Python 计算，不涉及网络）。"""
    from app.services import report_judge

    parsed = {
        "title": "test",
        "summary": "ok",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "银河创新成长", "action": "观察"}
        ],
        "caveats": [],
    }
    snapshots = [FundSnapshot(fund_code="519674", fund_name="银河创新成长", source="test")]
    runtime = resolve_analysis_runtime(get_settings(), "fast")

    with patch("httpx.post", side_effect=AssertionError("fast 模式不应发出任何 HTTP 请求")):
        out, meta = report_judge.judge_parsed_report(
            parsed, _request(), _risk(), snapshots, runtime, facts=_fake_facts()
        )

    assert meta["rule_judge"] is True
    assert meta["llm_judge_attempted"] is False
    assert meta["llm_judge_applied"] is False
    assert meta["llm_judge_timeout"] is False
    assert out["fund_recommendations"][0]["fund_code"] == "519674"


def test_fast_mode_bidirectional_guard_escalation_makes_no_network_calls():
    """M3.1：即使 M2 的双向 guard 触发了升级（resolve_escalation_floor 判定命中），
    apply_recommendation_guards 全程仍应是纯 Python 计算，不应发出任何 HTTP 请求——
    这是"fast 模式零新增 LLM 调用"能够成立的前提（guard 本身不调用模型）。"""
    from app.models import FundRecommendation, Holding, InvestorProfile, NewsItem
    from app.services.recommendation_guard import apply_recommendation_guards

    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10000,
        )
    ]
    request = AnalysisRequest(
        holdings=holdings,
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=30,
            expected_investment_amount=100000,
        ),
    )
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
                "evidence": {"composite": {"level": "不足", "score": 0.0}},
            }
        ]
    }
    rec = FundRecommendation(fund_code="519674", fund_name="银河创新成长", action="观察")

    with patch("httpx.post", side_effect=AssertionError("guard 不应发出任何 HTTP 请求")):
        _, guarded = apply_recommendation_guards(
            [rec],
            [],
            request,
            _risk(),
            [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)],
            [],
            facts=facts,
        )

    # 确认这条断言本身真的触发了升级（否则上面的 patch 就没测到点上）——facts 里
    # evidence.composite.level="不足" 属于弱证据，按 resolve_escalation_floor 的
    # 触发矩阵第2档应升级为「减仓评估」（非仅第1档的「暂停追涨」）。
    assert guarded[0].action == "减仓评估"
    assert guarded[0].suggested_position_change_percent == -25.0


def test_deep_judge_times_out_slow_llm_review(monkeypatch):
    from app.services import report_judge

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "c" * 32)
    refresh_settings()
    monkeypatch.setattr("app.services.report_judge.LLM_JUDGE_TIMEOUT_SECONDS", 0.01)

    def slow_llm_judge(*_args, **_kwargs):
        time.sleep(0.2)
        return {
            "title": "changed",
            "fund_recommendations": [
                {"fund_code": "519674", "fund_name": "x", "action": "观察"}
            ],
        }

    monkeypatch.setattr("app.services.report_judge._llm_judge", slow_llm_judge)
    parsed = {
        "title": "test",
        "summary": "ok",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "x", "action": "观察"}
        ],
        "caveats": [],
    }
    runtime = resolve_analysis_runtime(get_settings(), "deep")
    snapshots = [FundSnapshot(fund_code="519674", fund_name="x", source="test")]

    start = time.monotonic()
    out, meta = report_judge.judge_parsed_report(
        parsed,
        _request(),
        _risk(),
        snapshots,
        runtime,
        facts=_fake_facts(),
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.1
    assert out["title"] == "test"
    assert meta["llm_judge_attempted"] is True
    assert meta["llm_judge_timeout"] is True
    assert meta["llm_judge_applied"] is False


# --- M3.2: deep 模式风控复核角色（escalation_floors 随 draft 喂给 LLM judge） -----------


def test_escalation_floor_by_fund_code_extracts_only_triggered_holdings():
    from app.services.report_judge import _escalation_floor_by_fund_code

    facts = {
        "holdings": [
            {"fund_code": "519674", "escalation": {"min_bucket": 2, "min_action_label": "暂停追涨"}},
            {"fund_code": "008586", "escalation": {"min_bucket": None}},  # 未触发
            {"fund_code": "015945"},  # 完全没有 escalation key
            "not-a-dict",  # 防御性：facts 里混入非法条目不应崩
        ]
    }
    result = _escalation_floor_by_fund_code({}, facts)
    assert result == {"519674": {"min_bucket": 2, "min_action_label": "暂停追涨"}}


def test_escalation_floor_by_fund_code_handles_missing_holdings_key():
    from app.services.report_judge import _escalation_floor_by_fund_code

    assert _escalation_floor_by_fund_code({}, {}) == {}


def test_deep_mode_llm_judge_receives_risk_review_persona_and_escalation_floors(monkeypatch):
    """M3.2 核心：deep 模式下 judge_parsed_report 必须 (a) 使用风控经理复核角色的
    system prompt（不再是泛化的"审校员"），(b) 把 escalation_floors 一并放进
    user payload 喂给模型，作为它复核时的具体红线参照。"""
    from app.services import report_judge

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "c" * 32)
    refresh_settings()

    captured_payload = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "reviewed",
                                    "fund_recommendations": [
                                        {
                                            "fund_code": "519674",
                                            "fund_name": "银河创新成长",
                                            "action": "暂停追涨",
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ]
            }

    def fake_post(url, *, headers, json, timeout):  # noqa: A002 - 匹配 httpx.post 签名
        captured_payload["system"] = json["messages"][0]["content"]
        captured_payload["user"] = json["messages"][1]["content"]
        return _FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    facts = _fake_facts()
    facts["holdings"] = [
        {
            "fund_code": "519674",
            "weight_percent": 50.0,
            "escalation": {
                "min_bucket": 2,
                "min_action_label": "暂停追涨",
                "reasons": ["量价背离信号显著，且当前持仓板块方向不构成机会"],
                "suggested_position_change_percent": None,
                "basis": "量价背离信号显著，且当前持仓板块方向不构成机会",
            },
        }
    ]
    parsed = {
        "title": "test",
        "summary": "ok",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "银河创新成长", "action": "观察"}
        ],
        "caveats": [],
    }
    runtime = resolve_analysis_runtime(get_settings(), "deep")
    snapshots = [FundSnapshot(fund_code="519674", fund_name="银河创新成长", source="test")]

    out, meta = report_judge.judge_parsed_report(
        parsed, _request(), _risk(), snapshots, runtime, facts=facts
    )

    assert meta["llm_judge_attempted"] is True
    assert meta["llm_judge_applied"] is True
    assert out["fund_recommendations"][0]["action"] == "暂停追涨"
    assert "风控经理" in captured_payload["system"]
    user_payload = json.loads(captured_payload["user"])
    assert user_payload["escalation_floors"] == {
        "519674": facts["holdings"][0]["escalation"]
    }
    assert "escalation_floors" in user_payload["task"]
    assert "双向校验" in user_payload["task"]