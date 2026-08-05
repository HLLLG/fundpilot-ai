from __future__ import annotations

from types import SimpleNamespace

from app.services import fund_primary_sector_precompute_loop as loop_service


def test_new_profile_queue_wakes_holdings_before_normal_interval() -> None:
    scheduled_at = 6 * 60 * 60.0
    now = 100.0

    assert loop_service._wake_holdings_after_benchmark_queue(
        scheduled_at,
        SimpleNamespace(queued=12),
        now=now,
    ) == now
    assert loop_service._wake_holdings_after_benchmark_queue(
        50.0,
        SimpleNamespace(queued=12),
        now=now,
    ) == 50.0
    assert loop_service._wake_holdings_after_benchmark_queue(
        scheduled_at,
        SimpleNamespace(queued=0),
        now=now,
    ) == scheduled_at
    assert loop_service._wake_holdings_after_benchmark_queue(
        scheduled_at,
        None,
        now=now,
    ) == scheduled_at
