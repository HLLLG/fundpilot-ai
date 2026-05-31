import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.main import app
from app.models import Holding
from app.services.inbox_processor import process_inbox_file
from app.services.job_store import create_analysis_job, get_job_response


client = TestClient(app)


def test_automation_status_endpoint():
    response = client.get("/api/automation/status")
    assert response.status_code == 200
    body = response.json()
    assert "inbox_dir" in body
    assert "schedule_time" in body


def test_async_analyze_job_completes_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()

    from app.models import NewsItem
    from app.services.news_service import NewsService

    def fake_search(self, topic: str, limit: int | None = None):
        return [NewsItem(topic=topic, title=f"{topic}新闻")]

    monkeypatch.setattr(NewsService, "search", fake_search)

    from app.models import AnalysisRequest

    job_id = create_analysis_job(
        AnalysisRequest(
            holdings=[
                Holding(
                    fund_code="015608",
                    fund_name="测试基金",
                    holding_amount=1000,
                    return_percent=1.0,
                )
            ]
        )
    )

    for _ in range(30):
        job = get_job_response(job_id)
        assert job is not None
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("job did not finish in time")

    assert job["status"] == "completed"
    assert job.get("report") is not None


def test_inbox_processor_creates_event(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_INBOX_DIR", str(tmp_path / "inbox"))
    refresh_settings()

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    image = inbox / "sample.png"
    image.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    )

    def fake_extract(self, path: Path) -> str:
        return "测试基金A\n015608\n持有金额 1000\n持有收益率 -1.5%"

    from app.services.ocr_engine import OcrEngine

    monkeypatch.setattr(OcrEngine, "extract_text", fake_extract)

    event = process_inbox_file(image)
    assert event is not None
    assert event["kind"] == "ocr_ready"
    assert event["payload"]["holdings"]
    assert not image.exists()
