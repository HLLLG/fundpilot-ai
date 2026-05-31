from app.services.report_diff import diff_reports


def test_diff_reports_detects_holding_and_risk_changes():
    previous = {
        "id": "prev",
        "title": "昨日",
        "created_at": "2026-05-30T00:00:00+00:00",
        "risk": {
            "level": "low",
            "suggested_action": "watch",
            "weighted_return_percent": 1.0,
            "alerts": [],
        },
        "holdings": [
            {
                "fund_code": "015608",
                "fund_name": "基金A",
                "holding_amount": 5000,
                "return_percent": 1.0,
            }
        ],
        "fund_recommendations": [
            {"fund_code": "015608", "fund_name": "基金A", "action": "观察", "points": []}
        ],
    }
    current = {
        "id": "cur",
        "title": "今日",
        "created_at": "2026-05-31T00:00:00+00:00",
        "risk": {
            "level": "medium",
            "suggested_action": "pause_add",
            "weighted_return_percent": -2.0,
            "alerts": [],
        },
        "holdings": [
            {
                "fund_code": "015608",
                "fund_name": "基金A",
                "holding_amount": 5200,
                "return_percent": -1.0,
            },
            {
                "fund_code": "008114",
                "fund_name": "基金B",
                "holding_amount": 1000,
                "return_percent": 0.5,
            },
        ],
        "fund_recommendations": [
            {"fund_code": "015608", "fund_name": "基金A", "action": "暂停加仓", "points": []}
        ],
    }

    result = diff_reports(current, previous)

    assert result["risk_level_changed"] is True
    assert result["weighted_return_delta"] == -3.0
    assert any(change["type"] == "added" and change["fund_code"] == "008114" for change in result["holding_changes"])
    assert any(change["type"] == "changed" and change["fund_code"] == "015608" for change in result["holding_changes"])
    assert result["recommendation_changes"][0]["current_action"] == "暂停加仓"
