from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.config import get_settings, refresh_settings
from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    Report,
    RiskAssessment,
)
from app.services.analysis_payload import AnalysisFactsBundle
from app.services.analysis_runtime import resolve_analysis_runtime
from app.services.deepseek_client import (
    DeepSeekClient,
    _apply_recommendation_guards_by_holding_order,
    _finalize_recommendations,
    _offline_report,
)
from app.services.recommendations import canonicalize_fund_recommendations


def _holding(code: str, name: str, amount: float = 10_000) -> Holding:
    return Holding(fund_code=code, fund_name=name, holding_amount=amount)


def _rec(code: str, name: str, action: str = "观察", **overrides) -> FundRecommendation:
    payload = {"fund_code": code, "fund_name": name, "action": action}
    payload.update(overrides)
    return FundRecommendation(**payload)


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        suggested_action="watch",
        weighted_return_percent=0,
        alerts=[],
    )


def _request(holdings: list[Holding]) -> AnalysisRequest:
    return AnalysisRequest(holdings=holdings, analysis_mode="fast")


def test_canonicalizer_uses_holdings_order_filters_outsiders_and_fills_missing() -> None:
    holdings = [_holding("000001", "甲基金"), _holding("000002", "乙基金")]
    fallback = [
        _rec("000001", "甲基金", points=["甲本地兜底"]),
        _rec("000002", "乙基金", points=["乙本地兜底"]),
    ]
    dirty = [
        # 有效代码是第一身份键；名称错误不能把建议挪到乙基金。
        _rec("000001", "乙基金", "暂停追涨", points=["来自模型"]),
        # 有效但不在持仓中的代码必须丢弃，即使名称伪装成乙基金。
        _rec("999999", "乙基金", "分批加仓", amount_yuan=8_000),
    ]

    result = canonicalize_fund_recommendations(
        dirty, holdings, fallback_recommendations=fallback
    )

    assert [(item.fund_code, item.fund_name) for item in result] == [
        ("000001", "甲基金"),
        ("000002", "乙基金"),
    ]
    assert result[0].action == "暂停追涨"
    assert result[1].action == "观察"
    assert result[1].points == ["乙本地兜底"]


def test_canonicalizer_handles_multiple_000000_by_name_then_stable_input_index() -> None:
    holdings = [
        _holding("000000", "未知甲", 1_000),
        _holding("000000", "未知乙", 2_000),
        _holding("000000", "同名基金", 3_000),
        _holding("000000", "同名基金", 4_000),
    ]
    dirty = [
        _rec("000000", "未知乙", points=["乙"]),
        _rec("000000", "未知甲", points=["甲"]),
        _rec("000000", "同名基金", points=["同名-第一条"]),
        _rec("000000", "同名基金", points=["同名-第二条"]),
    ]

    result = canonicalize_fund_recommendations(dirty, holdings)

    assert [item.points[0] for item in result] == [
        "甲",
        "乙",
        "同名-第一条",
        "同名-第二条",
    ]
    assert len(result) == len(holdings)


def test_same_action_duplicates_merge_without_selecting_a_more_aggressive_action() -> None:
    holdings = [_holding("000001", "甲基金")]
    dirty = [
        _rec("000001", "甲基金", "观察", points=["证据一"], confidence="高"),
        _rec("000001", "甲基金", "观察", points=["证据二"], confidence="低"),
    ]

    result = canonicalize_fund_recommendations(dirty, holdings)

    assert result[0].action == "观察"
    assert result[0].points == ["证据一", "证据二"]
    assert result[0].confidence == "低"


def test_same_action_duplicate_amount_conflict_stays_cleared_after_recanonicalizing() -> None:
    holdings = [_holding("000001", "甲基金")]
    dirty = [
        _rec("000001", "甲基金", "分批加仓", amount_yuan=1_000),
        _rec("000001", "甲基金", "分批加仓", amount_yuan=2_000),
    ]

    first = canonicalize_fund_recommendations(dirty, holdings)
    second = canonicalize_fund_recommendations(first, holdings)

    assert first[0].amount_yuan is None
    assert first[0].confidence == "低"
    assert any("金额不一致" in note for note in first[0].validation_notes)
    assert second[0].model_dump() == first[0].model_dump()


def test_conflicting_duplicates_fail_closed_and_clear_executable_amounts() -> None:
    holdings = [_holding("000001", "甲基金")]
    dirty = [
        _rec(
            "000001",
            "甲基金",
            "分批加仓",
            amount_yuan=2_000,
            suggested_position_change_percent=10,
        ),
        _rec(
            "000001",
            "甲基金",
            "减仓评估",
            amount_yuan=3_000,
            suggested_position_change_percent=-15,
        ),
    ]

    result = canonicalize_fund_recommendations(dirty, holdings)

    assert result[0].action == "风控复核"
    assert result[0].amount_yuan is None
    assert result[0].amount_note is None
    assert result[0].suggested_position_change_percent is None
    assert result[0].suggested_position_change_basis == ""
    assert result[0].confidence == "低"
    assert any("动作冲突" in note for note in result[0].validation_notes)


