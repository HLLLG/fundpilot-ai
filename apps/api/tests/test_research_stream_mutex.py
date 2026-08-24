from __future__ import annotations

import pytest

from app.services.research_stream_mutex import (
    ANALYZE_BLOCKS_DISCOVERY,
    DISCOVERY_BLOCKS_ANALYZE,
    reset_research_stream_mutex_for_tests,
    try_acquire_research_stream,
)
from tests.conftest import auth_client_for_db


@pytest.fixture(autouse=True)
def _reset_mutex() -> None:
    reset_research_stream_mutex_for_tests()
    yield
    reset_research_stream_mutex_for_tests()


def test_analyze_and_discovery_cannot_overlap() -> None:
    analyze, error = try_acquire_research_stream("analyze")
    assert analyze is not None
    assert error is None

    discovery, conflict = try_acquire_research_stream("discovery")
    assert discovery is None
    assert conflict == ANALYZE_BLOCKS_DISCOVERY

    analyze.release()
    analyze.release()

    discovery, error = try_acquire_research_stream("discovery")
    assert discovery is not None
    assert error is None
    blocked, conflict = try_acquire_research_stream("analyze")
    assert blocked is None
    assert conflict == DISCOVERY_BLOCKS_ANALYZE
    discovery.release()


def _stream_payload() -> dict:
    return {
        "holdings": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "holding_amount": 10000,
            }
        ],
        "profile": {
            "max_drawdown_percent": 15,
            "concentration_limit_percent": 30,
            "expected_investment_amount": 100000,
        },
        "analysis_mode": "deep",
    }


def test_discovery_endpoint_returns_409_while_analyze_holds_the_mutex(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.main.stream_discovery",
        lambda *args, **kwargs: iter(()),
    )
    client = auth_client_for_db(monkeypatch, tmp_path / "mutex.db")
    slot, _ = try_acquire_research_stream("analyze")
    assert slot is not None
    try:
        response = client.post("/api/fund-discovery/stream", json=_stream_payload())
        assert response.status_code == 409
        assert response.json()["detail"] == ANALYZE_BLOCKS_DISCOVERY
    finally:
        slot.release()


def test_analyze_endpoint_returns_409_while_discovery_holds_the_mutex(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.main.stream_analysis",
        lambda *args, **kwargs: iter(()),
    )
    client = auth_client_for_db(monkeypatch, tmp_path / "mutex2.db")
    slot, _ = try_acquire_research_stream("discovery")
    assert slot is not None
    try:
        response = client.post("/api/analyze/stream", json=_stream_payload())
        assert response.status_code == 409
        assert response.json()["detail"] == DISCOVERY_BLOCKS_ANALYZE
    finally:
        slot.release()
