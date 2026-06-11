from app.services.recommendation_accuracy import build_recommendation_accuracy


def test_build_recommendation_accuracy_groups_by_style(monkeypatch):
    reports = [
        {
            "id": "r2",
            "created_at": "2026-06-10T10:00:00+00:00",
            "holdings": [
                {
                    "fund_code": "015608",
                    "fund_name": "A",
                    "daily_return_percent": -1.2,
                    "holding_return_percent": 3,
                }
            ],
            "fund_recommendations": [{"fund_code": "015608", "action": "观察"}],
            "analysis_facts": {"portfolio": {"decision_style": "tactical"}},
        },
        {
            "id": "r1",
            "created_at": "2026-06-09T10:00:00+00:00",
            "holdings": [
                {
                    "fund_code": "015608",
                    "fund_name": "A",
                    "daily_return_percent": 2.0,
                    "holding_return_percent": 2,
                }
            ],
            "fund_recommendations": [{"fund_code": "015608", "action": "分批加仓"}],
            "analysis_facts": {"portfolio": {"decision_style": "tactical"}},
        },
    ]
    monkeypatch.setattr(
        "app.services.recommendation_accuracy.list_reports",
        lambda: reports,
    )
    result = build_recommendation_accuracy(limit_reports=10)
    assert result["has_enough_data"] is True
    tactical = result["by_style"]["tactical"]
    assert tactical["paired_count"] == 1
    assert tactical["reversal"]["up_then_down_count"] == 1