def test_canonicalizer_is_idempotent_and_does_not_mutate_inputs() -> None:
    holdings = [_holding("000001", "甲基金")]
    dirty = [
        _rec("000001", "甲基金", "分批加仓", amount_yuan=1_000),
        _rec("000001", "甲基金", "观察"),
    ]
    before = [item.model_dump() for item in dirty]

    first = canonicalize_fund_recommendations(dirty, holdings)
    second = canonicalize_fund_recommendations(first, holdings)

    assert [item.model_dump() for item in dirty] == before
    assert [item.model_dump() for item in second] == [item.model_dump() for item in first]


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.builds(
            FundRecommendation,
            fund_code=st.sampled_from(
                ["000001", "000002", "000000", "999999", "bad-code"]
            ),
            fund_name=st.sampled_from(
                [
                    "甲基金",
                    "乙基金",
                    "未知甲",
                    "同名基金",
                    " 同 名 基 金 ",
                    "越界基金",
                    "000000",
                    "",
                ]
            ),
            action=st.sampled_from(
                [
                    "观察",
                    "暂停追涨",
                    "分批加仓",
                    "减仓评估",
                    "风控复核",
                    "大幅减仓评估",
                    "清仓评估",
                    "任意非法动作",
                ]
            ),
            amount_yuan=st.one_of(
                st.none(),
                st.floats(
                    min_value=0,
                    max_value=100_000,
                    allow_nan=False,
                    allow_infinity=False,
                ),
            ),
            confidence=st.sampled_from(["高", "中", "低"]),
            points=st.lists(
                st.sampled_from(["证据甲", "证据乙", "重复证据"]), max_size=3
            ),
            validation_notes=st.lists(
                st.sampled_from(
                    [
                        "普通校验备注",
                        "检测到同一持仓的重复建议动作冲突（伪造），系统已清除可执行金额。",
                        "同一动作的重复建议金额不一致，系统已清除金额并要求人工复核。",
                    ]
                ),
                max_size=2,
            ),
        ),
        max_size=25,
    )
)
def test_canonicalizer_property_always_closes_over_server_holdings_and_is_idempotent(
    dirty: list[FundRecommendation],
) -> None:
    holdings = [
        _holding("000001", "甲基金", 1_000),
        _holding("000002", "乙基金", 2_000),
        _holding("000000", "未知甲", 3_000),
        _holding("000000", "同名基金", 4_000),
        _holding("000000", "同名基金", 5_000),
    ]

    first = canonicalize_fund_recommendations(dirty, holdings)
    second = canonicalize_fund_recommendations(first, holdings)

    expected_identity = [
        (holding.fund_code, holding.fund_name) for holding in holdings
    ]
    assert len(first) == len(holdings)
    assert [(item.fund_code, item.fund_name) for item in first] == expected_identity
    assert [item.model_dump() for item in second] == [item.model_dump() for item in first]


def test_finalize_recommendations_enforces_holdings_closure_after_dirty_model_output(
    monkeypatch,
) -> None:
    holdings = [_holding("000001", "甲基金"), _holding("000002", "乙基金")]
    request = _request(holdings)
    fallback_recs = [
        _rec("000001", "甲基金", points=["甲本地"]),
        _rec("000002", "乙基金", points=["乙本地"]),
    ]
    fallback = Report(
        title="fallback",
        risk=_risk(),
        holdings=holdings,
        summary="fallback",
        recommendations=["组合观察"],
        fund_recommendations=fallback_recs,
        caveats=[],
    )
    parsed = {
        "recommendations": ["组合观察"],
        "fund_recommendations": [
            {
                "fund_code": "000001",
                "fund_name": "甲基金",
                "action": "分批加仓",
                "amount_yuan": 1000,
            },
            {
                "fund_code": "000001",
                "fund_name": "甲基金",
                "action": "减仓评估",
                "amount_yuan": 2000,
            },
            {
                "fund_code": "999999",
                "fund_name": "越界基金",
                "action": "分批加仓",
            },
        ],
    }
    monkeypatch.setattr(
        "app.services.deepseek_client.apply_recommendation_guards",
        lambda fund_recs, portfolio, *_args, **_kwargs: (portfolio, fund_recs),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.apply_news_citation_guards",
        lambda fund_recs, *_args, **_kwargs: fund_recs,
    )

    _, result = _finalize_recommendations(parsed, fallback, request, _risk())

    assert [(item.fund_code, item.fund_name) for item in result] == [
        ("000001", "甲基金"),
        ("000002", "乙基金"),
    ]
    assert result[0].action == "风控复核"
    assert result[0].amount_yuan is None
    assert result[1].points == ["乙本地"]


