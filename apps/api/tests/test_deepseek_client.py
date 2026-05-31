from app.models import AnalysisRequest, Holding, InvestorProfile, RiskAssessment
from app.services.deepseek_client import _build_payload, _parse_model_json


def test_deepseek_payload_limits_response_tokens():
    payload = _build_payload(
        request=AnalysisRequest(
            holdings=[
                Holding(
                    fund_code="025856",
                    fund_name="华夏中证电网设备主题ETF联接A",
                    holding_amount=15075.46,
                    return_percent=0.87,
                )
            ],
            profile=InvestorProfile(),
        ),
        risk=RiskAssessment(
            level="medium",
            suggested_action="watch",
            weighted_return_percent=0.87,
            alerts=[],
        ),
        snapshots=[],
        model="deepseek-v4-pro",
        max_tokens=1800,
    )

    assert payload["model"] == "deepseek-v4-pro"
    assert payload["max_tokens"] == 1800
    assert "response_format" in payload


def test_parse_model_json_extracts_object_from_wrapped_content():
    parsed = _parse_model_json(
        'Here is the JSON:\n```json\n{"title":"Daily report","summary":"Readable decision","recommendations":["pause AI adds"],"caveats":[]}\n```'
    )

    assert parsed["title"] == "Daily report"
    assert parsed["summary"] == "Readable decision"
    assert parsed["recommendations"] == ["pause AI adds"]
    assert parsed["caveats"] == []


def test_parse_model_json_salvages_truncated_json_without_showing_raw_object():
    parsed = _parse_model_json(
        '{"title":"2026-05-29 Portfolio","summary":"Grid concentration is too high. Pause AI adds.","recommend'
    )

    assert parsed["title"] == "2026-05-29 Portfolio"
    assert parsed["summary"] == "Grid concentration is too high. Pause AI adds."
    assert parsed["recommendations"] == []
    assert parsed.get("_truncated") is True
    assert not parsed["summary"].lstrip().startswith("{")
