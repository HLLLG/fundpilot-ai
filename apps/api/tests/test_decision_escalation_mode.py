"""M6：双向 guard 灰度开关（FUND_AI_DECISION_ESCALATION_MODE=shadow|enforced）。

设计文档：docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md 第 M6 节
+ 第 10 节「关于第 5 项」。

覆盖三个必须同时被 shadow 挡住的面：
1. 日报规则层 recommendation_guard.py 的 action/仓位%不真正改变，只写 validation_notes。
2. 荐基规则层 discovery_guard.py 的候选不真正被剔除/不真正提额，只写 validation_notes。
3. deep 模式 LLM 复核角色在 shadow 下须于请求前直接跳过，不能让模型自行改写
   action/候选/金额，也不能产生一笔不会应用的额外调用。

`conftest.py::_auth_env` 默认把测试环境切到 enforced（保持 M2~M4 历史测试的原始意图
不受影响），本文件内的用例显式 monkeypatch 回 shadow 来验证"只提示不生效"这一行为。
"""

from __future__ import annotations

import pytest

from app.config import get_settings, refresh_settings
from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.recommendation_guard import apply_recommendation_guards


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
def shadow_mode(monkeypatch):
    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "shadow")
    refresh_settings()
    yield
    refresh_settings()


_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


def _request(*, decision_style: str = "conservative") -> AnalysisRequest:
    profile = InvestorProfile(
        decision_style=decision_style,
        max_drawdown_percent=15,
        concentration_limit_percent=30,
        expected_investment_amount=100000,
        avoid_chasing=False,
    )
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10000,
        )
    ]
    return AnalysisRequest(holdings=holdings, profile=profile)


def _risk() -> RiskAssessment:
    return RiskAssessment(level="medium", weighted_return_percent=1.2, suggested_action="watch", alerts=[])


def _rec(**overrides) -> FundRecommendation:
    base = {"fund_code": "519674", "fund_name": "银河创新成长", "action": "观察"}
    base.update(overrides)
    return FundRecommendation(**base)


def _strong_divergence_facts() -> dict:
    return {
        "holdings": [
            {
                "fund_code": "519674",
                "weight_percent": 50.0,
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


def test_config_defaults_to_shadow(monkeypatch):
    """生产默认值必须是 shadow（更保守），enforced 需要用户显式切换。测试套件的
    `conftest.py::_auth_env` autouse fixture会把环境统一切到 enforced（保持
    M2~M4 历史测试的原始意图），因此这里必须显式 delenv 才能验证"未设置该环境变量
    时"pydantic 字段的真实默认值。"""
    monkeypatch.delenv("FUND_AI_DECISION_ESCALATION_MODE", raising=False)
    refresh_settings()
    assert get_settings().decision_escalation_mode == "shadow"


def test_shadow_mode_does_not_change_final_action(shadow_mode):
    """核心行为：shadow 模式下即使触发升级判定，最终展示给用户的 action 必须保持
    LLM 原始给出的动作不变（这里是"观察"），不能被规则层强制改写。"""
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=_strong_divergence_facts(),
    )
    rec = guarded[0]
    assert rec.action == "观察"
    assert rec.suggested_position_change_percent is None


def test_shadow_mode_annotates_would_be_escalation_in_validation_notes(shadow_mode):
    """未生效的升级判断必须以「灰度提示，未生效」的可识别文案写入 validation_notes，
    供 shadow_escalation_digest.py 结构化聚合、也供用户在报告里直接看到。"""
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=_strong_divergence_facts(),
    )
    rec = guarded[0]
    notes = " ".join(rec.validation_notes)
    assert "灰度提示" in notes
    assert "减仓评估" in notes  # 该场景按触发矩阵第2档应会被建议升级为"减仓评估"


def test_enforced_mode_still_changes_action(monkeypatch):
    """回归：显式切到 enforced 时，行为应与 M2 阶段实现完全一致（真正改变 action）。
    与 test_shadow_mode_does_not_change_final_action 形成对照，证明两种模式下
    真正产生了行为差异，而不是 shadow 分支写了但从未被触发。"""
    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "enforced")
    refresh_settings()
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=_strong_divergence_facts(),
    )
    rec = guarded[0]
    assert rec.action == "减仓评估"
    assert rec.suggested_position_change_percent == -25.0
    refresh_settings()


