from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services import fund_benchmark_sector as service


@pytest.fixture(autouse=True)
def _clear_metadata() -> None:
    with service._BENCHMARK_FETCH_METADATA_LOCK:
        service._BENCHMARK_FETCH_METADATA.clear()
    yield
    with service._BENCHMARK_FETCH_METADATA_LOCK:
        service._BENCHMARK_FETCH_METADATA.clear()


def test_benchmark_metadata_is_lru_bounded(monkeypatch) -> None:
    monkeypatch.setattr(service, "_BENCHMARK_FETCH_METADATA_MAX_ENTRIES", 2)
    service._remember_benchmark_fetch_metadata(
        "000001",
        "benchmark-a",
        kind="performance_benchmark",
        source_kind="live",
    )
    service._remember_benchmark_fetch_metadata(
        "000002",
        "benchmark-b",
        kind="performance_benchmark",
        source_kind="live",
    )
    assert service.get_fund_benchmark_fetch_metadata("000001", "benchmark-a")[
        "benchmark_text_source_kind"
    ] == "live"
    service._remember_benchmark_fetch_metadata(
        "000003",
        "benchmark-c",
        kind="tracking_target",
        source_kind="live",
    )

    assert list(service._BENCHMARK_FETCH_METADATA) == [
        ("000001", "benchmark-a"),
        ("000003", "benchmark-c"),
    ]


def test_concurrent_benchmark_metadata_writes_remain_bounded(monkeypatch) -> None:
    monkeypatch.setattr(service, "_BENCHMARK_FETCH_METADATA_MAX_ENTRIES", 16)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda index: service._remember_benchmark_fetch_metadata(
                    f"{index:06d}",
                    f"benchmark-{index}",
                    kind="performance_benchmark",
                    source_kind="live",
                ),
                range(128),
            )
        )

    assert len(service._BENCHMARK_FETCH_METADATA) == 16
