"""方向状态账本健康度披露回归。

背景：退出侧的「连续跌破天数」完全依赖每交易日 19:10 的定时捕获写
`sector_direction_states`。捕获断更**不会报错**，只是天数停在 1、−50% 那一档安静地
不可达——"连续天数是下界"此前只是一句没人能核实的免责声明。这里锁住：健康度随每份
退出判定一起披露，断更时 `stale=True` 且给出可核对的两个日期。

判定基准是**上一交易日**：日报白天生成时当天的捕获还没跑，账本覆盖到上一交易日就是
健康的。回填行（`source='backfilled'`）是补数手段，不代表捕获链路活着，不计入健康度。
"""

from __future__ import annotations

from app.models import Holding
from app.services import report_sector_opportunity as sector_ctx
from app.services import sector_direction_exit as exit_mod
from app.services.sector_direction_exit import load_direction_ledger_health


def _insert_state(
    *,
    trade_date: str,
    sector_label: str = "煤炭",
    source: str | None = "captured",
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
            ) VALUES (?, ?, 'v1', 'sector_entry_maturity.2026-08.v3',
                      'ready_to_start', 'ready_to_start', 1, 3,
                      70.0, 50.0, 50.0, 60.0, '2026-06-09T00:00:00Z', 1.0, ?)
            """,
            (trade_date, sector_label, source),
        )
        connection.commit()


# --------------------------------------------------------------------------
# load_direction_ledger_health
# --------------------------------------------------------------------------


def test_empty_ledger_is_stale_with_a_note() -> None:
    health = load_direction_ledger_health("2026-06-10")

    assert health["last_captured_trade_date"] is None
    assert health["stale"] is True
    assert "尚无捕获记录" in str(health["note"])


def test_ledger_covering_the_previous_trade_date_is_fresh() -> None:
    # as_of 2026-06-10 的上一交易日是 2026-06-09（conftest 的 stub 日历）。
    _insert_state(trade_date="2026-06-09")

    health = load_direction_ledger_health("2026-06-10")

    assert health["last_captured_trade_date"] == "2026-06-09"
    assert health["expected_trade_date"] == "2026-06-09"
    assert health["stale"] is False
    assert health["note"] is None


def test_same_day_capture_also_counts_as_fresh() -> None:
    """19:10 之后生成日报时账本可能已有当天的行，不得因此误判断更。"""
    _insert_state(trade_date="2026-06-10")

    health = load_direction_ledger_health("2026-06-10")

    assert health["stale"] is False


def test_ledger_behind_the_previous_trade_date_is_stale() -> None:
    _insert_state(trade_date="2026-06-05")

    health = load_direction_ledger_health("2026-06-10")

    assert health["last_captured_trade_date"] == "2026-06-05"
    assert health["stale"] is True
    note = str(health["note"])
    assert "2026-06-05" in note and "2026-06-09" in note
    assert "下界" in note


def test_backfilled_rows_do_not_count_as_capture() -> None:
    """回填行填的是历史趋势轴，不能证明捕获链路当天活着。"""
    _insert_state(trade_date="2026-06-05", source="captured")
    _insert_state(trade_date="2026-06-09", source="backfilled")

    health = load_direction_ledger_health("2026-06-10")

    assert health["last_captured_trade_date"] == "2026-06-05"
    assert health["stale"] is True


def test_legacy_null_source_rows_count_as_capture() -> None:
    """存量行 source 为 NULL：与 `load_previous_direction_states` 的兼容语义一致。"""
    _insert_state(trade_date="2026-06-09", source=None)

    health = load_direction_ledger_health("2026-06-10")

    assert health["last_captured_trade_date"] == "2026-06-09"
    assert health["stale"] is False


# --------------------------------------------------------------------------
# 挂载：每份退出判定都带同一份健康度
# --------------------------------------------------------------------------


def test_attach_direction_exit_carries_ledger_health(monkeypatch) -> None:
    monkeypatch.setattr(exit_mod, "load_direction_trend_history", lambda *_a, **_k: {})
    monkeypatch.setattr(
        exit_mod,
        "load_direction_entry_contracts",
        lambda _codes: {
            "015788": {
                "sector_label": "煤炭",
                "entry_date": "2026-06-04",
                "entry_state": "confirmed_entry",
                "entry_trend": 72.0,
                "entry_participation": 40.0,
                "entry_position_risk": 50.0,
                "entry_tranche_scale": 0.6,
                "thesis_event_id": "discovery:test:0:015788",
            }
        },
    )
    stub_health = {
        "last_captured_trade_date": "2026-06-05",
        "expected_trade_date": "2026-06-09",
        "stale": True,
        "note": "方向状态账本最后捕获日为 2026-06-05（应覆盖到 2026-06-09），连续跌破天数为下界、可能低估",
    }
    monkeypatch.setattr(
        exit_mod, "load_direction_ledger_health", lambda _as_of: dict(stub_health)
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
        holdings=[
            Holding(
                fund_code="015788",
                fund_name="测试基金",
                sector_name="煤炭",
                holding_amount=10_000.0,
                holding_profit=0.0,
            )
        ],
        trade_date="2026-06-10",
    )

    assert held["煤炭"]["direction_exit"]["ledger_health"] == stub_health
    assert by_code["015788"]["ledger_health"] == stub_health