def test_shadow_mode_report_judge_skips_llm_and_freezes_decisions(shadow_mode, monkeypatch):
    """shadow 下 deep 风控复核必须在请求前短路，不能依赖模型遵守非约束 Prompt。"""
    from app.services import report_judge

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "c" * 32)
    refresh_settings()
    monkeypatch.setattr(
        "httpx.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("shadow 模式不应调用二次 LLM judge")
        ),
    )

    facts = _strong_divergence_facts()
    facts["holdings"][0]["escalation"] = {
        "min_bucket": 0,
        "min_action_label": "减仓评估",
        "reasons": ["量价背离信号显著"],
        "suggested_position_change_percent": -25.0,
        "basis": "量价背离信号显著",
    }
    parsed = {
        "title": "test",
        "summary": "ok",
        "fund_recommendations": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "action": "观察",
                "amount_yuan": 1000,
                "suggested_position_change_percent": None,
            }
        ],
        "caveats": [],
    }
    from app.services.analysis_runtime import resolve_analysis_runtime

    runtime = resolve_analysis_runtime(get_settings(), "deep")
    from app.models import FundSnapshot

    snapshots = [FundSnapshot(fund_code="519674", fund_name="银河创新成长", source="test")]

    result, meta = report_judge.judge_parsed_report(
        parsed, _request(), _risk(), snapshots, runtime, facts=facts
    )

    final_rec = result["fund_recommendations"][0]
    assert final_rec["action"] == "观察"
    assert final_rec["amount_yuan"] == 1000
    assert final_rec["suggested_position_change_percent"] is None
    assert meta["llm_judge_attempted"] is False
    assert meta["llm_judge_applied"] is False
    assert meta["llm_judge_skipped_reason"] == "decision_escalation_shadow"


# --- M6：荐基侧 shadow/enforced（discovery_guard.py） -----------------------------------


def _discovery_profile(*, concentration_limit_percent: float = 30) -> InvestorProfile:
    return InvestorProfile(
        decision_style="conservative",
        avoid_chasing=True,
        concentration_limit_percent=concentration_limit_percent,
        expected_investment_amount=100000,
    )


def _discovery_pool_item(*, fund_quality_score: float) -> dict:
    return {
        "fund_code": "020357",
        "fund_name": "华夏半导体材料设备ETF联接C",
        "sector_label": "半导体材料",
        "fund_quality_score": fund_quality_score,
        "sector_fit_score": 37.12,
        "quality_reasons": ["近3/6月表现占优"],
        "quality_penalties": [],
        "return_3m_percent": 18.2,
        "return_6m_percent": 31.4,
        "return_1y_percent": 42.0,
        "nav_trend": {"distance_from_high_percent": -8.5, "trend_label": "回调企稳"},
    }


def _discovery_opportunity(*, opportunity_available: bool) -> dict:
    return {
        "sector_label": "半导体材料",
        "track": "momentum",
        "score": 86.5,
        "confidence": "高",
        "opportunity_available": opportunity_available,
        "entry_hint": "可分批关注",
        "evidence": ["1d/5d 动量延续"],
        "penalties": ["资金背离或持续流出"] if not opportunity_available else [],
        "today_main_force_net_yi": -3.2 if not opportunity_available else 3.2,
        "cumulative_5d_net_yi": -9.8 if not opportunity_available else 9.8,
        "pattern_label": "distribution" if not opportunity_available else "price_flow_aligned_up",
    }


def _discovery_rec(*, action: str = "建议关注", suggested_amount_yuan: float | None = None) -> dict:
    return {
        "fund_code": "020357",
        "fund_name": "华夏半导体材料设备ETF联接C",
        "sector_name": "半导体材料",
        "action": action,
        "suggested_amount_yuan": suggested_amount_yuan,
        "hold_horizon": "2-4周",
        "confidence": "中",
        "points": ["近3/6月表现占优"],
        "risks": ["波动较高"],
    }


def _run_discovery(
    *,
    pool_item: dict,
    opportunity: dict,
    rec_kwargs: dict | None = None,
    profile: InvestorProfile | None = None,
    budget_yuan: float = 50000,
):
    from app.services.discovery_client import build_discovery_report_from_parsed

    parsed = {
        "title": "机会扫描",
        "summary": "半导体材料方向。",
        "recommendations": [_discovery_rec(**(rec_kwargs or {}))],
        "caveats": [],
    }
    facts = {
        "portfolio_gap": {"available_budget_yuan": budget_yuan, "holdings_slim": []},
        "sector_opportunities": [opportunity],
    }
    return build_discovery_report_from_parsed(
        parsed,
        target_sectors=["半导体材料"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=[pool_item],
        discovery_facts=facts,
        profile=profile or _discovery_profile(),
        held_codes=set(),
        budget_yuan=budget_yuan,
        sector_heat=[{"sector_label": "半导体材料", "change_1d_percent": 1.0}],
        analysis_mode="fast",
    )


def test_discovery_shadow_mode_does_not_exclude_candidate(shadow_mode) -> None:
    """shadow 模式下，即使量价背离+基金质量分双维度共振指向剔除，候选仍应保留在
    recommendations 里（不真正剔除），只在 validation_notes 标注"若切换会怎样"。"""
    report = _run_discovery(
        pool_item=_discovery_pool_item(fund_quality_score=40.0),
        opportunity=_discovery_opportunity(opportunity_available=False),
    )
    assert len(report.recommendations) == 1
    assert report.recommendations[0].fund_code == "020357"
    assert report.eliminated_candidates == []
    notes = " ".join(report.recommendations[0].validation_notes)
    assert "灰度提示" in notes
    assert "剔除" in notes


def test_discovery_shadow_mode_does_not_boost_amount(shadow_mode) -> None:
    """shadow 模式下，即使双维度共振指向提额，建议金额仍应受常规集中度上限约束
    （不真正提额），只在 validation_notes 标注"若切换会怎样"。"""
    report = _run_discovery(
        pool_item=_discovery_pool_item(fund_quality_score=80.0),
        opportunity=_discovery_opportunity(opportunity_available=True),
        rec_kwargs={"action": "建议关注", "suggested_amount_yuan": 18000},
    )
    rec = report.recommendations[0]
    # 常规上限 15000（budget 50000 * concentration 30%），shadow 模式下不应被 boost。
    assert rec.suggested_amount_yuan == 15000
    notes = " ".join(rec.validation_notes)
    assert "灰度提示" in notes
    assert "提高" in notes


def test_discovery_enforced_mode_still_excludes_candidate(monkeypatch) -> None:
    """回归对照：enforced 模式下荐基剔除行为应与 M4 阶段实现完全一致。"""
    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "enforced")
    refresh_settings()
    report = _run_discovery(
        pool_item=_discovery_pool_item(fund_quality_score=40.0),
        opportunity=_discovery_opportunity(opportunity_available=False),
    )
    assert report.recommendations == []
    assert len(report.eliminated_candidates) == 1
    assert report.eliminated_candidates[0].fund_code == "020357"
    refresh_settings()


