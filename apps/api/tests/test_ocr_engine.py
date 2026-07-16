from __future__ import annotations

import sys
from types import SimpleNamespace

from app.services import ocr_engine


def _install_fake_paddleocr(monkeypatch, captured: list[dict]) -> None:
    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "paddleocr",
        SimpleNamespace(PaddleOCR=FakePaddleOCR),
    )


def test_preload_sets_current_paddlex_flag_and_omits_redundant_lang(monkeypatch) -> None:
    captured: list[dict] = []
    _install_fake_paddleocr(monkeypatch, captured)
    monkeypatch.delenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", raising=False)
    monkeypatch.setattr(
        ocr_engine,
        "get_settings",
        lambda: SimpleNamespace(ocr_use_mobile_models=True, ocr_max_image_side=1280),
    )
    ocr_engine._ocr_instance.cache_clear()

    try:
        ocr_engine.preload_ocr_engine()
    finally:
        ocr_engine._ocr_instance.cache_clear()

    assert captured == [
        {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "text_det_limit_side_len": 1280,
            "text_detection_model_name": "PP-OCRv4_mobile_det",
            "text_recognition_model_name": "PP-OCRv4_mobile_rec",
        }
    ]
    assert ocr_engine.os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"


def test_default_model_selection_keeps_chinese_language(monkeypatch) -> None:
    captured: list[dict] = []
    _install_fake_paddleocr(monkeypatch, captured)
    monkeypatch.setattr(
        ocr_engine,
        "get_settings",
        lambda: SimpleNamespace(ocr_use_mobile_models=False, ocr_max_image_side=1280),
    )
    ocr_engine._ocr_instance.cache_clear()

    try:
        ocr_engine._ocr_instance()
    finally:
        ocr_engine._ocr_instance.cache_clear()

    assert captured[0]["lang"] == "ch"
    assert "text_detection_model_name" not in captured[0]
