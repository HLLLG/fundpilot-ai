from app.services.discovery_diff import diff_discovery_reports


def test_diff_discovery_reports_detects_added_and_action_change():
    previous = {
        "id": "prev",
        "target_sectors": ["半导体"],
        "recommendations": [
            {"fund_code": "519674", "fund_name": "A", "action": "建议关注"},
        ],
    }
    current = {
        "id": "cur",
        "target_sectors": ["半导体", "商业航天"],
        "recommendations": [
            {"fund_code": "519674", "fund_name": "A", "action": "分批买入"},
            {"fund_code": "015945", "fund_name": "B", "action": "建议关注"},
        ],
    }
    diff = diff_discovery_reports(current, previous)
    assert diff["sector_changes"]["added"] == ["商业航天"]
    assert any(item["type"] == "added" and item["fund_code"] == "015945" for item in diff["recommendation_changes"])
    assert any(
        item.get("fund_code") == "519674" and item.get("action") == "分批买入"
        for item in diff["recommendation_changes"]
    )
