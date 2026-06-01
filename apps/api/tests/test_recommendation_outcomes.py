from app.services.recommendation_outcomes import build_recommendation_outcomes


def test_build_outcomes_between_two_reports():
    previous = {
        "id": "prev",
        "created_at": "2026-05-31",
        "risk": {"weighted_return_percent": -2},
        "holdings": [
            {
                "fund_code": "015608",
                "fund_name": "A",
                "holding_return_percent": -3,
            }
        ],
        "fund_recommendations": [
            {"fund_code": "015608", "fund_name": "A", "action": "观察"},
        ],
    }
    current = {
        "id": "curr",
        "created_at": "2026-06-01",
        "risk": {"weighted_return_percent": -1},
        "holdings": [
            {
                "fund_code": "015608",
                "fund_name": "A",
                "holding_return_percent": -1,
            }
        ],
        "fund_recommendations": [
            {"fund_code": "015608", "fund_name": "A", "action": "暂停追涨"},
        ],
    }

    outcomes = build_recommendation_outcomes(current, previous)

    assert outcomes["has_baseline"] is True
    assert outcomes["items"][0]["holding_return_delta"] == 2.0
