"""同类分位：要区分「这只基金缺值」与「目录压根不带这一列」。

回归背景（2026-08-11 线上实测）：

同类全集目录缓存 20000 行里，`max_drawdown_1y_percent` 与 `fund_scale_yi` 的非空数**都是 0**。
原因在数据源本身：全集来自东财基金排行接口（`akshare_subprocess` 里那段 rank 脚本），它的列里
只有 3 月/6 月/1 年收益，代码里对这两项是写死的 `None`。规模与回撤由**候选级** profile 富化提供，
而 profile 是逐只拉的，不可能覆盖两万只。

后果：`build_peer_rank` 要求所有 applicable 指标全部合格，而这两项永远合格不了，于是
`peer_data_qualified` 恒为 False、`status` 最好只到 `descriptive_only`、执行提额永不开放。而每份
报告里给出的原因是 `target_metric_value_missing`——读起来像"今天这只基金数据不巧缺了"，实际是
结构性的永不可得。运维据此完全判断不出该等下一份快照、还是该去补数据源。

本文件锁的是**口径诚实**，不是放松门禁：
* 全组零覆盖 → `peer_catalogue_metric_not_covered` / `not_covered_by_peer_catalogue`；
* 全组有覆盖但目标缺值 → 仍然是 `target_*`，那是真正的个体缺口；
* 两种情况都**继续**计入 `all_metric_qualified`，因此执行提额仍然 fail-closed——豁免零覆盖指标
  会让"只要收益类分位齐了就算合格"，在没有任何风险指标参与的情况下放开提额，正是这道门禁
  要防的事。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.fund_peer_ranking import build_peer_rank

_DECISION = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)
_AVAILABLE_AT = "2026-08-10T08:00:00+00:00"


def _row(
    code: str,
    *,
    family: str,
    drawdown: float | None = None,
    scale: float | None = None,
) -> dict:
    row = {
        "fund_code": code,
        "fund_name": f"基金{code}",
        "fund_type": "股票型",
        "source_fund_type": "gp",
        "snapshot_available_at": _AVAILABLE_AT,
        "membership_available_at": _AVAILABLE_AT,
        "nav_date": "2026-08-10",
        "fund_manager": f"经理{family}",
        "established_date": "2020-01-01",
        "return_3m_percent": 5.0,
        "return_3m_percent_available_at": _AVAILABLE_AT,
        "return_6m_percent": 8.0,
        "return_6m_percent_available_at": _AVAILABLE_AT,
        "return_1y_percent": 12.0,
        "return_1y_percent_available_at": _AVAILABLE_AT,
    }
    if drawdown is not None:
        row["max_drawdown_1y_percent"] = drawdown
        row["max_drawdown_1y_percent_available_at"] = _AVAILABLE_AT
    if scale is not None:
        row["fund_scale_yi"] = scale
        row["fund_scale_yi_available_at"] = _AVAILABLE_AT
    return row


def _universe(count: int = 30, *, with_drawdown: bool) -> list[dict]:
    return [
        _row(
            f"{600000 + index}",
            family=f"family{index}",
            drawdown=-10.0 - index if with_drawdown else None,
        )
        for index in range(count)
    ]


def _money_row(code: str, *, index: int) -> dict:
    return {
        "fund_code": code,
        "fund_name": f"货币基金{code}",
        "fund_type": "货币型",
        "snapshot_available_at": _AVAILABLE_AT,
        "membership_available_at": _AVAILABLE_AT,
        "nav_date": "2026-08-10",
        "return_1y_percent": 1.8 + index * 0.01,
        "return_1y_percent_available_at": _AVAILABLE_AT,
        "fund_scale_yi": 50.0 + index,
        "fund_scale_yi_available_at": _AVAILABLE_AT,
        "fund_scale_as_of": "2026-06-30",
        "seven_day_annualized_yield_percent": 1.5 + index * 0.01,
        "seven_day_annualized_yield_percent_available_at": _AVAILABLE_AT,
        "income_per_10k_yuan": 0.4 + index * 0.01,
        "income_per_10k_yuan_available_at": _AVAILABLE_AT,
    }


def _metric(rank: dict, field: str) -> dict:
    return (rank.get("metrics") or {}).get(field) or {}


def test_zero_coverage_metric_is_labelled_as_catalogue_gap() -> None:
    """全组一个成员都没有这项 → 是目录缺列，不是目标缺值。"""
    universe = _universe(with_drawdown=False)
    target = universe[0]
    rank = build_peer_rank(target, universe, decision_at=_DECISION)

    drawdown = _metric(rank, "max_drawdown_1y_percent")
    assert drawdown["sample_count"] == 0
    assert drawdown["availability"] == "not_covered_by_peer_catalogue"
    assert "peer_catalogue_metric_not_covered" in drawdown["reasons"]
    # 不得再把结构性缺列说成目标缺值。
    assert not any(str(r).startswith("target_") for r in drawdown["reasons"])


def test_catalogue_gap_is_listed_at_the_top_level() -> None:
    """要能一眼看出 qualified 档为什么永远到不了。"""
    universe = _universe(with_drawdown=False)
    rank = build_peer_rank(universe[0], universe, decision_at=_DECISION)

    assert "max_drawdown_1y_percent" in rank["catalogue_uncovered_metrics"]
    assert "peer_catalogue_missing_required_metrics" in rank["reasons"]


def test_target_specific_gap_is_still_blamed_on_the_target() -> None:
    """全组有覆盖、只有目标缺值 → 这是真正的个体缺口，口径不能被改掉。"""
    universe = _universe(with_drawdown=True)
    # 目标自己没有回撤，同类都有。
    target = _row("600999", family="target-family", drawdown=None)
    rank = build_peer_rank(target, [target, *universe], decision_at=_DECISION)

    drawdown = _metric(rank, "max_drawdown_1y_percent")
    assert drawdown["sample_count"] > 0
    assert drawdown["availability"] == "unavailable"
    assert any(str(r).startswith("target_") for r in drawdown["reasons"])
    assert "peer_catalogue_metric_not_covered" not in drawdown["reasons"]
    # 有覆盖的那一项不得被算进结构性缺列；同一份结果里其它真正零覆盖的项照旧列出。
    assert "max_drawdown_1y_percent" not in rank["catalogue_uncovered_metrics"]
    assert "fund_scale_yi" in rank["catalogue_uncovered_metrics"]


def test_execution_tilt_stays_blocked_despite_the_relabel() -> None:
    """口径改了，门禁不能松：没有风险指标参与时不得放开提额。"""
    universe = _universe(with_drawdown=False)
    rank = build_peer_rank(universe[0], universe, decision_at=_DECISION)

    assert rank["qualified"] is False
    assert rank["execution_tilt_eligible"] is False
    assert (rank["execution_tilt_gate"] or {}).get("eligible") is False
    # 收益类分位仍然算得出来，只是只能作描述。
    assert _metric(rank, "return_3m_percent")["percentile"] is not None
    assert rank["status"] == "descriptive_only"


def test_not_applicable_metrics_are_untouched() -> None:
    """按类型不适用的指标继续走 not_applicable，不能和"目录缺列"混为一谈。"""
    universe = _universe(with_drawdown=False)
    rank = build_peer_rank(universe[0], universe, decision_at=_DECISION)

    for field, item in (rank.get("metrics") or {}).items():
        if item.get("applicable") is True:
            continue
        assert item["availability"] == "not_applicable"
        assert field not in rank["catalogue_uncovered_metrics"]


def test_equity_catalogue_gap_lists_every_uncovered_column() -> None:
    """股票型的缺列不止回撤与规模：正式基准超额/下行捕获/风格漂移也一列都没有。

    这几项和回撤、规模同样来自候选级富化，全集目录里一样是空的。清单必须把它们
    全列出来，否则运维会以为"补上回撤和规模就能到 qualified"。
    """
    universe = _universe(with_drawdown=False)
    rank = build_peer_rank(universe[0], universe, decision_at=_DECISION)

    assert set(rank["catalogue_uncovered_metrics"]) == {
        "benchmark_excess_return_1y_percent",
        "downside_capture_1y_percent",
        "fund_scale_yi",
        "max_drawdown_1y_percent",
        "style_drift_score",
    }
    # 收益三项确实是覆盖住的，所以缺列清单不是"全都缺"的空话。
    for field in ("return_3m_percent", "return_6m_percent", "return_1y_percent"):
        assert _metric(rank, field)["availability"] == "available"


def test_fully_covered_universe_can_reach_qualified() -> None:
    """反证：目录把这一档的列补齐后 qualified 是可达的（说明门禁不是死路）。

    这里用货币型，因为它的 registry 四项全是目录里能直接带的普通数值列，不需要
    逐只冻结的正式基准合同。它证明的是同一套判定逻辑在数据齐全时能走到 qualified，
    股票型到不了纯粹是缺数据源。
    """
    universe = [
        _money_row(f"{500000 + index}", index=index) for index in range(30)
    ]
    rank = build_peer_rank(universe[0], universe, decision_at=_DECISION)

    assert rank["metric_profile"] == "money"
    assert rank["catalogue_uncovered_metrics"] == []
    assert "peer_catalogue_missing_required_metrics" not in rank["reasons"]
    assert rank["qualified"] is True
    assert rank["status"] == "qualified"
    for field in (
        "return_1y_percent",
        "fund_scale_yi",
        "seven_day_annualized_yield_percent",
        "income_per_10k_yuan",
    ):
        item = _metric(rank, field)
        assert item["sample_count"] > 0
        assert item["percentile"] is not None
    # 数据齐了也只放开研究影子重排，执行提额仍要单独的 PIT 统计与经济显著性验证。
    assert rank["research_shadow_rerank_eligible"] is True
    assert rank["execution_tilt_eligible"] is False
