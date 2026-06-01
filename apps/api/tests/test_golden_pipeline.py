from app.config import refresh_settings
from app.models import AnalysisRequest, Holding, InvestorProfile
from app.services.analyze_pipeline import run_analysis


def test_offline_pipeline_caps_aggressive_actions_under_risk_review(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "")
    refresh_settings()

    request = AnalysisRequest(
        holdings=[
            Holding(
                fund_code="015608",
                fund_name="高集中基金",
                holding_amount=9000,
                return_percent=-9,
                sector_return_percent=6,
                holding_return_percent=-8,
            ),
            Holding(
                fund_code="008114",
                fund_name="次要基金",
                holding_amount=1000,
                return_percent=-1,
            ),
        ],
        profile=InvestorProfile(max_drawdown_percent=8, concentration_limit_percent=35),
    )

    report = run_analysis(request)

    assert report.risk.suggested_action == "risk_review"
    assert report.analysis_facts["portfolio"]["risk_level"] == "high"
    for rec in report.fund_recommendations:
        assert "加仓" not in rec.action
        assert "分批" not in rec.action
    for rec in report.fund_recommendations:
        assert rec.news_bullish
        assert rec.news_bearish
