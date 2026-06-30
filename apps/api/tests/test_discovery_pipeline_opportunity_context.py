from __future__ import annotations

from unittest.mock import MagicMock

from app.models import DiscoveryRequest, InvestorProfile
from app.services.discovery_pipeline import run_discovery


def _request() -> DiscoveryRequest:
    return DiscoveryRequest(
        holdings=[],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=30,
            expected_investment_amount=100000,
        ),
        focus_sectors=["半导体"],
    )


def test_run_discovery_does_not_fetch_position_context(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_sector_heat_ranking",
        lambda **_kwargs: [
            {
                "sector_label": "半导体",
                "change_1d_percent": 1.0,
                "change_5d_percent": 3.0,
                "heat_score": 80,
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.select_target_sectors",
        lambda holdings, focus, heat, profile, scan_mode: ["半导体"],
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_sector_flow_map_for_opportunities",
        lambda heat, labels: {"半导体": {"available": True}},
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_sector_position_map_for_opportunities",
        lambda labels: (_ for _ in ()).throw(AssertionError("position context should not be fetched")),
        raising=False,
    )

    def fake_select(heat, **kwargs):
        captured.update(kwargs)
        return [{"sector_label": "半导体", "track": "momentum", "score": 70}]

    monkeypatch.setattr(
        "app.services.discovery_pipeline.select_sector_opportunities",
        fake_select,
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_candidate_pool",
        lambda *args, **kwargs: [
            {"fund_code": "161725", "fund_name": "fund", "sector_label": "半导体"}
        ],
    )
    monkeypatch.setattr("app.services.discovery_pipeline.enrich_candidates", lambda pool: pool)
    monkeypatch.setattr(
        "app.services.discovery_pipeline.NewsService",
        lambda: MagicMock(prefetch_topics=lambda topics: []),
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.summarize_all_topics",
        lambda market_news, offline_only=False: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_discovery_facts",
        lambda **kwargs: {"candidate_pool": kwargs.get("candidate_pool") or []},
    )
    report = MagicMock(id="pipeline-no-position")
    monkeypatch.setattr(
        "app.services.discovery_pipeline.DiscoveryClient",
        lambda: MagicMock(generate_report=lambda **kwargs: report),
    )
    monkeypatch.setattr("app.services.discovery_pipeline.save_discovery_report", lambda report: report)

    assert run_discovery(_request()) is report
    assert "sector_flow_by_label" in captured
    assert "sector_position_by_label" not in captured
