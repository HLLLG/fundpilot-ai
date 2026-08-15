"""被动指数基金：合同跟踪指数优先于持仓穿透。

持仓穿透会把南方黄金股C 写成「贵金属」、把国泰国证房地产行业指数A 写成
无 index_code 的「房地产」。数字优先级 holdings_infer(70) > benchmark(65)
不能继续挡住合同跟踪指数。主动基金仍由持仓穿透胜出。
"""

from __future__ import annotations

from app.services.fund_primary_sector_service import (
    _can_upsert_primary_sector,
    _is_passive_index_fund_name,
    resolve_primary_sector,
)
from app.services.fund_primary_sector_types import PrimarySectorRecord


def test_short_gold_equity_name_is_treated_as_passive() -> None:
    assert _is_passive_index_fund_name("南方黄金股C") is True
    assert _is_passive_index_fund_name("南方黄金股A") is True
    assert _is_passive_index_fund_name("国泰国证房地产行业指数A") is True
    assert _is_passive_index_fund_name("博时黄金ETF联接A") is True
    assert _is_passive_index_fund_name("易方达消费行业股票") is False


def test_passive_benchmark_may_overwrite_holdings_infer() -> None:
    existing = {
        "source": "holdings_infer",
        "sector_name": "贵金属",
        "detail": {"fund_name": "南方黄金股C"},
    }
    assert (
        _can_upsert_primary_sector(
            existing, "benchmark_index", fund_name="南方黄金股C"
        )
        is True
    )
    assert (
        _can_upsert_primary_sector(
            existing, "precompute_benchmark", fund_name="国泰国证房地产行业指数A"
        )
        is True
    )


def test_passive_holdings_infer_must_not_overwrite_benchmark() -> None:
    existing = {
        "source": "benchmark_index",
        "sector_name": "黄金股",
        "detail": {"fund_name": "南方黄金股C", "index_code": "931238"},
    }
    assert (
        _can_upsert_primary_sector(
            existing, "holdings_infer", fund_name="南方黄金股C"
        )
        is False
    )
    existing_precompute = {
        "source": "precompute_benchmark",
        "sector_name": "房地产",
        "detail": {"fund_name": "国泰国证房地产行业指数A", "index_code": "399393"},
    }
    assert (
        _can_upsert_primary_sector(
            existing_precompute,
            "precompute_holdings",
            fund_name="国泰国证房地产行业指数A",
        )
        is False
    )


def test_active_fund_holdings_infer_still_blocks_benchmark() -> None:
    existing = {
        "source": "holdings_infer",
        "sector_name": "煤炭",
        "detail": {"fund_name": "万家宏观择时多策略混合C"},
    }
    assert (
        _can_upsert_primary_sector(
            existing, "benchmark_index", fund_name="万家宏观择时多策略混合C"
        )
        is False
    )


def test_existing_holdings_infer_row_does_not_block_passive_benchmark(
    monkeypatch,
) -> None:
    from app.services import fund_primary_sector_service as svc

    monkeypatch.setattr(
        svc,
        "get_fund_primary_sector",
        lambda code: {
            "fund_code": "021959",
            "sector_name": "贵金属",
            "source": "holdings_infer",
            "intraday_index_name": None,
            "detail": {"fund_name": "南方黄金股C"},
        },
    )
    monkeypatch.setattr(svc, "load_fresh_global_sector", lambda code: None)
    monkeypatch.setattr(
        svc,
        "_resolve_from_benchmark_index",
        lambda *args, **kwargs: PrimarySectorRecord(
            fund_code="021959",
            sector_name="黄金股",
            intraday_index_name="沪港深黄金",
            source="benchmark_index",
            confidence=0.68,
            detail={"index_code": "931238"},
        ),
    )
    monkeypatch.setattr(
        svc,
        "_resolve_from_holdings_infer",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("被动指数基金不应先走持仓穿透")
        ),
    )

    record = resolve_primary_sector(
        "021959",
        fund_name="南方黄金股C",
        fetch_benchmark=True,
        fetch_holdings_infer=True,
    )

    assert record is not None
    assert record.sector_name == "黄金股"
    assert record.intraday_index_name == "沪港深黄金"
    assert record.source == "benchmark_index"
