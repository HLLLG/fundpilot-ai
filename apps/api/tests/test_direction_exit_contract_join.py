"""入场契约必须按基金归并，分类漂移要披露而不是静默丢弃。

回归背景（2026-08-11 线上实测，user=1）：

`load_direction_entry_contracts` 确实取回了 4 份入场契约，但 13:30 那份日报里只有 3 只
基金拿到 `basis=relative_to_entry`。掉队的是 015788：

  - 2026-08-06 被荐基推荐买入时，`recommendation.sector_name` 记的是「信创」；
  - 今天这只基金归到「数字经济」；
  - `_attach_direction_exit` 用**契约里记录的板块名**做 key，于是契约落在
    `contract_by_label["信创"]`，而 `held` 里的 key 是「数字经济」，永远查不到；
  - 结果 `entry_reference=null`、`basis=absolute`，而且**没有任何解释**——用户只看到
    「趋势 73.3 仍在退出线上方」，不知道系统其实握着那笔买入记录。

两条契约：
1. 归并按**基金当前所属板块**，让契约至少能进到索引里；
2. 分类漂移时仍然拒绝相对模式（拿两个成分篮子的分数对比会给出错误基线，比没有基线更
   危险），但必须把原因作为 `entry_reference_note` 披露出来；
3. 逐基金退出判定：板块行只能采用同方向最早那笔买入，每只基金要能用回自己的契约。
"""

from __future__ import annotations

import pytest

from app.models import Holding
from app.services import report_sector_opportunity as sector_ctx
from app.services import sector_direction_exit as exit_mod
from app.services.sector_direction_exit import assess_direction_exit

_EXIT_LINE = 52.0


def _contract(
    *,
    sector_label: str,
    entry_date: str = "2026-08-06",
    entry_trend: float = 72.72,
) -> dict:
    return {
        "sector_label": sector_label,
        "entry_date": entry_date,
        "entry_state": "confirmed_entry",
        "entry_trend": entry_trend,
        "entry_participation": 14.65,
        "entry_position_risk": 96.11,
        "entry_tranche_scale": 0.6,
        "thesis_event_id": "discovery:test:3:015788",
    }


# --- 契约 2：漂移要披露 -----------------------------------------------------


def test_matching_label_uses_the_entry_baseline() -> None:
    result = assess_direction_exit(
        sector_label="信创",
        entry_state="ready_to_start",
        trend_strength=73.32,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract(sector_label="信创"),
    )
    assert result["basis"] == "relative_to_entry"
    assert result["entry_reference"]["entry_trend"] == pytest.approx(72.72)
    assert result["entry_reference_note"] is None


def test_drifted_label_refuses_the_baseline_but_says_why() -> None:
    """015788 的真实形状：买入时记「信创」，现在归「数字经济」。"""
    result = assess_direction_exit(
        sector_label="数字经济",
        entry_state="ready_on_pullback",
        trend_strength=73.32,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=_contract(sector_label="信创"),
    )
    # 仍然不做相对比较——两个篮子的分数不可比。
    assert result["basis"] == "absolute"
    assert result["entry_reference"] is None
    # 但必须解释清楚，而不是静默为 null。
    note = result["entry_reference_note"]
    assert note is not None
    assert "信创" in note and "数字经济" in note
    assert "2026-08-06" in note


def test_no_contract_has_no_note() -> None:
    """压根没有买入记录（截图导入的持仓）时不该凭空多出一句解释。"""
    result = assess_direction_exit(
        sector_label="煤炭",
        entry_state="ready_to_start",
        trend_strength=80.31,
        exit_trend_threshold=_EXIT_LINE,
        entry_contract=None,
    )
    assert result["basis"] == "absolute"
    assert result["entry_reference"] is None
    assert result["entry_reference_note"] is None


# --- 契约 1/3：归并与逐基金判定 ---------------------------------------------


def _holding(code: str, sector: str, *, profit: float = 0.0) -> Holding:
    return Holding(
        fund_code=code,
        fund_name=f"基金{code}",
        sector_name=sector,
        holding_amount=10_000.0,
        holding_profit=profit,
    )


