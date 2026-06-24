from app.models import AnalysisRequest, Holding, InvestorProfile, RiskAssessment
from app.services.deepseek_client import (
    _build_payload,
    _compose_analysis_facts,
    _parse_model_json,
)


def test_compose_analysis_facts_wires_evidence(monkeypatch):
    """_compose_analysis_facts 应把 factor_scores/risk_metrics 注入 → 存档 facts 带 evidence。"""
    import app.services.portfolio_snapshot as ps
    from app.services.analysis_runtime import AnalysisRuntime

    monkeypatch.setattr(
        ps,
        "build_factor_scores_for_facts",
        lambda holdings: {
            "available": True,
            "factor_reliability": {
                "momentum": {"level": "高", "basis": "回测显著正向（IC +0.04），置信高"}
            },
            "holdings": [
                {
                    "fund_code": "000001",
                    "composite_grade": "A",
                    "factor_percentiles": {"momentum": 88, "risk_adjusted": 70, "drawdown": 60, "size": 40},
                }
            ],
        },
    )
    monkeypatch.setattr(
        ps,
        "build_risk_metrics_for_facts",
        lambda history, holdings: {
            "available": True,
            "confidence": {"level": "高", "basis": "150 交易日样本，置信高"},
        },
    )

    request = AnalysisRequest(
        holdings=[Holding(fund_code="000001", fund_name="基金A", holding_amount=5000)],
        profile=InvestorProfile(),
    )
    risk = RiskAssessment(
        level="medium", suggested_action="watch", weighted_return_percent=0.0, alerts=[]
    )
    runtime = AnalysisRuntime(
        mode="deep", model="deepseek-v4-pro", news_enabled=False,
        news_max_topics=0, news_tool_max_rounds=0,
    )

    facts = _compose_analysis_facts(
        request=request,
        risk=risk,
        snapshots=[],
        topic_briefs=None,
        nav_trends={},
        runtime=runtime,
        market_news=None,
        judge_meta={},
    )
    row = {h["fund_code"]: h for h in facts["holdings"]}["000001"]
    assert row["evidence"]["composite"]["level"] == "高"
    assert facts["evidence_overview"]["available"] is True


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
