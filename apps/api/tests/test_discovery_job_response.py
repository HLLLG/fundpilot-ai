from __future__ import annotations

from app.database import (
    DISCOVERY_SUMMARY_FIELDS,
    get_discovery_report,
    get_discovery_report_summary,
    save_discovery_report,
)
from app.models import FundDiscoveryReport
from app.services.discovery_job_store import get_discovery_job_response


def _heavy_report() -> FundDiscoveryReport:
    return FundDiscoveryReport(
        title="体积很大的荐基报告",
        summary="摘要",
        market_view="观点",
        target_sectors=["半导体"],
        focus_sectors=["半导体"],
        caveats=["注意"],
        discovery_facts={"pipeline": {"analysis_mode": "deep"}, "noise": "x" * 100},
        candidate_pool=[{"fund_code": "000001", "fund_name": "测试基金"}],
        decision_events=[{"id": "evt-1", "payload": "y" * 100}],
        recommendations=[
            {
                "fund_code": "000001",
                "fund_name": "测试基金",
                "sector_name": "半导体",
                "action": "建议关注",
            }
        ],
    )


def test_discovery_report_summary_omits_heavy_fields() -> None:
    saved = save_discovery_report(_heavy_report())
    full = get_discovery_report(saved.id)
    summary = get_discovery_report_summary(saved.id)

    assert full is not None
    assert "discovery_facts" in full
    assert "candidate_pool" in full
    assert summary is not None
    assert set(summary) <= set(DISCOVERY_SUMMARY_FIELDS)
    assert "discovery_facts" not in summary
    assert "candidate_pool" not in summary
    assert "decision_events" not in summary
    assert "recommendations" not in summary
    assert summary["title"] == "体积很大的荐基报告"


def test_job_status_response_embeds_summary_not_full_report(monkeypatch) -> None:
    saved = save_discovery_report(_heavy_report())
    monkeypatch.setattr(
        "app.services.discovery_job_store.get_discovery_job",
        lambda _job_id: {
            "id": "discovery-job",
            "status": "completed",
            "request": {"analysis_mode": "deep"},
            "discovery_report_id": saved.id,
            "error": None,
            "stage": "completed",
            "stage_label": "推荐报告已生成",
            "created_at": "2026-08-24T00:00:00+00:00",
            "updated_at": "2026-08-24T00:00:01+00:00",
        },
    )

    response = get_discovery_job_response("discovery-job")

    assert response is not None
    assert response["discovery_report_id"] == saved.id
    slim = response["discovery_report"]
    assert "discovery_facts" not in slim
    assert "candidate_pool" not in slim
    assert "decision_events" not in slim
    assert "recommendations" not in slim
    assert slim["title"] == "体积很大的荐基报告"
