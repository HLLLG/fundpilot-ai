from __future__ import annotations

import logging
import threading
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


def test_discovery_done_event_is_a_summary_not_the_full_body() -> None:
    from app.models import FundDiscoveryReport
    from app.services.discovery_streaming import _done

    report = FundDiscoveryReport(
        id="r-compact",
        title="压缩完成事件",
        summary="摘要",
        candidate_pool=[{"fund_code": "000001"}],
        discovery_facts={"pipeline": {"provider": "deepseek"}},
        decision_events=[{"id": "evt-1"}],
        recommendations=[],
    )
    event = _done(report)
    assert event["type"] == "done"
    assert event["report_id"] == "r-compact"
    assert event["report"]["title"] == "压缩完成事件"
    assert "candidate_pool" not in event["report"]
    assert "discovery_facts" not in event["report"]
    assert "decision_events" not in event["report"]
    assert "recommendations" not in event["report"]


def test_discovery_stream_logs_cancellation_with_last_stage(
    monkeypatch,
    caplog,
) -> None:
    def waiting_pipeline(
        _request,
        *,
        user_id: int,
        started_at: float,
        stop_event,
    ):
        assert user_id == 7
        assert started_at > 0
        yield {
            "type": "stage",
            "stage": "generating",
            "label": "AI 分析中…",
        }
        while not stop_event.wait(0.01):
            pass

    monkeypatch.setattr(discovery_streaming, "_stream_discovery", waiting_pipeline)
    stop_event = threading.Event()
    events = discovery_streaming.stream_discovery(
        SimpleNamespace(),
        user_id=7,
        stop_event=stop_event,
    )

    with caplog.at_level(logging.WARNING, logger=discovery_streaming.__name__):
        assert next(events)["stage"] == "generating"
        stop_event.set()
        assert list(events) == []

    assert "discovery_stream_cancelled" in caplog.text
    assert "stage=generating" in caplog.text
    assert "stop_event_set=True" in caplog.text
