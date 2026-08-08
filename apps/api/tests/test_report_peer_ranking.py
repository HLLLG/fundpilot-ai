"""日报持仓的同类分位。

两条纪律是这个文件的重点：

1. **它只能是描述性证据。** `execution_tilt_eligible` 恒为 False，因此不得进入任何
   确定性结论——不改仓位比例、不拦动作。移植荐基能力时最容易犯的错就是把分位当成
   可执行信号（与 factor 侧 `descriptive_applicable` vs `execution_qualified` 同一条线）。
2. **算不出来 ≠ 同类里差。** 目录缓存缺席、该基金不在目录、同类组欠定义（`fund_peer_ranking`
   对 mixed / bond / QDII / FOF / 被动指数在分类字段不足时按设计 fail closed）三种情形
   必须各自留下原因，否则模型会把缺席读成利空。
"""

from datetime import datetime, timezone

import pytest

from app.models import Holding
from app.services.discovery_candidate_llm import _compact_peer_research
from app.services.fund_peer_ranking import (
    compact_peer_research_for_llm,
    peer_catalogue_bucket,
)
from app.services.report_peer_ranking import (
    attach_holding_peer_research,
    resolve_holding_peer_research,
)

DECISION_AT = datetime(2026, 8, 7, 6, 30, tzinfo=timezone.utc)


# 目录行必须带 point-in-time 凭证，这不是测试细节而是 `build_peer_rank` 的硬要求：
# `_membership_instant` 缺 `membership_available_at` 就把整行从同类全集里剔除，
# 于是 `independent_peer_family_count` 归零、一切指标 `insufficient`。生产链路由
# `fund_discovery_data_cache._universe_rows_with_snapshot_contract` 统一盖上这批时点戳，
# fixture 按同一形状构造，否则测出来的是"没有时点证据"而不是同类分位本身。
_SNAPSHOT_AVAILABLE_AT = "2026-08-07T02:00:00+00:00"
_PIT_STAMPED_METRICS = (
    "return_3m_percent",
    "return_6m_percent",
    "return_1y_percent",
    "max_drawdown_1y_percent",
    "fund_scale_yi",
)


def _catalogue_row(index: int, *, code: str | None = None, fund_type: str = "gp") -> dict:
    row = {
        "fund_code": code or f"{600000 + index:06d}",
        "fund_name": f"测试股票基金{index}",
        "fund_type": fund_type,
        "established_date": "2018-01-15",
        "nav_date": "2026-08-07",
        "latest_nav": 1.5 + index * 0.01,
        "return_3m_percent": 4.0 + index * 0.3,
        "return_6m_percent": 9.0 + index * 0.5,
        "return_1y_percent": 18.0 + index * 0.7,
        "max_drawdown_1y_percent": -12.0 - index * 0.2,
        "fund_scale_yi": 20.0 + index,
        "membership_available_at": _SNAPSHOT_AVAILABLE_AT,
        "snapshot_available_at": _SNAPSHOT_AVAILABLE_AT,
        "source": "fund_universe_snapshot",
    }
    for field in _PIT_STAMPED_METRICS:
        row[f"{field}_available_at"] = _SNAPSHOT_AVAILABLE_AT
        row[f"{field}_source"] = "fund_universe_snapshot"
    return row


def _catalogue(count: int = 40) -> list[dict]:
    return [_catalogue_row(i) for i in range(count)]


def _holding(code: str) -> Holding:
    return Holding(
        fund_code=code,
        fund_name="测试股票基金0",
        sector_name="半导体",
        holding_amount=10_000.0,
    )


# --------------------------------------------------------------------------- #
# 三种 fail closed 必须可区分
# --------------------------------------------------------------------------- #


def test_missing_catalogue_cache_marks_every_holding_unavailable() -> None:
    result = resolve_holding_peer_research(
        [_holding("600000"), _holding("600001")],
        decision_at=DECISION_AT,
        fetch_universe=lambda: [],
    )

    assert set(result) == {"600000", "600001"}
    for row in result.values():
        assert row["available"] is False
        assert row["reason"] == "catalogue_cache_unavailable"
        assert row["execution_tilt_eligible"] is False
        assert row["descriptive_only"] is True


