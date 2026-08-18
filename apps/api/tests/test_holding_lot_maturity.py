"""持仓批次（acquisition lot）与赎回惩罚费窗口。

背景：日报的减仓语义此前写着「当前无逐笔 acquisition lot」。用户录入真实买卖交易后
这个前提不成立了：批次可以按先进先出真实重建，减仓建议能确定性地回答"现在赎回会不会
触发 7 天惩罚费、要等到哪天过窗"。

纪律：只披露、不否决——减仓是风险动作，费用贵不构成拦着用户降风险的理由。批次口径
必须与 `compute_effective_shares_map` 同一条基线过滤规则（基线日及之前的交易已折进
基线份额，再叠加是双重计数）。
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
from app.services.holding_lot_maturity import (
    build_holding_lot_maturity,
    build_lot_maturity_by_code,
    describe_reduction_lot_impact,
)
from app.services.recommendation_guard import apply_recommendation_guards
from app.services.risk import RiskAssessment

_AS_OF = "2026-06-10"


def _tx(
    *,
    direction: str,
    confirm_date: str,
    shares_delta: float,
    status: str = "confirmed",
) -> FundTransaction:
    return FundTransaction(
        id=f"tx-{direction}-{confirm_date}",
        fund_code="519674",
        fund_name="银河创新成长",
        direction=direction,
        amount_yuan=1000.0,
        trade_time=f"{confirm_date} 10:00:00",
        confirm_date=confirm_date,
        status=status,
        shares_delta=shares_delta,
        dedup_key=f"key-{direction}-{confirm_date}",
        created_at="2026-06-01T00:00:00+00:00",
    )


def _build(transactions, *, baseline_shares=None, baseline_date=None):
    return build_holding_lot_maturity(
        fund_code="519674",
        transactions=transactions,
        baseline_shares=baseline_shares,
        baseline_date=baseline_date,
        as_of_date=_AS_OF,
    )


# --------------------------------------------------------------------------
# 批次重建
# --------------------------------------------------------------------------


def test_baseline_plus_buys_form_ordered_lots() -> None:
    result = _build(
        [
            _tx(direction="buy", confirm_date="2026-06-08", shares_delta=100.0),
            _tx(direction="buy", confirm_date="2026-05-20", shares_delta=200.0),
        ],
        baseline_shares=500.0,
        baseline_date="2026-04-01",
    )

    assert result["available"] is True
    assert [row["source"] for row in result["lots"]] == [
        "baseline",
        "transaction",
        "transaction",
    ]
    assert result["total_lot_shares"] == pytest.approx(800.0)
    # 06-08 买入距 06-10 只有 2 天：在 7 天惩罚费窗口内。
    assert result["short_hold_lot_shares"] == pytest.approx(100.0)
    assert result["short_hold_share_percent"] == pytest.approx(12.5)
    assert result["next_penalty_free_date"] == "2026-06-15"
    # 基线批次的持有天数是下界。
    assert result["lots"][0]["hold_days_is_lower_bound"] is True


def test_sells_consume_the_oldest_lots_first() -> None:
    result = _build(
        [
            _tx(direction="buy", confirm_date="2026-05-20", shares_delta=200.0),
            _tx(direction="sell", confirm_date="2026-06-01", shares_delta=-550.0),
        ],
        baseline_shares=500.0,
        baseline_date="2026-04-01",
    )

    # 卖出 550：吃光基线 500，再吃 05-20 那批的 50。
    assert result["lot_count"] == 1
    only = result["lots"][0]
    assert only["confirm_date"] == "2026-05-20"
    assert only["shares"] == pytest.approx(150.0)
    assert result["unmatched_sell_shares"] == pytest.approx(0.0)
    assert result["coverage"] == "recorded_lots"


def test_oversell_is_disclosed_as_partial_records() -> None:
    """卖出对不上批次 = 交易记录不完整，必须披露、不得静默吞掉。"""
    result = _build(
        [
            _tx(direction="buy", confirm_date="2026-05-20", shares_delta=100.0),
            _tx(direction="sell", confirm_date="2026-06-01", shares_delta=-300.0),
        ],
    )
    assert result["available"] is False
    assert result["reason"] == "no_surviving_lots"
    assert result["unmatched_sell_shares"] == pytest.approx(200.0)

    partial = _build(
        [
            _tx(direction="buy", confirm_date="2026-05-20", shares_delta=100.0),
            _tx(direction="buy", confirm_date="2026-06-08", shares_delta=100.0),
            _tx(direction="sell", confirm_date="2026-06-09", shares_delta=-150.0),
        ],
    )
    assert partial["available"] is True
    assert partial["coverage"] == "recorded_lots"


def test_baseline_filter_matches_effective_shares_semantics() -> None:
    """基线日及之前的交易已折进基线份额，再叠加就是双重计数。"""
    result = _build(
        [
            _tx(direction="buy", confirm_date="2026-03-15", shares_delta=999.0),
            _tx(direction="buy", confirm_date="2026-04-01", shares_delta=888.0),
            _tx(direction="buy", confirm_date="2026-05-20", shares_delta=100.0),
        ],
        baseline_shares=500.0,
        baseline_date="2026-04-01",
    )
    assert result["total_lot_shares"] == pytest.approx(600.0)


def test_pending_and_shareless_transactions_are_skipped() -> None:
    result = _build(
        [
            _tx(direction="buy", confirm_date="2026-05-20", shares_delta=100.0),
            _tx(
                direction="buy",
                confirm_date="2026-06-09",
                shares_delta=50.0,
                status="pending",
            ),
        ],
    )
    assert result["total_lot_shares"] == pytest.approx(100.0)


def test_undated_baseline_never_counts_as_short_hold() -> None:
    """"不知道持有多久"不等于"在惩罚期"。"""
    result = _build([], baseline_shares=500.0, baseline_date=None)
    assert result["available"] is True
    assert result["undated_lot_count"] == 1
    assert result["short_hold_lot_shares"] == pytest.approx(0.0)
    assert result["lots"][0]["hold_days"] is None


# --------------------------------------------------------------------------
# 减仓触及判定
# --------------------------------------------------------------------------


def _maturity_with_fresh_tail() -> dict:
    return _build(
        [
            _tx(direction="buy", confirm_date="2026-06-08", shares_delta=100.0),
        ],
        baseline_shares=300.0,
        baseline_date="2026-04-01",
    )


def test_small_reduction_only_touches_seasoned_lots() -> None:
    note = describe_reduction_lot_impact(_maturity_with_fresh_tail(), -25.0)
    assert note is not None
    assert "均已持有满 7 天" in note


def test_full_reduction_touches_the_penalty_window() -> None:
    note = describe_reduction_lot_impact(_maturity_with_fresh_tail(), -100.0)
    assert note is not None
    assert "惩罚费窗口" in note and "25.0%" in note
    assert "2026-06-15" in note
    assert "费用不构成回避减仓的理由" in note


@pytest.mark.parametrize("percent", [None, 0.0, 10.0])
def test_non_reduction_percentages_produce_nothing(percent) -> None:
    assert describe_reduction_lot_impact(_maturity_with_fresh_tail(), percent) is None


def test_unavailable_maturity_produces_nothing() -> None:
    assert describe_reduction_lot_impact({"available": False}, -25.0) is None
    assert describe_reduction_lot_impact(None, -25.0) is None


# --------------------------------------------------------------------------
# 批量构建与 guard 集成
# --------------------------------------------------------------------------


class _Profile:
    fund_code = "519674"
    holding_shares = 300.0
    shares_baseline_date = "2026-04-01"


def test_build_by_code_groups_and_skips_funds_without_transactions(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.database.list_fund_transactions",
        lambda fund_code=None: [
            _tx(direction="buy", confirm_date="2026-06-08", shares_delta=100.0)
        ],
    )
    holdings = [
        Holding(fund_code="519674", fund_name="银河创新成长", holding_amount=10_000.0),
        Holding(fund_code="017787", fund_name="无交易基金", holding_amount=5_000.0),
    ]
    result = build_lot_maturity_by_code(
        holdings, [_Profile(), None], as_of_date=_AS_OF
    )
    assert set(result) == {"519674"}
    assert result["519674"]["available"] is True


_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


def test_guard_discloses_the_penalty_window_on_a_reduce_action() -> None:
    facts = {
        "holdings": [
            {
                "fund_code": "519674",
                "lot_maturity": _maturity_with_fresh_tail(),
            }
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
    }
    _, guarded = apply_recommendation_guards(
        [
            FundRecommendation(
                fund_code="519674",
                fund_name="银河创新成长",
                action="减仓评估",
            )
        ],
        [],
        AnalysisRequest(
            holdings=[
                Holding(
                    fund_code="519674",
                    fund_name="银河创新成长",
                    sector_name="半导体",
                    holding_amount=10_000.0,
                )
            ],
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
    assert rec.action == "减仓评估"
    # 减仓 1/4：只触及基线老批次，披露"不触发惩罚费"。
    assert rec.suggested_position_change_percent == pytest.approx(-25.0)
    assert any("均已持有满 7 天" in note for note in rec.validation_notes)
