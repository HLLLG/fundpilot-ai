from __future__ import annotations

from app.services.fund_dip_rebound_backtest import (
    build_sector_dip_rebound_hint,
    compute_sector_dip_rebound_stats,
)


def test_sector_rebound_rate_computes_from_kline_series():
    series = [
        {"date": "2026-01-01", "change_percent": -4.0},
        {"date": "2026-01-02", "change_percent": 1.0},
        {"date": "2026-01-03", "change_percent": 1.5},
        {"date": "2026-01-04", "change_percent": 0.5},
    ]
    stats = compute_sector_dip_rebound_stats(
        series,
        dip_threshold_percent=3.0,
        rebound_threshold_percent=2.5,
        forward_days=3,
    )
    assert stats["sample_count"] >= 1
    assert stats["rebound_rate_3d_percent"] == 100.0
    assert stats["note"]


def test_sector_rebound_rate_counts_misses():
    series = [
        {"date": "2026-01-01", "change_percent": -5.0},
        {"date": "2026-01-02", "change_percent": 0.5},
        {"date": "2026-01-03", "change_percent": 0.5},
        {"date": "2026-01-04", "change_percent": 0.5},
        {"date": "2026-01-05", "change_percent": -4.0},
        {"date": "2026-01-06", "change_percent": 0.2},
        {"date": "2026-01-07", "change_percent": 0.2},
        {"date": "2026-01-08", "change_percent": 0.2},
    ]
    stats = compute_sector_dip_rebound_stats(
        series,
        dip_threshold_percent=3.0,
        rebound_threshold_percent=2.5,
        forward_days=3,
    )
    assert stats["sample_count"] == 2
    assert stats["rebound_rate_3d_percent"] == 0.0


def test_build_sector_dip_rebound_hint_uses_injected_series(monkeypatch):
    from app.services.sector_canonical import CanonicalSector

    fake_canon = CanonicalSector(
        label="半导体",
        source_type="concept",
        source_name="半导体",
        eastmoney_secid="90.BK1036",
        source_code="BK1036",
    )

    monkeypatch.setattr(
        "app.services.fund_dip_rebound_backtest.get_canonical_sector",
        lambda _label: fake_canon,
    )
    monkeypatch.setattr(
        "app.services.fund_dip_rebound_backtest.get_trade_date_set",
        lambda: None,
    )

    def _fake_fetch(_canon):
        return [
            {"date": "2026-01-01", "change_percent": -4.0},
            {"date": "2026-01-02", "change_percent": 1.0},
            {"date": "2026-01-03", "change_percent": 1.5},
            {"date": "2026-01-04", "change_percent": 0.5},
            {"date": "2026-01-05", "change_percent": 0.1},
        ]

    hint = build_sector_dip_rebound_hint(
        "半导体",
        fetch_series=_fake_fetch,
        lookback_days=30,
    )
    assert hint is not None
    assert hint["sample_count"] >= 1
    assert hint["rebound_rate_3d_percent"] is not None


def test_compute_sector_dip_rebound_stats_empty_series():
    stats = compute_sector_dip_rebound_stats([], 3.0, 2.5, 3)
    assert stats["sample_count"] == 0
    assert stats["rebound_rate_3d_percent"] is None
