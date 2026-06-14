import json

from app.services.job_status_service import _discovery_response_from_row, _row_get


def test_discovery_response_from_row_completed():
    row = {
        "id": "job1",
        "status": "completed",
        "request_payload": json.dumps({"analysis_mode": "deep"}),
        "discovery_report_id": "rep1",
        "error": None,
        "stage": "completed",
        "stage_label": "完成",
        "created_at": "2026-06-14T00:00:00+00:00",
        "updated_at": "2026-06-14T00:00:01+00:00",
    }

    def fake_report(report_id: str):
        assert report_id == "rep1"
        return {"id": "rep1", "title": "测试"}

    import app.services.job_status_service as module

    original = module.get_discovery_report
    module.get_discovery_report = fake_report
    try:
        payload = _discovery_response_from_row(row)
    finally:
        module.get_discovery_report = original

    assert payload["job_kind"] == "discovery"
    assert payload["discovery_report"]["title"] == "测试"


def test_row_get_dict():
    assert _row_get({"status": "running"}, "status") == "running"
