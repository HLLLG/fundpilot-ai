"""板块补跑必须按基金代码增量，而不是「跑过一轮就永久收工」。

回归背景：原实现在状态文件里只记 `completed_at`，跑过一次之后整体跳过。于是那次之后
**新加入的基金永远补不上板块**——主动管理基金的正式身份只能靠季报重仓行业穿透得出，
而穿透只在不带时间预算的「精确刷新」里才跑（实测 6 只 13~16s），用户不点就一直空着。
"""
from __future__ import annotations

import json

import pytest

from app.models import Holding
from app.services import fund_primary_sector_backfill as backfill
from app.services import fund_primary_sector_service
from app.services.fund_primary_sector_types import PrimarySectorRecord


def _holding(fund_code: str, fund_name: str, sector_name: str | None = None) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name=fund_name,
        holding_amount=1000.0,
        return_percent=0.0,
        sector_name=sector_name,
    )


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """把补跑的外部依赖全部替换成内存桩，只测调度/记账逻辑。"""
    state = {
        "holdings": [_holding("012200", "新华鑫科技3个月滚动持有灵活配置混合A")],
        "resolve_calls": [],
        "raise_for": set(),
        "persisted": [],
    }

    status_file = tmp_path / "backfill_status.json"
    monkeypatch.setattr(backfill, "_status_path", lambda: status_file)
    monkeypatch.setattr(backfill, "_PER_CODE_SLEEP_SECONDS", 0)
    monkeypatch.setattr(backfill, "list_distinct_portfolio_user_ids", lambda: [1])
    monkeypatch.setattr(
        backfill,
        "load_persisted_holdings",
        lambda **_kwargs: (list(state["holdings"]), "snapshot", None, None),
    )
    monkeypatch.setattr(backfill, "get_fund_primary_sectors_by_codes", lambda codes: {})
    monkeypatch.setattr(backfill, "get_fund_primary_sector", lambda code: None)
    monkeypatch.setattr(
        backfill,
        "persist_holdings_after_sector_refresh",
        lambda holdings, **_kwargs: state["persisted"].append(list(holdings)) or holdings,
    )

    def fake_resolve(code, *, fund_name=None, **_kwargs):
        state["resolve_calls"].append(code)
        if code in state["raise_for"]:
            raise RuntimeError("simulated network failure")
        return PrimarySectorRecord(
            fund_code=code,
            sector_name="半导体材料",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.89,
            detail={},
        )

    monkeypatch.setattr(fund_primary_sector_service, "resolve_primary_sector", fake_resolve)
    monkeypatch.setattr(
        fund_primary_sector_service,
        "_record_should_override_holding_sector",
        lambda holding, record: True,
    )
    monkeypatch.setattr(
        fund_primary_sector_service,
        "_usable_intraday_index_name",
        lambda index_name, sector_name: index_name,
    )
    return state, status_file


def _read_status(status_file) -> dict:
    return json.loads(status_file.read_text(encoding="utf-8"))


def test_second_run_skips_already_attempted_codes(harness) -> None:
    state, status_file = harness

    first = backfill.backfill_primary_sectors_for_existing_holdings()
    assert first.get("skipped") is None
    assert state["resolve_calls"] == ["012200"]
    assert _read_status(status_file)["attempted_codes"] == ["012200"]

    second = backfill.backfill_primary_sectors_for_existing_holdings()
    assert second["skipped"] == "no_new_codes"
    # 关键：没有第二次解析调用，也就没有第二次网络/LLM 开销
    assert state["resolve_calls"] == ["012200"]


def test_newly_added_fund_is_picked_up_after_a_completed_run(harness) -> None:
    """这是原实现的核心缺陷：跑过一轮之后新加的基金再也补不上。"""
    state, _ = harness

    backfill.backfill_primary_sectors_for_existing_holdings()
    assert state["resolve_calls"] == ["012200"]

    state["holdings"].append(_holding("017787", "万家宏观择时多策略混合C"))
    third = backfill.backfill_primary_sectors_for_existing_holdings()

    assert third.get("skipped") is None
    assert third["codes_new_this_run"] == 1
    assert state["resolve_calls"] == ["012200", "017787"]


def test_failed_attempt_is_retried_on_the_next_run(harness) -> None:
    """网络抖动导致的一次失败不该让这只基金永久失去补板块的机会。"""
    state, status_file = harness
    state["raise_for"] = {"012200"}

    backfill.backfill_primary_sectors_for_existing_holdings()
    assert state["resolve_calls"] == ["012200"]
    assert _read_status(status_file)["attempted_codes"] == []

    state["raise_for"] = set()
    second = backfill.backfill_primary_sectors_for_existing_holdings()

    assert second.get("skipped") is None
    assert state["resolve_calls"] == ["012200", "012200"]
    assert _read_status(status_file)["attempted_codes"] == ["012200"]


def test_legacy_status_file_without_attempted_codes_runs_once_more(harness) -> None:
    """升级前的状态文件只有 completed_at；不能因此把当前仍缺板块的基金也跳过。"""
    state, status_file = harness
    status_file.write_text(
        json.dumps({"completed_at": "2026-07-01T07:58:17Z", "codes_resolved": 7}),
        encoding="utf-8",
    )

    result = backfill.backfill_primary_sectors_for_existing_holdings()

    assert result.get("skipped") is None
    assert state["resolve_calls"] == ["012200"]
    # 首次完成时间保留，便于观测这套记录是什么时候建立的
    assert _read_status(status_file)["completed_at"] == "2026-07-01T07:58:17Z"


def test_force_reruns_every_pending_code(harness) -> None:
    state, _ = harness

    backfill.backfill_primary_sectors_for_existing_holdings()
    backfill.backfill_primary_sectors_for_existing_holdings(force=True)

    assert state["resolve_calls"] == ["012200", "012200"]
