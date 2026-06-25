"""Preview OCR 应只做识别+查码，不做档案 enrichment（养基宝式快路径）。"""

from app.models import Holding
from app.services import ocr_pipeline
from app.services.fund_profile import FundProfileService
from app.services.holdings_extractor import ExtractionResult


def _fake_extraction():
    return ExtractionResult(
        holdings=[
            Holding(
                fund_code="000000",
                fund_name="华夏中证电网设备主题ETF联接C",
                holding_amount=2000.01,
                holding_profit=0.01,
            )
        ],
        ocr_source="alipay_holdings",
        raw_text="华夏中证电网设备主题ETF联接C",
        provider="vlm",
    )


def test_preview_skips_resolve_holdings_and_enrich(monkeypatch):
    calls = {"resolve_holdings": 0, "enrich": 0}

    class TrackingProfileService(FundProfileService):
        def resolve_holdings(self, holdings):
            calls["resolve_holdings"] += 1
            return super().resolve_holdings(holdings)

    monkeypatch.setattr(ocr_pipeline, "FundProfileService", TrackingProfileService)
    monkeypatch.setattr(ocr_pipeline, "extract_holdings", lambda **kwargs: _fake_extraction())
    monkeypatch.setattr(
        "app.services.ocr_pipeline.get_previous_holdings_for_review",
        lambda: [],
    )

    def fake_enrich(holdings):
        calls["enrich"] += 1
        return holdings

    monkeypatch.setattr(ocr_pipeline, "enrich_holdings_from_profiles", fake_enrich)
    monkeypatch.setattr(
        ocr_pipeline,
        "_resolve_fund_codes",
        lambda holdings, profile_service: (holdings, []),
    )

    result = ocr_pipeline.run_ocr_upload_pipeline(
        file_bytes=b"img", filename="x.png", preview=True
    )

    assert result["preview"] is True
    assert len(result["holdings"]) == 1
    assert result["holdings"][0]["fund_name"] == "华夏中证电网设备主题ETF联接C"
    assert result["holdings"][0]["holding_amount"] == 2000.01
    assert result["sector_refresh"]["skipped"] is True
    assert calls["resolve_holdings"] == 0
    assert calls["enrich"] == 0


def test_non_preview_still_runs_full_pipeline(monkeypatch):
    calls = {"process": 0}

    monkeypatch.setattr(ocr_pipeline, "extract_holdings", lambda **kwargs: _fake_extraction())
    monkeypatch.setattr(
        "app.services.ocr_pipeline.get_previous_holdings_for_review",
        lambda: [],
    )
    monkeypatch.setattr(
        ocr_pipeline,
        "_resolve_fund_codes",
        lambda holdings, profile_service: (holdings, []),
    )

    def fake_process(holdings, **kwargs):
        calls["process"] += 1
        return holdings, {"ok": True, "holdings": []}, kwargs.get("portfolio_summary")

    monkeypatch.setattr(ocr_pipeline, "process_overview_holdings", fake_process)
    monkeypatch.setattr(ocr_pipeline, "save_daily_snapshot", lambda *args, **kwargs: None)

    ocr_pipeline.run_ocr_upload_pipeline(file_bytes=b"img", filename="x.png", preview=False)
    assert calls["process"] == 1
