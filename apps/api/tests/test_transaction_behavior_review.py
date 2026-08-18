"""用户真实买卖行为与系统建议的对照（纯披露）。

背景：用户录入真实交易后，系统第一次能看见"建议之外真实发生了什么"。两条契约：

1. 最终动作与用户近几天的真实操作方向相反时（建议加仓而你刚卖过 / 建议减仓而你刚
   买过），必须把那笔操作摆出来——但**只披露、不改动作**：方向证据独立于用户的资金
   需求，系统无从知道那笔操作的动机；
2. 组合级对照（aligned / contrary / neutral / no_advice）只作背景事实，明文禁止据此
   批评用户或反向修正建议。
"""

from __future__ import annotations

import pytest

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    FundTransaction,
    Holding,
    InvestorProfile,
    NewsItem,
)
from app.services.recommendation_guard import apply_recommendation_guards
from app.services.risk import RiskAssessment
from app.services.transaction_behavior_review import (
    build_transaction_behavior_review,
    recent_transaction_conflict_note,
    summarize_recent_transactions_by_code,
)

_AS_OF = "2026-06-10"


def _tx(
    *,
    direction: str,
    trade_date: str,
    fund_code: str = "519674",
    status: str = "confirmed",
) -> FundTransaction:
    return FundTransaction(
        id=f"tx-{direction}-{trade_date}",
        fund_code=fund_code,
        fund_name="银河创新成长",
        direction=direction,
        amount_yuan=1000.0,
        trade_time=f"{trade_date} 10:00:00",
        confirm_date=trade_date,
        status=status,
        shares_delta=100.0 if direction == "buy" else -100.0,
        dedup_key=f"key-{direction}-{trade_date}",
        created_at="2026-06-01T00:00:00+00:00",
    )


def _holding(code: str = "519674") -> Holding:
    return Holding(
        fund_code=code,
        fund_name="银河创新成长",
        sector_name="半导体",
        holding_amount=10_000.0,
    )


# --------------------------------------------------------------------------
# 近期交易摘要
# --------------------------------------------------------------------------


def test_summary_keeps_only_confirmed_window_transactions(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.database.list_fund_transactions",
        lambda fund_code=None: [
            _tx(direction="buy", trade_date="2026-06-05"),
            _tx(direction="sell", trade_date="2026-06-08"),
            _tx(direction="sell", trade_date="2026-04-01"),  # 窗口外
            _tx(direction="buy", trade_date="2026-06-09", status="pending"),
            _tx(direction="buy", trade_date="2026-06-09", fund_code="000001"),  # 非持仓
        ],
    )

    result = summarize_recent_transactions_by_code([_holding()], as_of_date=_AS_OF)

    assert set(result) == {"519674"}
    row = result["519674"]
    assert row["buy_count"] == 1 and row["sell_count"] == 1
    assert row["last_sell"]["trade_date"] == "2026-06-08"
    assert row["last_buy"]["trade_date"] == "2026-06-05"


# --------------------------------------------------------------------------
# 反向交易披露
# --------------------------------------------------------------------------


def _recent(*, last_sell: str | None = None, last_buy: str | None = None) -> dict:
    return {
        "available": True,
        "as_of_date": _AS_OF,
        "buy_count": 1 if last_buy else 0,
        "sell_count": 1 if last_sell else 0,
        "last_buy": {"trade_date": last_buy} if last_buy else None,
        "last_sell": {"trade_date": last_sell} if last_sell else None,
    }


def test_add_after_a_recent_sell_is_disclosed() -> None:
    note = recent_transaction_conflict_note(_recent(last_sell="2026-06-07"), "分批加仓")
    assert note is not None
    assert "2026-06-07" in note and "卖出过" in note


def test_stale_sell_outside_the_window_is_silent() -> None:
    assert (
        recent_transaction_conflict_note(_recent(last_sell="2026-06-01"), "分批加仓")
        is None
    )


def test_reduce_after_a_recent_buy_is_disclosed() -> None:
    note = recent_transaction_conflict_note(_recent(last_buy="2026-06-09"), "减仓评估")
    assert note is not None
    assert "买入过" in note


@pytest.mark.parametrize("action", ["观察", "暂停追涨"])
def test_passive_actions_never_conflict(action: str) -> None:
    assert (
        recent_transaction_conflict_note(
            _recent(last_sell="2026-06-09", last_buy="2026-06-09"), action
        )
        is None
    )


def test_missing_summary_is_silent() -> None:
    assert recent_transaction_conflict_note(None, "分批加仓") is None
    assert recent_transaction_conflict_note({"available": False}, "分批加仓") is None


# --------------------------------------------------------------------------
# 组合级对照
# --------------------------------------------------------------------------


def _advice_event(*, decision_date: str, action: str, fund_code: str = "519674") -> dict:
    return {
        "fund_code": fund_code,
        "decision_date": decision_date,
        "final_action": action,
    }


def test_review_classifies_each_transaction_against_the_advice(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.database.list_fund_transactions",
        lambda fund_code=None: [
            _tx(direction="sell", trade_date="2026-06-08"),  # 当日建议减仓 → aligned
            _tx(direction="sell", trade_date="2026-06-05"),  # 前一日建议加仓 → contrary
            _tx(direction="buy", trade_date="2026-06-02"),   # 当日建议观察 → neutral
            _tx(direction="buy", trade_date="2026-05-20"),   # 窗口内无建议 → no_advice
        ],
    )
    monkeypatch.setattr(
        "app.services.decision_repository.list_decision_events",
        lambda **_kwargs: [
            _advice_event(decision_date="2026-06-08", action="减仓评估"),
            _advice_event(decision_date="2026-06-04", action="分批加仓"),
            _advice_event(decision_date="2026-06-02", action="观察"),
        ],
    )

    review = build_transaction_behavior_review([_holding()], as_of_date=_AS_OF)

    assert review["available"] is True
    assert review["counts"] == {
        "aligned": 1,
        "contrary": 1,
        "neutral": 1,
        "no_advice": 1,
    }
    by_date = {row["trade_date"]: row for row in review["rows"]}
    assert by_date["2026-06-08"]["verdict"] == "aligned"
    assert by_date["2026-06-05"]["verdict"] == "contrary"
    assert by_date["2026-06-05"]["advice_date"] == "2026-06-04"
    assert by_date["2026-06-02"]["verdict"] == "neutral"
    assert by_date["2026-05-20"]["verdict"] == "no_advice"
    assert "不得据此批评用户" in review["instruction"]


def test_review_without_transactions_is_honest(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.database.list_fund_transactions", lambda fund_code=None: []
    )
    monkeypatch.setattr(
        "app.services.decision_repository.list_decision_events",
        lambda **_kwargs: [],
    )
    review = build_transaction_behavior_review([_holding()], as_of_date=_AS_OF)
    assert review["available"] is False
    assert review["reason"] == "no_transactions_in_window"


# --------------------------------------------------------------------------
# guard 集成：反向交易披露挂在最终动作上
# --------------------------------------------------------------------------

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


def test_guard_discloses_a_recent_sell_on_an_add_action() -> None:
    facts = {
        "holdings": [
            {
                "fund_code": "519674",
                "recent_transactions": _recent(last_sell="2026-06-08"),
                "sector_opportunity": {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "direction_score": 76.5,
                    "entry_state": "ready_to_start",
                    "raw_entry_state": "ready_to_start",
                    "opportunity_available": True,
                    "confidence": "高",
                    "track": "momentum",
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
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="分批加仓",
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

    rec = guarded[0]
    assert rec.action == "分批加仓"
    assert any("卖出过" in note for note in rec.validation_notes)
