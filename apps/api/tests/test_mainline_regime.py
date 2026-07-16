from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services.discovery_sector_position import summarize_sector_position
from app.services.mainline_regime import (
    align_sector_opportunities_with_mainline_snapshot,
    build_mainline_regime_snapshot,
    mainline_regime_by_label,
)
from app.services.sector_opportunity_scoring import select_sector_opportunities


_DECISION_AT = datetime(2026, 7, 15, 7, 10, tzinfo=timezone.utc)


def _position(
    *,
    return_5d: float,
    return_10d: float,
    return_20d: float,
    return_60d: float,
    relative_10d: float,
    relative_20d: float,
    relative_60d: float,
    distance_high: float = -3.0,
    volume_ratio: float = 1.2,
) -> dict:
    return {
        "available": True,
        "sample_days": 90,
        "data_end_date": "2026-07-15",
        "return_5d_percent": return_5d,
        "return_10d_percent": return_10d,
        "return_20d_percent": return_20d,
        "return_60d_percent": return_60d,
        "relative_return_10d_percent": relative_10d,
        "relative_return_20d_percent": relative_20d,
        "relative_return_60d_percent": relative_60d,
        "distance_from_ma20_percent": 6.0,
        "distance_from_ma60_percent": 5.0,
        "distance_from_20d_high_percent": distance_high,
        "volume_ratio_5d_vs_20d": volume_ratio,
        "max_drawdown_20d_percent": 3.0,
        "annualized_volatility_20d_percent": 25.0,
        "positive_day_ratio_20d_percent": 65.0,
        "benchmark_code": "000300",
        "benchmark_name": "沪深300",
        "benchmark_source": "sina",
        "benchmark_data_end_date": "2026-07-15",
    }


def _flow(flow_5d: float, flow_20d: float, *, pattern: str = "price_flow_aligned_up") -> dict:
    return {
        "available": True,
        "date_aligned": True,
        "today_available": True,
        "five_day_available": True,
        "today_main_force_net_yi": 2.0 if flow_5d >= 0 else -2.0,
        "cumulative_5d_net_yi": flow_5d,
        "cumulative_20d_net_yi": flow_20d,
        "pattern_label": pattern,
        "flow_date": "2026-07-15",
    }


def test_mainline_state_machine_distinguishes_confirmed_crowded_and_fading() -> None:
    heat = [
        {"sector_label": "CPO", "change_1d_percent": 1.5, "change_5d_percent": 5.0, "advancing_ratio_percent": 72.0},
        {"sector_label": "半导体", "change_1d_percent": 5.2, "change_5d_percent": 14.0, "advancing_ratio_percent": 82.0},
        {"sector_label": "医药", "change_1d_percent": -1.0, "change_5d_percent": -3.0, "advancing_ratio_percent": 28.0},
    ]
    positions = {
        "CPO": _position(
            return_5d=5.0,
            return_10d=9.0,
            return_20d=16.0,
            return_60d=24.0,
            relative_10d=14.0,
            relative_20d=20.0,
            relative_60d=24.0,
        ),
        "半导体": _position(
            return_5d=14.0,
            return_10d=18.0,
            return_20d=24.0,
            return_60d=30.0,
            relative_10d=12.0,
            relative_20d=18.0,
            relative_60d=20.0,
            distance_high=-0.3,
            volume_ratio=2.1,
        ),
        "医药": _position(
            return_5d=-3.0,
            return_10d=-5.0,
            return_20d=2.0,
            return_60d=12.0,
            relative_10d=-4.0,
            relative_20d=1.0,
            relative_60d=6.0,
        ),
    }
    flows = {
        "CPO": _flow(10.0, 28.0),
        "半导体": _flow(16.0, 35.0),
        "医药": _flow(-8.0, -20.0, pattern="weak_outflow"),
    }

    snapshot = build_mainline_regime_snapshot(
        heat,
        sector_flow_by_label=flows,
        sector_position_by_label=positions,
        sector_labels=["CPO", "半导体", "医药"],
        decision_at=_DECISION_AT,
        captured_at=_DECISION_AT + timedelta(seconds=2),
    )
    by_label = mainline_regime_by_label(snapshot)

    assert by_label["CPO"]["status"] == "confirmed"
    assert by_label["半导体"]["status"] == "crowded"
    assert by_label["半导体"]["risk_penalty"] >= 15
    assert by_label["医药"]["status"] == "fading"
    assert all(row["execution_eligible"] is False for row in by_label.values())
    assert snapshot["execution_gate_changed"] is False
    assert snapshot["automatic_promotion_allowed"] is False
    assert len(snapshot["snapshot_hash"]) == 64


