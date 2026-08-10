"""板块标签必须以 `fund_primary_sectors` 权威身份表为准。

回归背景：快照与基金档案里的 `sector_name` 都只是权威身份表的反规范化副本。持仓穿透
把「万家宏观择时多策略混合C」的身份从名称残留「宏观择时多策略」纠正为「煤炭」之后，
这两份副本不会跟着变，于是列表页继续显示「宏观择时多策略」，而旁边的涨跌其实取自
煤炭板块——**标签和数字来自两个不同的板块**，比干脆没有标签更容易误导。
"""
from __future__ import annotations

from app.models import Holding
from app.services import portfolio_holdings_service as service


def _holding(fund_code: str, fund_name: str, sector_name: str | None) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name=fund_name,
        holding_amount=1000.0,
        return_percent=0.0,
        sector_name=sector_name,
    )


def test_authoritative_identity_replaces_stale_name_residue_label(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "get_fund_primary_sectors_by_codes",
        lambda codes: {
            "017787": {"sector_name": "煤炭", "source": "holdings_infer"},
        },
    )

    aligned = service.apply_authoritative_sector_labels(
        [_holding("017787", "万家宏观择时多策略混合C", "宏观择时多策略")]
    )

    assert [item.sector_name for item in aligned] == ["煤炭"]


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
