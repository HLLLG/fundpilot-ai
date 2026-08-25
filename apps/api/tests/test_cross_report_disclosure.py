"""日报 ↔ 发现基金的跨报告披露。

背景：两侧方向层共用同一套打分不会矛盾，但基金层会"看起来打架"——发现基金今天推荐
买板块 A 的新基金 Y（它只排除已持有的代码），日报同一天可以因浮亏封档/载体质量把同
板块持仓 X 按在观察。两个结论都对，但此前没有任何一句话向用户解释这不是自相矛盾。

契约：只披露、不仲裁。日报侧只读**当日**发现报告（昨天的推荐基于昨天的方向状态）；
发现侧对买入类推荐点名同方向持仓并声明分工。两侧都不修改任何动作、比例或金额。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import (
    AnalysisRequest,
    DiscoveryRecommendation,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
)
from app.services.discovery_guard import _held_same_sector_note
from app.services.recommendation_guard import apply_recommendation_guards
from app.services.report_discovery_cross_reference import (
    build_discovery_cross_reference,
)
from app.services.risk import RiskAssessment

# 北京时间 2026-06-10 10:00 的决策时钟。
_DECISION_AT = datetime(2026, 6, 10, 2, 0, tzinfo=timezone.utc)


def _holding(sector: str = "半导体") -> Holding:
    return Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name=sector,
        holding_amount=10_000.0,
    )


def _summary(created_at: str) -> dict:
    return {"id": "report-1", "created_at": created_at, "title": "扫描"}


def _payload(recommendations: list[dict]) -> dict:
    return {"id": "report-1", "recommendations": recommendations}


# --------------------------------------------------------------------------
# build_discovery_cross_reference
# --------------------------------------------------------------------------


def test_same_day_buy_recommendation_on_a_held_sector_is_surfaced(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.database.list_discovery_reports",
        lambda limit=1: [_summary("2026-06-10T01:00:00+00:00")],
    )
    monkeypatch.setattr(
        "app.database.get_discovery_report",
        lambda _id: _payload(
            [
                {
                    "fund_code": "008888",
                    "fund_name": "更好的半导体基金",
                    "sector_name": "半导体",
                    "action": "分批买入",
                    "entry_path": "confirmed_entry",
                },
                # 非买入类不披露：观察候选不构成"看起来矛盾"的素材。
                {
                    "fund_code": "009999",
                    "fund_name": "观察中的半导体基金",
                    "sector_name": "半导体",
                    "action": "建议关注",
                },
                # 非持仓板块不披露。
                {
                    "fund_code": "007777",
                    "fund_name": "医疗基金",
                    "sector_name": "医疗",
                    "action": "分批买入",
                },
            ]
        ),
    )

    cross = build_discovery_cross_reference([_holding()], decision_at=_DECISION_AT)

    assert cross["available"] is True
    assert cross["report_id"] == "report-1"
    rows = cross["buy_recommendations_by_sector"]["半导体"]
    assert [row["fund_code"] for row in rows] == ["008888"]
    assert rows[0]["relation"] == "same_sector"
    assert "医疗" not in cross["buy_recommendations_by_sector"]


def test_family_sector_buy_recommendation_is_surfaced_for_held_parent(monkeypatch) -> None:
    """持仓「医疗」必须能看到同族细分「CXO」的买入推荐（2026-08 线上漏披露实例）。

    两个键是分开计算的方向状态，同日一边判退出、一边判可布局完全可能；精确匹配会让
    这类最容易被用户读成"自相矛盾"的组合恰好没有任何披露。
    """
    monkeypatch.setattr(
        "app.database.list_discovery_reports",
        lambda limit=1: [_summary("2026-06-10T01:00:00+00:00")],
    )
    monkeypatch.setattr(
        "app.database.get_discovery_report",
        lambda _id: _payload(
            [
                {
                    "fund_code": "000960",
                    "fund_name": "招商医药健康产业股票",
                    "sector_name": "CXO",
                    "action": "分批买入",
                    "entry_path": "confirmed_entry",
                }
            ]
        ),
    )

    cross = build_discovery_cross_reference([_holding("医疗")], decision_at=_DECISION_AT)

    rows = cross["buy_recommendations_by_sector"]["医疗"]
    assert [row["fund_code"] for row in rows] == ["000960"]
    assert rows[0]["relation"] == "same_family"
    assert rows[0]["sector_label"] == "CXO"


def test_exact_sector_rows_sort_before_family_rows(monkeypatch) -> None:
    """披露文案只点名第一行：同名命中信息量更高，必须排在同族口径之前。"""
    monkeypatch.setattr(
        "app.database.list_discovery_reports",
        lambda limit=1: [_summary("2026-06-10T01:00:00+00:00")],
    )
    monkeypatch.setattr(
        "app.database.get_discovery_report",
        lambda _id: _payload(
            [
                {
                    "fund_code": "000960",
                    "fund_name": "CXO基金",
                    "sector_name": "CXO",
                    "action": "分批买入",
                },
                {
                    "fund_code": "011111",
                    "fund_name": "医疗基金",
                    "sector_name": "医疗",
                    "action": "分批买入",
                },
            ]
        ),
    )

    cross = build_discovery_cross_reference([_holding("医疗")], decision_at=_DECISION_AT)

    rows = cross["buy_recommendations_by_sector"]["医疗"]
    assert [row["relation"] for row in rows] == ["same_sector", "same_family"]
    assert rows[0]["fund_code"] == "011111"


def test_yesterdays_report_is_not_referenced(monkeypatch) -> None:
    """昨天的推荐基于昨天的方向状态，引用它只会制造新的矛盾。"""
    monkeypatch.setattr(
        "app.database.list_discovery_reports",
        lambda limit=1: [_summary("2026-06-09T07:00:00+00:00")],
    )

    cross = build_discovery_cross_reference([_holding()], decision_at=_DECISION_AT)

    assert cross["available"] is False
    assert cross["reason"] == "no_same_day_discovery_report"


def test_no_reports_and_no_held_sectors_are_honest_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("app.database.list_discovery_reports", lambda limit=1: [])
    cross = build_discovery_cross_reference([_holding()], decision_at=_DECISION_AT)
    assert cross["available"] is False
    assert cross["reason"] == "no_discovery_reports"

    holding = _holding()
    holding.sector_name = None
    cross = build_discovery_cross_reference([holding], decision_at=_DECISION_AT)
    assert cross["reason"] == "no_held_sectors"


def test_read_failure_never_raises(monkeypatch) -> None:
    def _boom(limit=1):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.database.list_discovery_reports", _boom)
    cross = build_discovery_cross_reference([_holding()], decision_at=_DECISION_AT)
    assert cross["available"] is False
    assert cross["reason"] == "cross_reference_error"


# --------------------------------------------------------------------------
# 日报守卫侧：同板块持仓卡片带披露
# --------------------------------------------------------------------------

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


def _guard(facts) -> FundRecommendation:
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="观察",
            )
        ],
        [],
        AnalysisRequest(
            holdings=[_holding()],
            profile=InvestorProfile(
                max_drawdown_percent=15,
                concentration_limit_percent=100,
                expected_investment_amount=100_000,
                avoid_chasing=False,
            ),
        ),
        RiskAssessment(
            level="medium",
            weighted_return_percent=1.2,
            suggested_action="watch",
            alerts=[],
        ),
        _TODAY_NEWS,
        facts=facts,
    )
    return guarded[0]


def _cross_reference_facts(available: bool) -> dict:
    return {
        "holdings": [{"fund_code": "519674"}],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
        "discovery_cross_reference": {
            "available": available,
            "buy_recommendations_by_sector": (
                {
                    "半导体": [
                        {
                            "fund_code": "008888",
                            "fund_name": "更好的半导体基金",
                            "action": "分批买入",
                        }
                    ]
                }
                if available
                else {}
            ),
        },
    }


def test_holding_card_discloses_the_same_day_discovery_pick() -> None:
    rec = _guard(_cross_reference_facts(available=True))

    notes = "；".join(rec.validation_notes)
    assert "更好的半导体基金" in notes
    assert "停加" in notes and "卖掉方向" in notes
    # 披露不改变动作。
    assert rec.action == "观察"


def test_no_disclosure_without_a_same_day_report() -> None:
    rec = _guard(_cross_reference_facts(available=False))
    assert not any("发现基金今日报告" in note for note in rec.validation_notes)


def test_holding_card_family_pick_uses_separate_state_wording() -> None:
    """持仓「医疗」×推荐「CXO」：文案必须讲清是两条分开计算的方向状态，不能沿用
    "两侧共用同一套方向打分"——那句话对同族口径是错的，会把分歧说成不可能存在。"""
    facts = {
        "holdings": [{"fund_code": "519674"}],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
        "discovery_cross_reference": {
            "available": True,
            "buy_recommendations_by_sector": {
                "医疗": [
                    {
                        "fund_code": "000960",
                        "fund_name": "招商医药健康产业股票",
                        "action": "分批买入",
                        "sector_label": "CXO",
                        "relation": "same_family",
                    }
                ]
            },
        },
    }
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="招商前沿医疗保健股票A",
                action="观察",
            )
        ],
        [],
        AnalysisRequest(
            holdings=[_holding("医疗")],
            profile=InvestorProfile(
                max_drawdown_percent=15,
                concentration_limit_percent=100,
                expected_investment_amount=100_000,
                avoid_chasing=False,
            ),
        ),
        RiskAssessment(
            level="medium",
            weighted_return_percent=1.2,
            suggested_action="watch",
            alerts=[],
        ),
        _TODAY_NEWS,
        facts=facts,
    )
    notes = "；".join(guarded[0].validation_notes)
    assert "「CXO」口径" in notes
    assert "分开计算" in notes
    assert "总敞口" in notes
    # 同族措辞不得混入同名板块那句"共用同一套方向打分"。
    assert "共用同一套方向打分" not in notes


# --------------------------------------------------------------------------
# 发现侧：买入类推荐点名同方向持仓
# --------------------------------------------------------------------------


def _discovery_rec(action: str = "分批买入") -> DiscoveryRecommendation:
    return DiscoveryRecommendation(
        fund_code="008888",
        fund_name="更好的半导体基金",
        sector_name="半导体",
        action=action,
    )


def _discovery_facts_with_holding() -> dict:
    return {
        "portfolio": {
            "holdings_slim": [
                {
                    "fund_code": "519674",
                    "fund_name": "银河创新成长",
                    "sector_name": "半导体",
                }
            ]
        }
    }


def test_buy_recommendation_names_the_held_same_sector_fund() -> None:
    note = _held_same_sector_note(_discovery_rec(), _discovery_facts_with_holding())
    assert note is not None
    assert "银河创新成长" in note and "由日报按这只载体处理" in note


def test_buy_recommendation_names_the_held_family_fund() -> None:
    """推荐「CXO」×持仓「医疗」：同族口径必须点名，且说清两者状态分开计算。"""
    rec = DiscoveryRecommendation(
        fund_code="000960",
        fund_name="招商医药健康产业股票",
        sector_name="CXO",
        action="分批买入",
    )
    facts = {
        "portfolio": {
            "holdings_slim": [
                {
                    "fund_code": "011373",
                    "fund_name": "招商前沿医疗保健股票A",
                    "sector_name": "医疗",
                }
            ]
        }
    }
    note = _held_same_sector_note(rec, facts)
    assert note is not None
    assert "招商前沿医疗保健股票A" in note
    assert "「医疗」口径" in note and "「CXO」口径" in note
    assert "分开计算" in note and "总敞口" in note


def test_exact_sector_holding_is_named_before_family_holding() -> None:
    rec = DiscoveryRecommendation(
        fund_code="000960",
        fund_name="新CXO基金",
        sector_name="CXO",
        action="分批买入",
    )
    facts = {
        "portfolio": {
            "holdings_slim": [
                {"fund_code": "011373", "fund_name": "医疗持仓", "sector_name": "医疗"},
                {"fund_code": "012345", "fund_name": "CXO老持仓", "sector_name": "CXO"},
            ]
        }
    }
    note = _held_same_sector_note(rec, facts)
    assert note is not None
    # 同名持仓优先点名，走"共用同一套打分"的原措辞。
    assert "CXO老持仓" in note
    assert "共用同一套打分" in note


@pytest.mark.parametrize("action", ["建议关注", "等待回调"])
def test_non_buy_actions_do_not_get_the_note(action: str) -> None:
    assert _held_same_sector_note(_discovery_rec(action), _discovery_facts_with_holding()) is None


def test_no_note_without_a_same_sector_holding() -> None:
    facts = {
        "portfolio": {
            "holdings_slim": [
                {"fund_code": "017787", "fund_name": "煤炭基金", "sector_name": "煤炭"}
            ]
        }
    }
    assert _held_same_sector_note(_discovery_rec(), facts) is None
