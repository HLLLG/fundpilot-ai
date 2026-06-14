from app.services.discovery_outcomes import build_discovery_outcomes


def test_build_discovery_outcomes_with_mock_nav():
    report = {
        "id": "r1",
        "created_at": "2026-06-01T10:00:00+00:00",
        "recommendations": [
            {"fund_code": "519674", "fund_name": "测试基金", "action": "分批买入"},
        ],
    }

    def fake_nav(code, trading_days=30):
        return {
            "data": [
                {"date": "2026-06-01", "nav": 1.0},
                {"date": "2026-06-10", "nav": 1.05},
            ]
        }

    payload = build_discovery_outcomes(report, days=7, fetch_nav=fake_nav)
    assert payload["has_data"] is True
    assert payload["items"][0]["fund_code"] == "519674"
    assert payload["items"][0]["period_change_percent"] == 5.0
