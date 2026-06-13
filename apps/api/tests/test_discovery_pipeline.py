from app.models import DiscoveryRequest, InvestorProfile
from app.services.discovery_pipeline import run_discovery


def test_run_discovery_offline(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_sector_heat_ranking",
        lambda: [{"sector_label": "半导体", "heat_score": 1.0, "change_1d_percent": 1.0}],
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.build_candidate_pool",
        lambda *args, **kwargs: [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "sector_label": "半导体",
                "return_1y_percent": 10.0,
            }
        ],
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.enrich_candidates",
        lambda pool: pool,
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.NewsService",
        lambda: type(
            "NS",
            (),
            {
                "prefetch_topics": staticmethod(lambda topics: []),
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.discovery_pipeline.summarize_all_topics",
        lambda topics, news: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_client.get_settings",
        lambda: type("S", (), {"deepseek_api_key": None})(),
    )
    request = DiscoveryRequest(profile=InvestorProfile(), focus_sectors=["半导体"])
    report = run_discovery(request)
    assert report.recommendations
    assert report.recommendations[0].fund_code == "519674"