def _held(**rows: float) -> dict[str, dict]:
    return {
        label: {
            "sector_label": label,
            "entry_state": "ready_to_start",
            "trend_strength_score": trend,
        }
        for label, trend in rows.items()
    }


def test_contract_is_joined_by_the_funds_current_sector(monkeypatch) -> None:
    """按基金当前板块归并：契约至少要能进到 held 的索引里。"""
    monkeypatch.setattr(
        exit_mod, "load_direction_trend_history", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        exit_mod,
        "load_direction_entry_contracts",
        lambda _codes: {"015788": _contract(sector_label="信创")},
    )

    held = _held(数字经济=73.32)
    by_code = sector_ctx._attach_direction_exit(
        held,
        holdings=[_holding("015788", "数字经济")],
        trade_date="2026-08-11",
    )

    # 板块行拿到了契约（此前 key 是「信创」，这里压根查不到）。
    exit_row = held["数字经济"]["direction_exit"]
    assert exit_row["entry_reference_note"] is not None
    # 逐基金那份也在，且解释同源。
    assert by_code["015788"]["entry_reference_note"] == exit_row["entry_reference_note"]


def test_each_fund_uses_its_own_contract_not_the_earliest_in_the_sector(
    monkeypatch,
) -> None:
    """同一方向两只基金：板块行只能取最早那笔，逐基金那份必须各归各。"""
    monkeypatch.setattr(
        exit_mod, "load_direction_trend_history", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        exit_mod,
        "load_direction_entry_contracts",
        lambda _codes: {
            "000711": _contract(
                sector_label="医疗", entry_date="2026-08-04", entry_trend=60.0
            ),
            "011373": _contract(
                sector_label="医疗", entry_date="2026-08-10", entry_trend=92.16
            ),
        },
    )

    held = _held(医疗=93.14)
    by_code = sector_ctx._attach_direction_exit(
        held,
        holdings=[_holding("000711", "医疗"), _holding("011373", "医疗")],
        trade_date="2026-08-11",
    )

    # 板块行按最早买入（08-04, 60.0）。
    assert held["医疗"]["direction_exit"]["entry_reference"]["entry_date"] == "2026-08-04"
    # 逐基金各用自己的那笔。
    assert by_code["000711"]["entry_reference"]["entry_trend"] == pytest.approx(60.0)
    assert by_code["011373"]["entry_reference"]["entry_trend"] == pytest.approx(92.16)


def test_holdings_without_a_contract_are_absent_from_the_per_fund_map(
    monkeypatch,
) -> None:
    """没有自己契约的基金不单独算——板块行那份已经是它能得到的最好判定。"""
    monkeypatch.setattr(
        exit_mod, "load_direction_trend_history", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        exit_mod, "load_direction_entry_contracts", lambda _codes: {}
    )

    held = _held(煤炭=80.31)
    by_code = sector_ctx._attach_direction_exit(
        held,
        holdings=[_holding("017787", "煤炭")],
        trade_date="2026-08-11",
    )

    assert by_code == {}
    assert held["煤炭"]["direction_exit"]["basis"] == "absolute"


def test_per_fund_exit_wins_over_the_sector_row_on_the_holding(monkeypatch) -> None:
    """`analysis_facts` 必须给持仓行挂上属于它自己的那一份。"""
    from app.models import InvestorProfile
    from app.services.analysis_facts import _attach_escalation_to_holdings

    per_fund = [
        {
            "fund_code": "011373",
            "sector_opportunity": {
                "sector_label": "医疗",
                "direction_exit": {"exit_state": "hold", "min_bucket": None,
                                   "entry_reference": {"entry_date": "2026-08-04"}},
            },
        }
    ]
    _attach_escalation_to_holdings(
        per_fund,
        market_breadth=None,
        profile=InvestorProfile(
            decision_style="tactical",
            max_drawdown_percent=15,
            concentration_limit_percent=100,
            expected_investment_amount=100_000,
            avoid_chasing=False,
        ),
        direction_exit_by_fund_code={
            "011373": {
                "exit_state": "hold",
                "min_bucket": None,
                "entry_reference": {"entry_date": "2026-08-10"},
            }
        },
    )

    assert per_fund[0]["direction_exit"]["entry_reference"]["entry_date"] == "2026-08-10"
