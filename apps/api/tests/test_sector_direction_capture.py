"""每日方向状态捕获的口径回归。

背景：`sector_direction_states` 原来只在用户手动跑发现基金时才写，于是退出侧的「连续
跌破 N 个交易日才升级」攒不出数据（本地实测整张表只有一天）。这里锁住捕获脚本的三条
关键口径：全白名单（不是发现基金那约 24 个预筛板块）、默认跳过量价背离、以及「落库了
但趋势证据为 0」必须被如实报成不可用而不是成功。
"""

from __future__ import annotations

import pytest

from app.services import sector_direction_capture as capture_module
from app.services.sector_direction_capture import capture_sector_direction_states

_HEAT = [
    {"sector_label": "煤炭", "heat_score": 90.0},
    {"sector_label": "黄金", "heat_score": 80.0},
    {"sector_label": "国防军工", "heat_score": 70.0},
]


@pytest.fixture
def stub_pipeline(monkeypatch):
    """把整条取数链路替换成可观测的假实现，全程离线。"""
    calls: dict[str, object] = {"divergence_called": False}

    from app.services import (
        discovery_pipeline,
        discovery_sector_heat,
        discovery_sector_position,
        mainline_regime,
        sector_opportunity_scoring,
    )

    monkeypatch.setattr(
        discovery_sector_heat,
        "build_sector_heat_ranking",
        lambda **_kwargs: list(_HEAT),
    )

    def _flow(sector_heat, sector_labels, **_kwargs):  # noqa: ANN001
        calls["flow_labels"] = list(sector_labels)
        return {label: {"available": True, "date_aligned": True} for label in sector_labels}

    monkeypatch.setattr(
        sector_opportunity_scoring,
        "build_sector_flow_map_for_opportunities",
        _flow,
    )

    def _divergence(sector_labels, **_kwargs):  # noqa: ANN001
        calls["divergence_called"] = True
        calls["divergence_labels"] = list(sector_labels)
        return {}

    monkeypatch.setattr(
        sector_opportunity_scoring,
        "build_sector_divergence_map_for_opportunities",
        _divergence,
    )

    def _position(sector_labels, **_kwargs):  # noqa: ANN001
        calls["position_labels"] = list(sector_labels)
        return {label: {"distance_from_ma20_percent": 1.0} for label in sector_labels}

    monkeypatch.setattr(
        discovery_sector_position,
        "build_sector_position_map_for_opportunities",
        _position,
    )
    monkeypatch.setattr(
        discovery_sector_position,
        "build_sector_percentile_universe_positions",
        lambda *_args, **_kwargs: {},
    )

    def _snapshot(sector_heat, **kwargs):  # noqa: ANN001
        calls["mainline_labels"] = list(kwargs.get("sector_labels") or [])
        return {"sectors": []}

    monkeypatch.setattr(mainline_regime, "build_mainline_regime_snapshot", _snapshot)
    monkeypatch.setattr(
        mainline_regime,
        "mainline_regime_by_label",
        lambda _snapshot: {label: {"status": "confirmed"} for label in [row["sector_label"] for row in _HEAT]},
    )

    def _score(sector_heat, **kwargs):  # noqa: ANN001
        calls["score_kwargs"] = dict(kwargs)
        return [{"sector_label": row["sector_label"]} for row in sector_heat]

    monkeypatch.setattr(
        discovery_pipeline,
        "_score_select_and_persist_directions",
        _score,
    )
    return calls


def _stub_persisted(monkeypatch, *, persisted: int, with_evidence: int) -> None:
    monkeypatch.setattr(
        capture_module,
        "_persisted_stats",
        lambda _trade_date: {
            "persisted": persisted,
            "with_trend_evidence": with_evidence,
            "degraded": max(0, persisted - with_evidence),
        },
    )


def test_captures_the_full_whitelist_not_a_prescreened_subset(
    stub_pipeline, monkeypatch
) -> None:
    """前台集合必须是全白名单。

    发现基金只对约 24 个预筛板块取联网证据，不在其中的板块会拿到 v3 的证据不足兜底
    （趋势分 ≤45，必然低于退出线 52）。这张表没有 userId、一次捕获服务所有用户，所以
    任何用户的任何持仓方向都必须落在真实证据上。
    """
    _stub_persisted(monkeypatch, persisted=3, with_evidence=3)
    summary = capture_sector_direction_states(trade_date="2026-08-10")

    expected = ["煤炭", "黄金", "国防军工"]
    assert stub_pipeline["flow_labels"] == expected
    assert stub_pipeline["position_labels"] == expected
    assert stub_pipeline["mainline_labels"] == expected
    assert summary["universe_size"] == 3
    assert summary["ok"] is True