def test_fund_absent_from_catalogue_is_reported_separately() -> None:
    result = resolve_holding_peer_research(
        [_holding("999999")],
        decision_at=DECISION_AT,
        fetch_universe=_catalogue,
    )

    assert result["999999"]["available"] is False
    assert result["999999"]["reason"] == "fund_not_in_catalogue"


def test_universe_fetch_error_does_not_raise() -> None:
    def explode():
        raise RuntimeError("cache down")

    result = resolve_holding_peer_research(
        [_holding("600000")],
        decision_at=DECISION_AT,
        fetch_universe=explode,
    )

    assert result["600000"]["reason"] == "catalogue_cache_unavailable"


def test_underspecified_group_stays_descriptive_and_keeps_its_reason() -> None:
    """混合型缺风险暴露分类：分位仍可给出，但 `qualified` 不成立、原因必须留着。

    `build_peer_rank` 的契约是"组太小或欠定义时 percentile 可以存在，`qualified` 与
    `execution_tilt_eligible` 保持 false"。所以这里不该断言"不可用"，而该断言
    "拿到的是描述性分位 + 欠定义原因"——否则会把一个刻意保留的中间态测成失败态。
    """
    catalogue = [_catalogue_row(i, fund_type="hh") for i in range(40)]
    assert peer_catalogue_bucket(catalogue[0]) == "mixed"

    result = resolve_holding_peer_research(
        [_holding("600000")],
        decision_at=DECISION_AT,
        fetch_universe=lambda: catalogue,
    )
    row = result["600000"]

    assert row["status"] == "descriptive_only"
    assert row["reason"] == "mixed_risk_exposure_unavailable"
    assert row["execution_tilt_eligible"] is False
    assert row["descriptive_only"] is True


def test_rows_without_point_in_time_stamps_are_insufficient() -> None:
    """没有时点凭证时同类全集归零——这是"缺证据"，不是"同类里差"。

    这条同时是 `_universe_rows_with_snapshot_contract` 的契约保护：日报走的
    `fetch_discovery_fund_universe_cache_only()` 必须经过它盖时点戳，绕过去
    （例如有人改成直读缓存 payload 的 rows）就会静默退化成这里的结果。
    """
    catalogue = []
    for index in range(40):
        row = _catalogue_row(index)
        row.pop("membership_available_at")
        row.pop("snapshot_available_at")
        for field in _PIT_STAMPED_METRICS:
            row.pop(f"{field}_available_at", None)
        catalogue.append(row)

    result = resolve_holding_peer_research(
        [_holding("600000")],
        decision_at=DECISION_AT,
        fetch_universe=lambda: catalogue,
    )
    row = result["600000"]

    assert row["available"] is False
    assert row["status"] == "insufficient"
    assert row["independent_peer_family_count"] == 0


# --------------------------------------------------------------------------- #
# 真的算出分位
# --------------------------------------------------------------------------- #


def test_well_populated_group_produces_descriptive_percentiles() -> None:
    result = resolve_holding_peer_research(
        [_holding("600000")],
        decision_at=DECISION_AT,
        fetch_universe=_catalogue,
    )
    row = result["600000"]

    assert row["status"] in {"qualified", "descriptive_only"}
    assert row["available"] is True
    assert row["descriptive_only"] is True
    # 无论数据多好，执行语义恒为 false。
    assert row["execution_tilt_eligible"] is False
    assert row["independent_peer_family_count"] >= 20
    percentiles = [
        metric.get("percentile")
        for metric in (row.get("metrics") or {}).values()
        if isinstance(metric, dict)
    ]
    assert any(value is not None for value in percentiles)