def test_mainline_fails_closed_when_price_structure_is_missing() -> None:
    snapshot = build_mainline_regime_snapshot(
        [{"sector_label": "CPO", "change_1d_percent": 3.0}],
        sector_flow_by_label={"CPO": _flow(5.0, 8.0)},
        sector_position_by_label={},
        sector_labels=["CPO"],
        decision_at=_DECISION_AT,
        captured_at=_DECISION_AT,
    )

    row = snapshot["sectors"][0]
    assert row["status"] == "insufficient"
    assert row["score"] is not None
    assert row["confidence"] == "低"
    assert "仅保留研究观察" in row["risks"][0]


def test_frozen_mainline_snapshot_overrides_stale_nested_opportunity() -> None:
    snapshot = {
        "sectors": [
            {
                "sector_label": "医药",
                "status": "confirmed",
                "score": 85.1,
                "feature_coverage": 1.0,
                "features": {"relative_return_20d_percent": 32.59},
            }
        ]
    }
    opportunities = [
        {
            "sector_label": "医药",
            "score": 85.1,
            "mainline_regime": {
                "sector_label": "医药",
                "status": "insufficient",
                "features": {"relative_return_20d_percent": None},
            },
        }
    ]

    aligned = align_sector_opportunities_with_mainline_snapshot(opportunities, snapshot)

    assert aligned[0]["mainline_regime"]["status"] == "confirmed"
    assert aligned[0]["mainline_regime"]["features"]["relative_return_20d_percent"] == 32.59
    assert opportunities[0]["mainline_regime"]["status"] == "insufficient"


def test_mainline_changes_research_order_without_changing_opportunity_score() -> None:
    heat = [
        {"sector_label": "CPO", "change_1d_percent": 1.0, "change_5d_percent": 2.0, "heat_score": 1.4},
        {"sector_label": "半导体", "change_1d_percent": 1.0, "change_5d_percent": 2.0, "heat_score": 1.4},
    ]
    flows = {"CPO": _flow(2.0, 4.0), "半导体": _flow(2.0, 4.0)}
    baseline = select_sector_opportunities(heat, sector_flow_by_label=flows, max_total=2)
    mainline = {
        "CPO": {"status": "confirmed", "score": 90.0},
        "半导体": {"status": "neutral", "score": 30.0},
    }
    ranked = select_sector_opportunities(
        heat,
        sector_flow_by_label=flows,
        mainline_by_label=mainline,
        max_total=2,
    )

    baseline_scores = {row["sector_label"]: row["score"] for row in baseline}
    ranked_scores = {row["sector_label"]: row["score"] for row in ranked}
    assert ranked[0]["sector_label"] == "CPO"
    assert ranked_scores == baseline_scores
    assert ranked[0]["research_score"] > ranked[1]["research_score"]


def test_sector_position_excludes_future_rows_and_aligns_benchmark() -> None:
    start = date(2026, 3, 1)
    sector_rows = []
    benchmark_rows = []
    for index in range(90):
        day = (start + timedelta(days=index)).isoformat()
        benchmark_close = 100.0 + index
        sector_rows.append({"date": day, "close": benchmark_close * (1.0 + index / 1000.0)})
        benchmark_rows.append({"date": day, "close": benchmark_close})
    cutoff = sector_rows[-2]["date"]
    sector_rows[-1]["close"] = 10_000.0

    result = summarize_sector_position(
        "CPO",
        sector_rows,
        benchmark_rows=benchmark_rows,
        as_of_trade_date=cutoff,
    )

    assert result["available"] is True
    assert result["data_end_date"] == cutoff
    assert result["return_20d_percent"] < 100
    assert result["relative_return_20d_percent"] is not None
    assert result["relative_return_20d_percent"] > 0