def test_skips_divergence_by_default(stub_pipeline, monkeypatch) -> None:
    """量价背离只影响 confidence，不落库也不参与 entry_state，默认不跑。

    实测它在全白名单上跑满 90s 预算仍整段超时，占了总耗时 103.5s 里的 90s，而落库结果
    逐项相同。
    """
    _stub_persisted(monkeypatch, persisted=3, with_evidence=3)
    capture_sector_direction_states(trade_date="2026-08-10")

    assert stub_pipeline["divergence_called"] is False
    assert stub_pipeline["score_kwargs"]["sector_divergence_by_label"] == {}
    # 但它仍然可以显式打开
    capture_sector_direction_states(trade_date="2026-08-10", include_divergence=True)
    assert stub_pipeline["divergence_called"] is True


def test_capture_does_not_pin_any_user_focus_sectors(stub_pipeline, monkeypatch) -> None:
    """捕获不服务单个用户，不能给谁的关注方向加排序分。"""
    _stub_persisted(monkeypatch, persisted=3, with_evidence=3)
    capture_sector_direction_states(trade_date="2026-08-10")
    assert stub_pipeline["score_kwargs"]["focus_sectors"] == []
    assert stub_pipeline["score_kwargs"]["effective_trade_date"] == "2026-08-10"


def test_reports_degraded_rows_so_a_useless_capture_is_visible(
    stub_pipeline, monkeypatch
) -> None:
    """落库行数不能单独当成功判据：证据不足时行数照样是满的。"""
    _stub_persisted(monkeypatch, persisted=78, with_evidence=0)
    summary = capture_sector_direction_states(trade_date="2026-08-10")

    assert summary["persisted"] == 78
    assert summary["with_trend_evidence"] == 0
    assert summary["degraded"] == 78


def test_missing_sector_heat_fails_instead_of_writing_nothing_quietly(
    monkeypatch,
) -> None:
    from app.services import discovery_sector_heat

    monkeypatch.setattr(
        discovery_sector_heat, "build_sector_heat_ranking", lambda **_kwargs: []
    )
    summary = capture_sector_direction_states(trade_date="2026-08-10")
    assert summary["ok"] is False
    assert summary["reason"] == "sector_heat_unavailable"


def test_capture_shares_the_single_direction_state_writer() -> None:
    """打分/滞回/落库必须复用发现基金那一份。

    另写一份会让同一个板块同一天出现两个 `trend_strength_score`，而退出侧要把「今天实算
    的分」和「历史落库的分」放在一条序列上比连续天数——两套口径会让这个计数失去意义。
    """
    import inspect

    source = inspect.getsource(capture_sector_direction_states)
    assert (
        "from app.services.discovery_pipeline import "
        "_score_select_and_persist_directions" in source
    )


# ---------------------------------------------------------------------------
# 回填：趋势轴可重算，但滞回三列不可信 —— 用 source 列把两个读取侧分开
# ---------------------------------------------------------------------------


