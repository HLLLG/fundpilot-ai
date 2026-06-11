from app.services.recommendation_outcomes import build_recommendation_outcomes


def test_reversal_up_then_down_assessment():
    previous = {
        "holdings": [
            {
                "fund_code": "015608",
                "fund_name": "测试",
                "holding_return_percent": 5.0,
                "daily_return_percent": 2.0,
            }
        ],
        "fund_recommendations": [
            {"fund_code": "015608", "fund_name": "测试", "action": "观察"},
        ],
    }
    current = {
        "holdings": [
            {
                "fund_code": "015608",
                "fund_name": "测试",
                "holding_return_percent": 4.0,
                "daily_return_percent": -1.2,
            }
        ],
        "fund_recommendations": [
            {"fund_code": "015608", "fund_name": "测试", "action": "暂停追涨"},
        ],
    }
    result = build_recommendation_outcomes(current, previous)
    assert result["items"][0]["reversal_scenario"] == "up_then_down"
    assert "涨后回吐" in result["items"][0]["assessment"]
