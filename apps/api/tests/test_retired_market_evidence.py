from app.services.retired_market_evidence import sanitize_retired_market_evidence


def test_retired_market_evidence_is_removed_recursively() -> None:
    payload = {
        "summary": "市场震荡。北向实时净买额暂停披露，影响判断。仍需控制仓位。",
        "northbound_status": "not_disclosed",
        "recommendations": [
            {
                "fund_code": "001188",
                "validation_notes": [
                    "北向资金缺失，量化支持不足",
                    "真实申购费率待核验",
                ],
                "risks": ["Northbound flow is unavailable", "最大回撤偏高"],
            }
        ],
    }

    result = sanitize_retired_market_evidence(payload)

    assert result["summary"] == "市场震荡。仍需控制仓位。"
    assert "northbound_status" not in result
    recommendation = result["recommendations"][0]
    assert recommendation["validation_notes"] == ["真实申购费率待核验"]
    assert recommendation["risks"] == ["最大回撤偏高"]
