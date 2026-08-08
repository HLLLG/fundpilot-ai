"""日报 facts 的持有收益口径必须与界面「持有」列一致。

回归背景：`analysis_facts` 曾为规避「每只持仓一次 profile 单点查询」而内联了一份
无 profile 的展示指标简化实现，并且只在 `budget_enhancements=True` 时使用——而两条
LLM 路径（同步 `run_analysis` 与流式 `stream_analysis`）都是 `budget_enhancements=True`，
所以线上一直走的是那份简化版。`resolve_matched_profiles` 引入批量读之后性能理由已经
消失，但简化版仍然丢掉三个真实行为，其中「支付宝 OCR 持有收益已含当日」会让日报把
当日涨跌重复加一次，直接污染 prompt 反复强调的 `estimated_holding_return_percent`
（浮亏线、over_drawdown_limit、escalation 的 has_unrealized_gain 都读它）。
"""

import pytest

from app.models import FundProfile, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services import analysis_facts as analysis_facts_module
from app.services.analysis_facts import build_analysis_facts
from app.services.holding_estimates import build_holding_display_metrics


@pytest.fixture(autouse=True)
def _stub_market_enhancements(monkeypatch: pytest.MonkeyPatch) -> None:
    """本文件只验证持有收益口径，把全部板块/大盘增强项替换为静态空值。

    这些增强项本身是 best-effort 并发 IO；留着真实实现会让用例依赖网络与交易日。
    """
    monkeypatch.setattr(
        analysis_facts_module,
        "build_signal_backtest_context",
        lambda _labels: {"enabled": False, "has_data": False, "sectors": []},
    )
    monkeypatch.setattr(
        analysis_facts_module,
        "resolve_signal_guard_policy",
        lambda _holdings: {
            "enforce_reversal_block": True,
            "enforce_pullback_block": True,
            "tighten_tactical": False,
        },
    )
    monkeypatch.setattr(
        analysis_facts_module,
        "_build_sector_intraday_map",
        lambda _labels: {},
    )
    monkeypatch.setattr(
        analysis_facts_module,
        "build_stock_connect_flow_context",
        lambda **_kwargs: {"available": False},
    )
    monkeypatch.setattr(
        analysis_facts_module,
        "build_holding_sector_opportunity_context",
        lambda _holdings, **_kwargs: {
            "available": False,
            "held": {},
            "market_top": [],
            "sector_flow_by_label": {},
            "divergence_backtest": {},
        },
    )
    monkeypatch.setattr(
        analysis_facts_module,
        "build_market_breadth_signal",
        lambda _trade_date: {"available": False},
    )


def _investor_profile() -> InvestorProfile:
    return InvestorProfile(
        decision_style="conservative",
        max_drawdown_percent=15,
        concentration_limit_percent=100,
        expected_investment_amount=100_000,
    )


def _snapshot(holding: Holding) -> FundSnapshot:
    return FundSnapshot(
        fund_code=holding.fund_code,
        fund_name=holding.fund_name,
        source="test",
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        suggested_action="watch",
        weighted_return_percent=1.0,
        alerts=[],
    )


def _facts_row(
    holding: Holding,
    *,
    fund_profile: FundProfile | None,
    budget_enhancements: bool,
) -> dict:
    facts = build_analysis_facts(
        [holding],
        _risk(),
        [_snapshot(holding)],
        _investor_profile(),
        budget_enhancements=budget_enhancements,
        matched_profiles=[fund_profile],
    )
    return facts["holdings"][0]


def _ocr_cumulative_holding() -> Holding:
    """支付宝 OCR 行：持有收益与持有收益率互相自洽，已是含当日的累计值。

    10000 × 10 / 110 = 909.09，落在 `_ocr_holding_profit_is_cumulative` 的判定带内。
    """
    return Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name="半导体",
        holding_amount=10_000.0,
        holding_return_percent=10.0,
        holding_profit=909.09,
        sector_return_percent=3.0,
        daily_return_percent_source="sector_estimate",
    )


def test_ocr_cumulative_holding_return_is_not_double_counted() -> None:
    row = _facts_row(
        _ocr_cumulative_holding(),
        fund_profile=None,
        budget_enhancements=True,
    )

    # 持有收益率已含当日，不能再叠加 sector_return_percent=3（旧简化版会给 13.0）。
    assert row["estimated_holding_return_percent"] == pytest.approx(10.0)
    assert row["estimated_holding_profit"] == pytest.approx(909.09)
    assert row["holding_return_is_estimated"] is False


def test_deferred_profit_accrual_does_not_add_sector_estimate() -> None:
    """份额待确认（收益计提递延）期间不得把板块涨跌算成持有收益。"""
    from app.services.trading_session import get_effective_trade_date

    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长",
        sector_name="半导体",
        holding_amount=5_000.0,
        holding_return_percent=0.0,
        holding_profit=0.0,
        sector_return_percent=4.0,
        daily_return_percent_source="sector_estimate",
    )
    deferred = FundProfile(
        fund_code="519674",
        fund_name="银河创新成长",
        profit_accrual_deferred_until=get_effective_trade_date(),
    )

    row = _facts_row(holding, fund_profile=deferred, budget_enhancements=True)

    # 旧简化版不认 profile，会给 0 + 4 = 4.0。
    assert row["estimated_holding_return_percent"] == pytest.approx(0.0)
    assert row["estimated_holding_profit"] == pytest.approx(0.0)
    assert row["holding_return_is_estimated"] is False


@pytest.mark.parametrize(
    "holding",
    [
        pytest.param(_ocr_cumulative_holding(), id="ocr_cumulative"),
        pytest.param(
            Holding(
                fund_code="161725",
                fund_name="招商中证白酒",
                sector_name="白酒",
                holding_amount=8_000.0,
                holding_return_percent=-6.0,
                holding_profit=-510.64,
                sector_return_percent=-1.5,
                daily_return_percent_source="sector_estimate",
            ),
            id="intraday_estimate",
        ),
        pytest.param(
            Holding(
                fund_code="110022",
                fund_name="易方达消费行业",
                sector_name="食品饮料",
                holding_amount=12_000.0,
                holding_return_percent=4.0,
                holding_profit=461.54,
                daily_return_percent=0.8,
                daily_return_percent_source="official_nav",
            ),
            id="official_nav",
        ),
    ],
)
def test_budget_and_plain_paths_share_one_holding_return_contract(holding: Holding) -> None:
    """两条 facts 路径与界面「持有」列必须完全同源，不允许再出现第二份口径。"""
    expected = build_holding_display_metrics(holding, profile=None)

    budget_row = _facts_row(holding, fund_profile=None, budget_enhancements=True)
    plain_row = _facts_row(holding, fund_profile=None, budget_enhancements=False)

    for row in (budget_row, plain_row):
        assert row["holding_return_percent"] == expected["holding_return_percent_settled"]
        assert row["estimated_holding_return_percent"] == pytest.approx(
            round(float(expected["estimated_holding_return_percent"] or 0), 4)
        )
        assert row["estimated_holding_profit"] == expected["estimated_holding_profit"]
        assert row["holding_return_is_estimated"] == expected["holding_return_is_estimated"]
