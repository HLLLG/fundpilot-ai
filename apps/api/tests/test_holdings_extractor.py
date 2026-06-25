from app.config import Settings
from app.models import Holding
from app.services.holdings_extractor import extract_holdings


def _holding(name: str) -> list[Holding]:
    return [Holding(fund_code="000000", fund_name=name, holding_amount=100.0)]


def test_auto_uses_vlm_when_key_present():
    s = Settings(ocr_provider="auto", vlm_ocr_api_key="sk-test")
    result = extract_holdings(
        file_bytes=b"img",
        text="",
        settings=s,
        vlm_fn=lambda b, settings: (_holding("VLM基金C"), "vlm-ocr-text"),
        local_fn=lambda b, t: (_holding("本地基金C"), "raw"),
    )
    assert result.provider == "vlm"
    assert result.holdings[0].fund_name == "VLM基金C"
    assert result.raw_text == "vlm-ocr-text"  # VLM 路径也透传 OCR 文本


def test_auto_falls_back_to_local_on_vlm_error():
    s = Settings(ocr_provider="auto", vlm_ocr_api_key="sk-test")

    def boom(b, settings):
        raise RuntimeError("vlm down")

    result = extract_holdings(
        file_bytes=b"img",
        text="",
        settings=s,
        vlm_fn=boom,
        local_fn=lambda b, t: (_holding("本地基金C"), "raw"),
    )
    assert result.provider == "local"
    assert result.holdings[0].fund_name == "本地基金C"


def test_auto_uses_local_when_no_key():
    s = Settings(ocr_provider="auto", vlm_ocr_api_key=None)
    result = extract_holdings(
        file_bytes=b"img",
        text="",
        settings=s,
        vlm_fn=lambda b, settings: (_holding("VLM基金C"), "vlm-ocr-text"),
        local_fn=lambda b, t: (_holding("本地基金C"), "raw"),
    )
    assert result.provider == "local"


def test_provider_local_forces_local_even_with_key():
    s = Settings(ocr_provider="local", vlm_ocr_api_key="sk-test")
    result = extract_holdings(
        file_bytes=b"img",
        text="",
        settings=s,
        vlm_fn=lambda b, settings: (_holding("VLM基金C"), "vlm-ocr-text"),
        local_fn=lambda b, t: (_holding("本地基金C"), "raw"),
    )
    assert result.provider == "local"


def test_manual_text_uses_local_no_vlm():
    s = Settings(ocr_provider="auto", vlm_ocr_api_key="sk-test")
    called = {"vlm": False}

    def vlm(b, settings):
        called["vlm"] = True
        return _holding("VLM基金C"), "vlm-ocr-text"

    result = extract_holdings(
        file_bytes=None,
        text="某基金C\n1000.00\n0.00%",
        settings=s,
        vlm_fn=vlm,
        local_fn=lambda b, t: (_holding("本地基金C"), t),
    )
    assert called["vlm"] is False
    assert result.provider == "local"