def test_worst_and_best_member_get_opposite_percentiles() -> None:
    """分位方向要正确：收益最高的那只不该拿到低分位。"""
    catalogue = _catalogue(40)
    best = catalogue[-1]["fund_code"]
    worst = catalogue[0]["fund_code"]

    result = resolve_holding_peer_research(
        [_holding(best), _holding(worst)],
        decision_at=DECISION_AT,
        fetch_universe=lambda: catalogue,
    )

    def perf(code: str) -> float | None:
        metric = (result[code].get("metrics") or {}).get("return_3m_percent")
        return (metric or {}).get("percentile")

    best_percentile = perf(best)
    worst_percentile = perf(worst)
    assert best_percentile is not None and worst_percentile is not None
    assert best_percentile > worst_percentile


def test_duplicate_holding_codes_are_resolved_once() -> None:
    result = resolve_holding_peer_research(
        [_holding("600000"), _holding("600000")],
        decision_at=DECISION_AT,
        fetch_universe=_catalogue,
    )

    assert list(result) == ["600000"]


def test_no_holdings_returns_empty_without_touching_the_catalogue() -> None:
    def explode():
        raise AssertionError("must not read the catalogue for zero holdings")

    assert resolve_holding_peer_research([], fetch_universe=explode) == {}


# --------------------------------------------------------------------------- #
# 挂载与共享投影
# --------------------------------------------------------------------------- #


def test_attach_writes_peer_research_per_row_and_fills_gaps() -> None:
    rows = attach_holding_peer_research(
        [{"fund_code": "600000"}, {"fund_code": "999999"}, None],
        {"600000": {"available": True, "status": "descriptive_only"}},
    )

    assert len(rows) == 2
    assert rows[0]["peer_research"]["available"] is True
    # 没算到的持仓也必须带上显式不可用，而不是缺键。
    assert rows[1]["peer_research"]["available"] is False
    assert rows[1]["peer_research"]["execution_tilt_eligible"] is False


def test_discovery_projection_delegates_to_the_shared_one() -> None:
    """两条链路必须共用同一份投影，否则「同类分位」在两个界面上不可比。"""
    item = {
        "peer_rank": {
            "schema_version": "peer_rank.v2",
            "status": "descriptive_only",
            "execution_tilt_eligible": False,
            "metric_profile": "equity_active",
            "universe": {"independent_peer_family_count": 31},
            "metrics": {
                "return_3m_percent": {
                    "applicable": True,
                    "available": True,
                    "percentile": 62.5,
                    "label": "近3月收益",
                    "nested": {"dropped": True},
                },
                "capacity": {
                    "applicable": False,
                    "available": False,
                    "reason": "metric_not_applicable_to_equity_active",
                },
            },
        },
        "peer_group": {"group_key": "equity_active:cn", "group_label": "主动股票"},
    }

    assert _compact_peer_research(item) == compact_peer_research_for_llm(item)
    projected = compact_peer_research_for_llm(item)
    # 嵌套容器被丢掉，标量保留。
    assert "nested" not in projected["metrics"]["return_3m_percent"]
    assert projected["metrics"]["return_3m_percent"]["percentile"] == 62.5
    # 不适用维度必须显式留下，否则"本就不适用"会被读成"算出来是空"。
    assert projected["not_applicable_metrics"]["capacity"]["applicable"] is False


@pytest.mark.parametrize(
    ("fund_type", "expected"),
    [
        ("zs", "equity_index"),
        ("gp", "equity_active"),
        ("hh", "mixed"),
        ("zq", "bond"),
        ("货币型", "money"),
        ("", "unknown"),
    ],
)
def test_catalogue_bucket_is_shared_vocabulary(fund_type: str, expected: str) -> None:
    """日报与荐基必须用同一套分桶，否则同一只基金会落进不同同类组。"""
    from app.services.discovery_candidate_pool import _peer_catalogue_bucket

    row = {"fund_code": "600000", "fund_name": "X", "fund_type": fund_type}
    assert peer_catalogue_bucket(row) == expected
    assert _peer_catalogue_bucket(row) == expected
