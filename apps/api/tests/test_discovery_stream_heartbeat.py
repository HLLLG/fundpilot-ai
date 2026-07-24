from __future__ import annotations

import time
from types import SimpleNamespace

from app.services import discovery_streaming


def test_whole_discovery_pipeline_emits_heartbeat_during_unwrapped_work(
    monkeypatch,
) -> None:
    def slow_pipeline(
        _request,
        *,
        user_id: int,
        started_at: float,
        stop_event,
    ):
        assert user_id == 7
        assert started_at > 0
        assert not stop_event.is_set()
        yield {
            "type": "stage",
            "stage": "sector_heat",
            "label": "计算板块热度…",
        }
        time.sleep(0.06)
        yield {"type": "done", "report_id": "r1", "report": {"id": "r1"}}

    monkeypatch.setattr(discovery_streaming, "_stream_discovery", slow_pipeline)
    monkeypatch.setattr(discovery_streaming, "PIPELINE_HEARTBEAT_SECONDS", 0.01)

    events = list(
        discovery_streaming.stream_discovery(SimpleNamespace(), user_id=7)
    )

    heat_events = [
        event
        for event in events
        if event.get("type") == "stage" and event.get("stage") == "sector_heat"
    ]
    assert len(heat_events) >= 2
    assert all(event["label"] == "计算板块热度…" for event in heat_events)
    assert events[-1]["type"] == "done"
