"""板块标签必须以 `fund_primary_sectors` 权威身份表为准。

宏观择时 / 灵活配置这类无法确定单一板块的基金，权威表也不该写死重仓切片猜测。
"""
from __future__ import annotations

from app.models import FundProfile, Holding
from app.services import portfolio_holdings_service as service


def _holding(fund_code: str, fund_name: str, sector_name: str | None) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name=fund_name,
        holding_amount=1000.0,
        return_percent=0.0,
        sector_name=sector_name,
    )


def test_authoritative_identity_does_not_stamp_timing_strategy_fund(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "get_fund_primary_sectors_by_codes",
        lambda codes: {
            "017787": {"sector_name": "煤炭", "source": "holdings_infer"},
            "000960": {"sector_name": "CXO", "source": "holdings_infer"},
        },
    )

    aligned = service.apply_authoritative_sector_labels(
        [
            _holding("017787", "万家宏观择时多策略混合C", "煤炭"),
            _holding("000960", "招商医疗保健股票A", "医疗保健"),
        ]
    )

    assert [item.sector_name for item in aligned] == [None, "医疗"]
    assert aligned[1].intraday_index_name != "国证CXO"


def test_profile_guozheng_cxo_does_not_stick_on_named_healthcare_fund() -> None:
    holding = service._overlay_profile_onto_holding(
        _holding("011373", "招商前沿医疗保健股票A", "CXO").model_copy(
            update={"intraday_index_name": "国证CXO"}
        ),
        FundProfile(
            fund_code="011373",
            fund_name="招商前沿医疗保健股票A",
            aliases=["招商前沿医疗保健股票A"],
            holding_amount=1000,
            source="alipay-overview",
            sector_name="CXO",
            intraday_index_name="国证CXO",
        ),
        identity_row={
            "sector_name": "CXO",
            "intraday_index_name": "国证CXO",
            "source": "holdings_infer",
        },
    )

    assert holding.sector_name == "医疗"
    assert holding.intraday_index_name != "国证CXO"


def test_identity_label_that_is_not_a_board_name_keeps_the_usable_copy(monkeypatch) -> None:
    """身份表里也有「军工」这种当前板块表查不到的写法，此时保留可用的「国防军工」。"""
    monkeypatch.setattr(
        service,
        "get_fund_primary_sectors_by_codes",
        lambda codes: {"015945": {"sector_name": "军工", "source": "holdings_infer"}},
    )

    aligned = service.apply_authoritative_sector_labels(
        [_holding("015945", "易方达国防军工混合C", "国防军工")]
    )

    assert [item.sector_name for item in aligned] == ["国防军工"]


def test_missing_identity_row_leaves_the_holding_untouched(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "get_fund_primary_sectors_by_codes",
        lambda codes: {},
    )

    holdings = [_holding("002610", "博时黄金ETF联接A", "黄金")]
    aligned = service.apply_authoritative_sector_labels(holdings)

    assert [item.sector_name for item in aligned] == ["黄金"]


def test_identity_read_failure_does_not_break_the_holdings_list(monkeypatch) -> None:
    """身份表不可用时必须退回副本，而不是让持仓列表打不开。"""

    def _boom(codes):
        raise RuntimeError("primary sector table unavailable")

    monkeypatch.setattr(service, "get_fund_primary_sectors_by_codes", _boom)

    aligned = service.apply_authoritative_sector_labels(
        [_holding("017787", "万家宏观择时多策略混合C", "宏观择时多策略")]
    )

    assert [item.sector_name for item in aligned] == ["宏观择时多策略"]


def test_placeholder_fund_codes_are_not_looked_up(monkeypatch) -> None:
    calls: list[set[str]] = []

    def _record(codes):
        calls.append(set(codes))
        return {}

    monkeypatch.setattr(service, "get_fund_primary_sectors_by_codes", _record)

    service.apply_authoritative_sector_labels(
        [_holding("000000", "未查到代码的基金", "半导体")]
    )

    assert calls == []