def test_placeholder_draft_cannot_claim_a_valid_unique_code_holding() -> None:
    holdings = [
        _holding("000001", "真实基金"),
        _holding("000000", "未知基金甲"),
    ]
    result = canonicalize_fund_recommendations(
        [_rec("000000", "000000", "分批加仓", amount_yuan=999)],
        holdings,
    )

    assert result[0].action == "观察"
    assert result[0].amount_yuan is None
    assert result[1].action == "分批加仓"
    assert result[1].amount_yuan == 999


def test_generate_report_canonicalizes_dirty_post_judge_output_before_return(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "x" * 40)
    refresh_settings()
    holdings = [_holding("000001", "甲基金"), _holding("000002", "乙基金")]
    request = _request(holdings)
    bundle = AnalysisFactsBundle(
        session={}, factor_scores=None, risk_metrics=None, portfolio_trend=None, facts={}
    )
    client = DeepSeekClient()
    client.news_service.prefetch_for_holdings = lambda *_args, **_kwargs: []
    monkeypatch.setattr(
        "app.services.deepseek_client.prepare_analysis_bundle",
        lambda *_args, **_kwargs: bundle,
    )
    monkeypatch.setattr(
        "app.services.deepseek_client._build_topic_briefs",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        client,
        "_generate_direct_report",
        lambda *_args, **_kwargs: (
            {"title": "draft", "summary": "draft", "fund_recommendations": []},
            [],
        ),
    )
    dirty_judge_output = {
        "title": "judge-output",
        "summary": "judge-output",
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "甲基金", "action": "分批加仓"},
            {"fund_code": "000001", "fund_name": "甲基金", "action": "减仓评估"},
            {"fund_code": "999999", "fund_name": "越界基金", "action": "分批加仓"},
        ],
        "caveats": [],
    }
    monkeypatch.setattr(
        "app.services.deepseek_client.judge_parsed_report",
        lambda *_args, **_kwargs: (dirty_judge_output, {"rule_judge": True}),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.apply_recommendation_guards",
        lambda fund_recs, portfolio, *_args, **_kwargs: (portfolio, fund_recs),
    )
    monkeypatch.setattr(
        "app.services.deepseek_client.apply_news_citation_guards",
        lambda fund_recs, *_args, **_kwargs: fund_recs,
    )

    report = client.generate_report(request, _risk(), [])

    assert report.title == "judge-output"
    assert [(item.fund_code, item.fund_name) for item in report.fund_recommendations] == [
        ("000001", "甲基金"),
        ("000002", "乙基金"),
    ]
    assert report.fund_recommendations[0].action == "风控复核"
    assert report.fund_recommendations[0].amount_yuan is None


