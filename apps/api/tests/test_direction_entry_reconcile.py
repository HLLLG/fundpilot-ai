"""入场契约与持仓真实时间线的核对（确认成交闭环）。

背景：入场契约来自 discovery 的 buy 决策事件，在**报告生成时**就冻结——它是"推荐"，
不是"成交回执"。真实账户里两类错位都会发生：

1. 用户在推荐**之前**就持有这只基金（截图导入的老仓）——那笔推荐根本不是这笔持仓的
   入场，拿它的方向分当基线是把别的决策安到这笔仓上；
2. 用户看了推荐、拖了几周才买——推荐日的方向分早已不代表买入决策，"买入时 69 分"这句
   话从第一天就是错的。

处理沿用分类漂移的既有先例：能重定基线就重定（账本里有买入日附近的有证据分数），
不能就拒绝相对模式，但必须把原因披露出来，不许静默。
"""

from __future__ import annotations

import pytest

from app.models import Holding
from app.services import report_sector_opportunity as sector_ctx
from app.services import sector_direction_exit as exit_mod
from app.services.sector_direction_exit import (
    ENTRY_REBASE_TOLERANCE_DAYS,
    assess_direction_exit,
    reconcile_entry_contract_with_holding,
)

_EXIT_LINE = 52.0


def _contract(**overrides) -> dict:
    base = {
        "sector_label": "煤炭",
        "entry_date": "2026-06-04",
        "entry_state": "confirmed_entry",
        "entry_trend": 72.0,
        "entry_participation": 40.0,
        "entry_position_risk": 50.0,
        "entry_tranche_scale": 0.6,
        "thesis_event_id": "discovery:test:0:015788",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# reconcile_entry_contract_with_holding（纯函数）
# --------------------------------------------------------------------------


def test_purchase_within_tolerance_keeps_the_contract_untouched() -> None:
    result = reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date="2026-06-08",  # 推荐后 4 天，容差 5 天以内
        first_seen_date="2026-06-09",
    )
    assert result == _contract()


def test_holding_older_than_the_recommendation_is_disqualified() -> None:
    """购入日早于推荐日：这笔推荐不是这笔持仓的入场。"""
    result = reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date="2026-06-02",
        first_seen_date=None,
    )
    reason = result["disqualified_reason"]
    assert "购入日" in reason and "2026-06-02" in reason and "2026-06-04" in reason


def test_first_seen_before_the_recommendation_also_disqualifies() -> None:
    """首见日是实际买入日的上界：它早于推荐日即可确定持有在前。"""
    result = reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date=None,
        first_seen_date="2026-06-03",
    )
    assert "首次出现" in result["disqualified_reason"]


def test_first_seen_after_the_recommendation_proves_nothing() -> None:
    """首见晚于推荐不能确定什么——导入截图有延迟，不得据此重定或作废。"""
    result = reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date=None,
        first_seen_date="2026-07-20",
    )
    assert result == _contract()


def test_late_purchase_rebases_to_the_ledger_score_near_the_purchase_date() -> None:
    calls: list[tuple[str, str, str]] = []

    def loader(label: str, on_or_before: str, not_before: str):
        calls.append((label, on_or_before, not_before))
        return "2026-06-24", 61.5

    result = reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date="2026-06-25",  # 推荐后 21 天，远超容差
        first_seen_date=None,
        rebase_score_loader=loader,
    )

    assert result["entry_date"] == "2026-06-24"
    assert result["entry_trend"] == pytest.approx(61.5)
    # 原推荐日保留，供文案披露"基线取自买入日、推荐发生在 06-04"。
    assert result["entry_rebased_from"] == "2026-06-04"
    # 买入日的参与度/价格位置没有可信快照，清空而不是留推荐日的旧值冒充。
    assert result["entry_participation"] is None
    assert result["entry_position_risk"] is None
    # 承诺失效条件仍属于那笔推荐（用户按它买的），不清。
    assert result["entry_tranche_scale"] == pytest.approx(0.6)
    # loader 收到的是买入日与回看下限。
    assert calls == [("煤炭", "2026-06-25", "2026-06-20")]


def test_late_purchase_without_a_ledger_score_is_disqualified() -> None:
    result = reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date="2026-06-25",
        first_seen_date=None,
        rebase_score_loader=lambda *_a: None,
    )
    reason = result["disqualified_reason"]
    assert "2026-06-25" in reason and "2026-06-04" in reason
    assert str(ENTRY_REBASE_TOLERANCE_DAYS) in reason