def test_discovery_shadow_mode_skips_llm_and_freezes_decisions(shadow_mode, monkeypatch) -> None:
    """荐基 shadow 在请求前短路，候选集合、动作和金额自然保持草案值。"""
    from app.services.discovery_judge import judge_parsed_discovery_report

    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-" + "c" * 32)
    refresh_settings()
    monkeypatch.setattr(
        "httpx.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("shadow 模式不应调用二次 LLM judge")
        ),
    )

    parsed = {
        "title": "机会扫描",
        "summary": "半导体材料方向。",
        "market_view": "偏强。",
        "recommendations": [_discovery_rec()],
        "caveats": [],
    }
    result, meta = judge_parsed_discovery_report(
        parsed,
        candidate_pool=[_discovery_pool_item(fund_quality_score=40.0)],
        discovery_facts={"sector_opportunities": [_discovery_opportunity(opportunity_available=False)]},
        analysis_mode="deep",
    )
    assert result is parsed
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["action"] == "建议关注"
    assert result["recommendations"][0]["suggested_amount_yuan"] is None
    assert meta["llm_judge_attempted"] is False
    assert meta["llm_judge_applied"] is False
    assert meta["llm_judge_skipped_reason"] == "decision_escalation_shadow"


# --- M6：allowed_actions 词表在 shadow 模式下不向 LLM 开放新增两档 ------------------------


def test_allowed_actions_hides_new_extreme_actions_in_shadow_mode(shadow_mode) -> None:
    """即使某持仓命中了「大幅减仓评估/清仓评估」触发门槛，shadow 模式下也不应向
    allowed_actions 追加这两个新词——灰度期间连"选项本身"都不该出现在 LLM 的可选
    动作列表里（比只挡规则层生效更进一步，从源头不给模型看到新选项）。"""
    from app.services.analysis_facts import _extra_allowed_actions_for_escalation
    from app.services.decision_guard_shared import ACTION_BUCKET_CLEAR_ALL

    per_fund = [{"fund_code": "519674", "escalation": {"min_bucket": ACTION_BUCKET_CLEAR_ALL}}]
    assert _extra_allowed_actions_for_escalation(per_fund) == []


def test_allowed_actions_shows_new_extreme_actions_in_enforced_mode(monkeypatch) -> None:
    """回归对照：enforced 模式下应恢复 M2.2 阶段实现的原始行为（追加两个新词）。"""
    from app.services.analysis_facts import _extra_allowed_actions_for_escalation
    from app.services.decision_guard_shared import ACTION_BUCKET_CLEAR_ALL, ACTION_BUCKET_DEEP_REDUCE

    monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", "enforced")
    refresh_settings()

    clear_all_row = [{"fund_code": "519674", "escalation": {"min_bucket": ACTION_BUCKET_CLEAR_ALL}}]
    assert _extra_allowed_actions_for_escalation(clear_all_row) == ["清仓评估", "大幅减仓评估"]

    deep_reduce_row = [{"fund_code": "519674", "escalation": {"min_bucket": ACTION_BUCKET_DEEP_REDUCE}}]
    assert _extra_allowed_actions_for_escalation(deep_reduce_row) == ["大幅减仓评估"]

    no_trigger_row = [{"fund_code": "519674", "escalation": {"min_bucket": None}}]
    assert _extra_allowed_actions_for_escalation(no_trigger_row) == []
    refresh_settings()
