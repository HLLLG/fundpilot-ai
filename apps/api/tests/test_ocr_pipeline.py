def test_pipeline_reports_extraction_provider(monkeypatch):
    from app.models import Holding
    from app.services import ocr_pipeline
    from app.services.holdings_extractor import ExtractionResult

    def fake_extract(*, file_bytes, text, settings=None, vlm_fn=None, local_fn=None):
        return ExtractionResult(
            holdings=[Holding(fund_code="000000", fund_name="某基金C", holding_amount=100.0)],
            ocr_source="alipay_holdings",
            raw_text="某基金C",
            provider="vlm",
        )

    monkeypatch.setattr(ocr_pipeline, "extract_holdings", fake_extract)
    monkeypatch.setattr(
        "app.services.ocr_pipeline.get_previous_holdings_for_review",
        lambda: [],
    )
    result = ocr_pipeline.run_ocr_upload_pipeline(
        file_bytes=b"img", filename="x.png", preview=True
    )
    assert result["extraction_provider"] == "vlm"
    assert result["holdings"]
    assert result["ocr_source"] == "alipay_holdings"
