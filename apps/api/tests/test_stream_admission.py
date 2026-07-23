from __future__ import annotations

from app.services.stream_admission import (
    active_stream_count,
    try_acquire_stream_slot,
)


def test_stream_admission_is_bounded_and_release_is_idempotent() -> None:
    before = active_stream_count()
    first = try_acquire_stream_slot(before + 1)
    assert first is not None
    assert active_stream_count() == before + 1
    assert try_acquire_stream_slot(before + 1) is None

    first.release()
    first.release()
    assert active_stream_count() == before


def test_zero_limit_disables_stream_admission_gate() -> None:
    before = active_stream_count()
    slot = try_acquire_stream_slot(0)
    assert slot is not None
    assert active_stream_count() == before
    slot.release()