def _insert_state(
    *,
    trade_date: str,
    sector_label: str,
    trend: float | None,
    coverage: float | None,
    source: str | None,
    entry_state: str = "ready_to_start",
    consecutive: int = 5,
) -> None:
    from app.database import _connect

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO sector_direction_states (
                trade_date, sector_label, schema_version, policy_version,
                entry_state, raw_entry_state, qualifies_for_ready,
                consecutive_qualifying_days, trend_strength_score,
                participation_score, position_risk_score, direction_score,
                recorded_at, trend_evidence_coverage, source
            ) VALUES (?, ?, 'v1', 'sector_entry_maturity.2026-08.v3', ?, ?, 1, ?,
                      ?, 50.0, 50.0, 50.0, '2026-08-10T00:00:00Z', ?, ?)
            """,
            (
                trade_date,
                sector_label,
                entry_state,
                entry_state,
                consecutive,
                trend,
                coverage,
                source,
            ),
        )
        connection.commit()


def test_backfilled_rows_are_hidden_from_discovery_hysteresis() -> None:
    """回填行的 entry_state 由中性填充的 participation 派生，不能进滞回链条。"""
    from app.services.sector_direction_state import load_previous_direction_states

    _insert_state(
        trade_date="2026-08-06",
        sector_label="煤炭",
        trend=70.0,
        coverage=1.0,
        source="backfilled",
    )
    assert load_previous_direction_states("2026-08-06") is None

    _insert_state(
        trade_date="2026-08-06",
        sector_label="黄金",
        trend=88.0,
        coverage=1.0,
        source="captured",
    )
    previous = load_previous_direction_states("2026-08-06")
    assert previous is not None
    assert set(previous) == {"黄金"}, "回填行不得出现在滞回输入里"


def test_backfilled_rows_are_usable_for_the_exit_trend_history() -> None:
    """趋势轴是日线纯函数、历史可如实重算，退出侧应当收下。"""
    from app.services.sector_direction_exit import load_direction_trend_history

    _insert_state(
        trade_date="2026-08-06",
        sector_label="半导体材料",
        trend=32.5,
        coverage=1.0,
        source="backfilled",
    )
    history = load_direction_trend_history(
        ["半导体材料"], before_trade_date="2026-08-10"
    )
    assert history == {"半导体材料": [("2026-08-06", 32.5)]}


def test_backfill_replaces_rows_without_trend_evidence(monkeypatch) -> None:
    """覆盖度为 NULL 的存量行必须允许被替换，否则它会挡住更早的回填。

    实测过这个坑：先回填了 5 天 390 行，但 08-07 那批迁移前写入的行（覆盖度 NULL）像
    路障一样把历史序列整段截断，退出侧仍然读成空。
    """
    from app.services import sector_direction_capture

    _insert_state(
        trade_date="2026-08-07",
        sector_label="国防军工",
        trend=45.0,  # 证据不足时的兜底占位值
        coverage=None,  # 迁移前写入，无覆盖度
        source="captured",
    )
    assert sector_direction_capture._labels_with_trend_evidence("2026-08-07") == set()

    written, with_evidence = sector_direction_capture._record_backfilled_trend_rows(
        [
            {
                "sector_label": "国防军工",
                "score_policy_version": "sector_entry_maturity.2026-08.v3",
                "entry_state": "forming",
                "trend_strength_score": 36.8,
                "participation_score": 50.0,
                "position_risk_score": 50.0,
                "direction_score": 44.0,
                "component_coverage": {"trend": 1.0},
            }
        ],
        trade_date="2026-08-07",
    )
    assert (written, with_evidence) == (1, 1)

    from app.database import _connect

    with _connect() as connection:
        row = connection.execute(
            "SELECT trend_strength_score, trend_evidence_coverage, source, "
            "consecutive_qualifying_days, qualifies_for_ready "
            "FROM sector_direction_states "
            "WHERE trade_date = '2026-08-07' AND sector_label = '国防军工'"
        ).fetchone()
    assert row["trend_strength_score"] == pytest.approx(36.8)
    assert row["trend_evidence_coverage"] == pytest.approx(1.0)
    assert row["source"] == "backfilled"
    # 滞回三列显式置零，不假装重算得出来
    assert row["consecutive_qualifying_days"] == 0
    assert row["qualifies_for_ready"] == 0


def test_backfill_never_touches_rows_that_already_have_evidence() -> None:
    """真实捕获且有证据的行是更好的数据，不许被重算值盖掉。"""
    from app.services import sector_direction_capture

    _insert_state(
        trade_date="2026-08-06",
        sector_label="煤炭",
        trend=82.1,
        coverage=1.0,
        source="captured",
    )
    assert sector_direction_capture._labels_with_trend_evidence("2026-08-06") == {"煤炭"}


def test_backfill_reports_no_trade_dates_instead_of_pretending_success() -> None:
    from app.services.sector_direction_capture import backfill_sector_direction_trend

    summary = backfill_sector_direction_trend(trade_dates=[])
    assert summary["ok"] is False
    assert summary["reason"] == "no_trade_dates"