def test_unparseable_dates_leave_the_contract_alone() -> None:
    """"不知道"不等于"错位"：解析不了就原样返回，与浮亏门禁对 None 的纪律一致。"""
    assert reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date="不是日期",
        first_seen_date=None,
    ) == _contract()
    assert reconcile_entry_contract_with_holding(
        _contract(entry_date=""),
        first_purchase_date="2026-06-02",
        first_seen_date=None,
    ) == _contract(entry_date="")


# --------------------------------------------------------------------------
# 消费侧：disqualified / rebased 契约在退出判定里的表现
# --------------------------------------------------------------------------


def test_disqualified_contract_falls_back_to_absolute_with_the_reason() -> None:
    contract = reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date="2026-06-02",
        first_seen_date=None,
    )
    result = assess_direction_exit(
        sector_label="煤炭",
        entry_state="ready_to_start",
        trend_strength=80.0,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=contract,
    )
    assert result["basis"] == "absolute"
    assert result["entry_reference"] is None
    assert "购入日" in result["entry_reference_note"]


def test_rebased_contract_drives_the_relative_baseline() -> None:
    contract = reconcile_entry_contract_with_holding(
        _contract(),
        first_purchase_date="2026-06-25",
        first_seen_date=None,
        rebase_score_loader=lambda *_a: ("2026-06-24", 61.5),
    )
    result = assess_direction_exit(
        sector_label="煤炭",
        entry_state="ready_to_start",
        trend_strength=80.0,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=contract,
    )
    assert result["basis"] == "relative_to_entry"
    reference = result["entry_reference"]
    assert reference["entry_date"] == "2026-06-24"
    assert reference["entry_trend"] == pytest.approx(61.5)
    assert reference["entry_rebased_from"] == "2026-06-04"


# --------------------------------------------------------------------------
# 集成：_attach_direction_exit 用档案里的日期做核对
# --------------------------------------------------------------------------


def _holding(code: str, sector: str) -> Holding:
    return Holding(
        fund_code=code,
        fund_name=f"基金{code}",
        sector_name=sector,
        holding_amount=10_000.0,
        holding_profit=0.0,
    )


class _Profile:
    def __init__(self, first_purchase_date: str | None, first_seen_date: str | None):
        self.first_purchase_date = first_purchase_date
        self.first_seen_date = first_seen_date


def test_attach_direction_exit_reconciles_against_the_profile(monkeypatch) -> None:
    monkeypatch.setattr(exit_mod, "load_direction_trend_history", lambda *_a, **_k: {})
    monkeypatch.setattr(
        exit_mod,
        "load_direction_entry_contracts",
        lambda _codes: {"015788": _contract()},
    )
    monkeypatch.setattr(
        "app.services.holding_profile_batch.resolve_matched_profiles",
        lambda _holdings, **_k: [_Profile("2026-06-02", None)],
    )

    held = {
        "煤炭": {
            "sector_label": "煤炭",
            "entry_state": "ready_to_start",
            "trend_strength_score": 80.0,
        }
    }
    by_code = sector_ctx._attach_direction_exit(
        held,
        holdings=[_holding("015788", "煤炭")],
        trade_date="2026-06-10",
    )

    # 推荐前已持有：板块行与逐基金行都退化为绝对模式并披露原因。
    assert held["煤炭"]["direction_exit"]["basis"] == "absolute"
    assert "购入日" in held["煤炭"]["direction_exit"]["entry_reference_note"]
    assert by_code["015788"]["basis"] == "absolute"


def test_attach_direction_exit_without_a_profile_keeps_the_contract(monkeypatch) -> None:
    """档案缺席（新库/读失败）时契约原样生效——"不知道"不等于"错位"。"""
    monkeypatch.setattr(exit_mod, "load_direction_trend_history", lambda *_a, **_k: {})
    monkeypatch.setattr(
        exit_mod,
        "load_direction_entry_contracts",
        lambda _codes: {"015788": _contract()},
    )
    monkeypatch.setattr(
        "app.services.holding_profile_batch.resolve_matched_profiles",
        lambda _holdings, **_k: [None],
    )

    held = {
        "煤炭": {
            "sector_label": "煤炭",
            "entry_state": "ready_to_start",
            "trend_strength_score": 80.0,
        }
    }
    by_code = sector_ctx._attach_direction_exit(
        held,
        holdings=[_holding("015788", "煤炭")],
        trade_date="2026-06-10",
    )

    assert by_code["015788"]["basis"] == "relative_to_entry"
    assert by_code["015788"]["entry_reference"]["entry_trend"] == pytest.approx(72.0)
