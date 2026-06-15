from app.models import InvestorProfile
from app.services.discovery_payload import build_user_payload


def test_build_user_payload_includes_candidate_pool():
    facts = {
        "profile": {"horizon": "半年到一年"},
        "portfolio_gap": {"available_budget_yuan": 5000},
        "sector_heat": [],
        "market_flow": {"available": False},
        "signal_backtest": {},
        "news": {"freshness_label": "empty"},
        "candidate_pool": [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "sector_label": "半导体",
                "return_1y_percent": 12.0,
            }
        ],
    }
    payload = build_user_payload(
        discovery_facts=facts,
        profile=InvestorProfile(),
        focus_sectors=["半导体"],
    )
    assert payload["focus_sectors"] == ["半导体"]
    assert payload["scan_mode"] == "full_market"
    assert "全市场" in payload["requirements"][1]
    assert payload["discovery_facts"]["candidate_pool"][0]["fund_code"] == "519674"


def test_build_user_payload_gap_mode_requirements():
    payload = build_user_payload(
        discovery_facts={"candidate_pool": []},
        profile=InvestorProfile(),
        focus_sectors=[],
        scan_mode="portfolio_gap",
    )
    assert payload["scan_mode"] == "portfolio_gap"
    assert "portfolio_gap" in payload["requirements"][1]
