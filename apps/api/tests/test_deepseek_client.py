from app.models import AnalysisRequest, Holding, InvestorProfile, RiskAssessment
from app.services.deepseek_client import _build_payload


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
        market_context=[],
        model="deepseek-v4-pro",
        max_tokens=1800,
    )

    assert payload["model"] == "deepseek-v4-pro"
    assert payload["max_tokens"] == 1800
    assert "response_format" in payload
