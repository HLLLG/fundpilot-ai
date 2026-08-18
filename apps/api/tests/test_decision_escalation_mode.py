"""双向守卫的灰度门控（`FUND_AI_DECISION_ESCALATION_MODE`）。

这个文件补的是一个真实缺口：`conftest.py` 把测试套件整体设为 `enforced`，理由写的是
"shadow 模式'只提示不生效'的行为由专门的 test_decision_escalation_mode.py 显式
monkeypatch 覆盖验证"——但该文件此前并不存在。当时生产默认还是 `shadow`，等于**默认路径
几乎没有覆盖，而全部测试跑的是非默认路径**。2026-08 默认值已切到 `enforced`，两边现在
一致；`shadow` 降级为回滚开关，它仍然需要这里的覆盖，否则回滚时没有任何测试兜底。

`resolve_escalation_floor` 这个纯函数本身在 `test_decision_guard_shared.py` 里已经覆盖
充分；这里只锁模式门控本身：同一份触发升级的证据，在两种模式下最终动作、仓位比例、
校验备注与动作词表分别应该是什么。
"""

import pytest

from app.config import refresh_settings
from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.analysis_facts import build_allowed_actions
from app.services.decision_guard_shared import (
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
    ACTION_BUCKET_REDUCE,
)
from app.services.recommendation_guard import apply_recommendation_guards

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]
_BASE_ACTIONS = ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"]


@pytest.fixture
def escalation_mode(monkeypatch: pytest.MonkeyPatch):
    """按用例切换 `FUND_AI_DECISION_ESCALATION_MODE` 并刷新 settings 缓存。"""

    def _set(mode: str) -> None:
        monkeypatch.setenv("FUND_AI_DECISION_ESCALATION_MODE", mode)
        refresh_settings()

    yield _set
    monkeypatch.undo()
    refresh_settings()


@pytest.fixture(autouse=True)
def _no_live_intraday_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10_000,
            )
        ],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
            avoid_chasing=False,
        ),
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        suggested_action="watch",
        weighted_return_percent=1.0,
        alerts=[],
    )


def _escalating_facts() -> dict:
    """触发第 2 档（减仓评估 / -25%）：量价背离显著 + 方向不成立 + 基金证据不足。"""
    return {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": {
                    "track": "momentum",
                    "confidence": "高",
                    "opportunity_available": False,
                    "penalties": ["资金背离或持续流出"],
                },
                "evidence": {"composite": {"level": "不足", "score": 0.0}},
            }
        ],
        "allowed_actions": list(_BASE_ACTIONS),
    }


def _guard(facts: dict) -> FundRecommendation:
    _portfolio, guarded = apply_recommendation_guards(
        [FundRecommendation(fund_code="519674", fund_name="银河创新成长", action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        facts=facts,
    )
    return guarded[0]


def test_enforced_mode_rewrites_the_action_and_the_position_percent(escalation_mode) -> None:
    escalation_mode("enforced")

    rec = _guard(_escalating_facts())

    assert rec.action == "减仓评估"
    assert rec.suggested_position_change_percent == pytest.approx(-25.0)
    assert any("已将" in point and "上调为" in point for point in rec.points)
    # enforced 下不该再出现灰度提示。
    assert not any("灰度提示" in note for note in rec.validation_notes)


def test_shadow_mode_keeps_the_action_and_only_annotates(escalation_mode) -> None:
    escalation_mode("shadow")

    rec = _guard(_escalating_facts())

    # 生产默认路径：动作与仓位建议都不能被改写。
    assert rec.action == "观察"
    assert rec.suggested_position_change_percent is None
    shadow_notes = [note for note in rec.validation_notes if "灰度提示" in note]
    assert shadow_notes, rec.validation_notes
    # 提示必须写出"若启用会被升级成什么"，否则灰度期无从复盘。
    assert "减仓评估" in shadow_notes[0]
    assert "未生效" in shadow_notes[0]


def test_shadow_mode_withholds_the_stronger_action_words(escalation_mode) -> None:
    """shadow 期间模型连选项都不该看到，否则它会选中一个事后不生效的动作词。"""
    escalation_mode("shadow")

    per_fund = [
        {"escalation": {"min_bucket": ACTION_BUCKET_CLEAR_ALL}},
        {"escalation": {"min_bucket": ACTION_BUCKET_DEEP_REDUCE}},
    ]

    assert build_allowed_actions(per_fund) == _BASE_ACTIONS


@pytest.mark.parametrize(
    ("min_bucket", "expected_extra"),
    [
        pytest.param(ACTION_BUCKET_REDUCE, [], id="reduce_adds_nothing"),
        pytest.param(
            ACTION_BUCKET_DEEP_REDUCE,
            ["大幅减仓评估"],
            id="deep_reduce_adds_one",
        ),
        pytest.param(
            ACTION_BUCKET_CLEAR_ALL,
            ["清仓评估", "大幅减仓评估"],
            id="clear_all_adds_both",
        ),
    ],
)
def test_enforced_mode_extends_the_action_vocabulary_by_threshold(
    escalation_mode,
    min_bucket: int,
    expected_extra: list[str],
) -> None:
    escalation_mode("enforced")

    actions = build_allowed_actions([{"escalation": {"min_bucket": min_bucket}}])

    assert actions == [*_BASE_ACTIONS, *expected_extra]


def test_untriggered_escalation_is_identical_in_both_modes(escalation_mode) -> None:
    """未触发升级时两种模式必须完全一致——否则灰度开关本身就在改变无关行为。"""
    quiet_facts = {
        "holdings": [
            {
                "fund_code": "519674",
                "sector_opportunity": {
                    "track": "setup",
                    "confidence": "中",
                    "opportunity_available": True,
                    "pattern_label": "accumulation",
                },
                "evidence": {"composite": {"level": "中", "score": 2.0}},
            }
        ],
        "allowed_actions": list(_BASE_ACTIONS),
    }

    escalation_mode("enforced")
    enforced = _guard(quiet_facts)
    escalation_mode("shadow")
    shadow = _guard(quiet_facts)

    assert enforced.action == shadow.action == "观察"
    assert enforced.suggested_position_change_percent is None
    assert shadow.suggested_position_change_percent is None
    assert not any("灰度提示" in note for note in shadow.validation_notes)
