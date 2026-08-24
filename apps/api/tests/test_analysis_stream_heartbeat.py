from __future__ import annotations

import time
from types import SimpleNamespace

from app.services import analyze_streaming


def test_whole_analysis_pipeline_emits_heartbeat_during_unwrapped_work(
    monkeypatch,
) -> None:
    def slow_pipeline(_request, *, user_id: int, stop):
        assert user_id == 7
        assert not stop.is_set()
        yield {
            "type": "stage",
            "stage": "fund_data",
            "label": "正在拉取净值与诊断数据…",
        }
        time.sleep(0.06)
        yield {"type": "done", "report_id": "r1", "report": {"id": "r1"}}

    monkeypatch.setattr(analyze_streaming, "_stream_analysis_on_lane", slow_pipeline)
    monkeypatch.setattr(analyze_streaming, "PIPELINE_HEARTBEAT_SECONDS", 0.01)

    events = list(analyze_streaming.stream_analysis(SimpleNamespace(), user_id=7))

    fund_data_events = [
        event
        for event in events
        if event.get("type") == "stage" and event.get("stage") == "fund_data"
    ]
    assert len(fund_data_events) >= 2
    assert all(
        event["label"] == "正在拉取净值与诊断数据…" for event in fund_data_events
    )
    assert events[-1]["type"] == "done"