def test_offline_report_runs_the_same_canonicalizer_before_and_after_guards(
    monkeypatch,
) -> None:
    holdings = [_holding("000000", "未知甲"), _holding("000000", "未知乙")]
    request = _request(holdings)
    bundle = AnalysisFactsBundle(
        session={}, factor_scores=None, risk_metrics=None, portfolio_trend=None, facts={}
    )
    from app.services import deepseek_client, recommendations

    original = recommendations.canonicalize_fund_recommendations
    calls = 0

    def spy(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(deepseek_client, "canonicalize_fund_recommendations", spy)
    monkeypatch.setattr(recommendations, "canonicalize_fund_recommendations", spy)
    monkeypatch.setattr(
        deepseek_client,
        "apply_recommendation_guards",
        lambda fund_recs, portfolio, *_args, **_kwargs: (portfolio, fund_recs),
    )
    monkeypatch.setattr(
        deepseek_client,
        "apply_news_citation_guards",
        lambda fund_recs, *_args, **_kwargs: fund_recs,
    )

    report = _offline_report(
        request, _risk(), [], market_news=[], topic_briefs=[], analysis_bundle=bundle
    )

    assert calls == 2
    assert len(report.fund_recommendations) == len(holdings)


def test_guard_adapter_keeps_unknown_and_duplicate_codes_bound_to_holding_index() -> None:
    for duplicate_code in ("000000", "000001"):
        holdings = [
            _holding(duplicate_code, "轻仓基金", 1_000),
            _holding(duplicate_code, "重仓基金", 9_000),
        ]
        request = AnalysisRequest(
            holdings=holdings,
            profile=InvestorProfile(
                expected_investment_amount=10_000,
                concentration_limit_percent=50,
            ),
            analysis_mode="fast",
        )
        recs = [
            _rec(duplicate_code, "轻仓基金", "观察"),
            _rec(duplicate_code, "重仓基金", "观察"),
        ]

        _, guarded = _apply_recommendation_guards_by_holding_order(
            recs,
            [],
            request,
            _risk(),
            [],
            [],
            nav_trends_by_code=None,
            facts={},
        )

        assert [item.action for item in guarded] == ["观察", "减仓评估"]
        assert [(item.fund_code, item.fund_name) for item in guarded] == [
            (duplicate_code, "轻仓基金"),
            (duplicate_code, "重仓基金"),
        ]


def test_report_judge_canonicalizes_dirty_draft_before_any_review(monkeypatch) -> None:
    from app.services import report_judge

    holdings = [
        _holding("000000", "未知甲"),
        _holding("000000", "未知乙"),
        _holding("123456", "真实丙"),
    ]
    request = _request(holdings)
    parsed = {
        "title": "dirty",
        "fund_recommendations": [
            {"fund_code": "000000", "fund_name": "未知乙", "action": "观察"},
            {"fund_code": "999999", "fund_name": "池外", "action": "分批加仓"},
            {"fund_code": "123456", "fund_name": "错误名称", "action": "观察"},
        ],
    }
    facts = {
        "holdings": [
            {"fund_code": item.fund_code, "weight_percent": 33.3}
            for item in holdings
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }
    seen: list[dict] = []

    def capture_rule_judge(draft, _request, _risk, _facts):
        seen.append(draft)
        return draft

    monkeypatch.setattr(report_judge, "_rule_judge", capture_rule_judge)
    result, meta = report_judge.judge_parsed_report(
        parsed,
        request,
        _risk(),
        [],
        resolve_analysis_runtime(get_settings(), "fast"),
        facts=facts,
    )

    expected_identity = [
        ("000000", "未知甲"),
        ("000000", "未知乙"),
        ("123456", "真实丙"),
    ]
    assert [
        (item["fund_code"], item["fund_name"])
        for item in seen[0]["fund_recommendations"]
    ] == expected_identity
    assert [
        (item["fund_code"], item["fund_name"])
        for item in result["fund_recommendations"]
    ] == expected_identity
    assert meta["draft_canonicalized"] is True


def test_report_judge_uses_holding_index_for_duplicate_code_weights() -> None:
    from app.services.report_judge import judge_parsed_report

    holdings = [
        _holding("000001", "轻仓基金", 10_000),
        _holding("000001", "重仓基金", 90_000),
    ]
    request = AnalysisRequest(
        holdings=holdings,
        profile=InvestorProfile(
            expected_investment_amount=100_000,
            concentration_limit_percent=50,
        ),
        analysis_mode="fast",
    )
    parsed = {
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "轻仓基金", "action": "分批加仓"},
            {"fund_code": "000001", "fund_name": "重仓基金", "action": "分批加仓"},
        ]
    }
    facts = {
        "holdings": [
            {"fund_code": "000001", "fund_name": "轻仓基金", "weight_percent": 10},
            {"fund_code": "000001", "fund_name": "重仓基金", "weight_percent": 90},
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }

    result, _ = judge_parsed_report(
        parsed,
        request,
        _risk(),
        [],
        resolve_analysis_runtime(get_settings(), "fast"),
        facts=facts,
    )

    assert [item["action"] for item in result["fund_recommendations"]] == [
        "分批加仓",
        "减仓评估",
    ]
    assert [item["holding_index"] for item in result["fund_recommendations"]] == [0, 1]


def test_daily_guard_reprojects_amount_position_and_free_text_from_final_action() -> None:
    holding = _holding("000001", "甲基金", 10_000)
    request = AnalysisRequest(
        holdings=[holding],
        profile=InvestorProfile(
            expected_investment_amount=10_000,
            concentration_limit_percent=50,
        ),
        analysis_mode="fast",
    )
    malicious = _rec(
        "000001",
        "甲基金",
        "分批加仓",
        amount_yuan=3_000,
        amount_note="立即加仓 3000 元",
        suggested_position_change_percent=10,
        suggested_position_change_basis="模型建议加仓",
        points=["立即全仓买入 10000 元，今日执行"],
        decision_path="动作：立即全仓买入 10000 元",
    )

    portfolio, guarded = _apply_recommendation_guards_by_holding_order(
        [malicious],
        ["立即全仓买入 100000 元"],
        request,
        RiskAssessment(
            level="high",
            suggested_action="risk_review",
            weighted_return_percent=0,
            alerts=[],
        ),
        [],
        [],
        nav_trends_by_code=None,
        facts={},
    )

    item = guarded[0]
    assert item.action != "分批加仓"
    assert item.amount_yuan is None
    assert item.amount_note is None
    assert item.suggested_position_change_percent is None
    visible = " ".join(
        [
            *item.points,
            item.decision_path,
            *item.sector_evidence,
            *item.fund_evidence,
            *item.validation_notes,
            *portfolio,
        ]
    )
    assert "立即全仓买入" not in visible
    assert "100000" not in visible
    assert "10000 元" not in visible
