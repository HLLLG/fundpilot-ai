from pathlib import Path

from app.config import refresh_settings
from app.services.ocr_pipeline import _cleanup_upload_artifacts, run_ocr_upload_pipeline


def test_cleanup_upload_artifacts_removes_original_and_prepared(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setenv("FUND_AI_UPLOAD_DIR", str(upload_dir))
    refresh_settings()

    original = upload_dir / "screenshot.png"
    prepared = upload_dir / "screenshot.ocr-prepared.jpg"
    original.write_bytes(b"png")
    prepared.write_bytes(b"jpg")

    _cleanup_upload_artifacts(original)

    assert not original.exists()
    assert not prepared.exists()


def test_run_ocr_upload_pipeline_deletes_file_after_text_upload(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("FUND_AI_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    fixture = Path(__file__).parent / "fixtures" / "alipay_holdings_list_ocr.txt"
    text = fixture.read_text(encoding="utf-8")

    run_ocr_upload_pipeline(
        text=text,
        file_bytes=b"same-image-bytes",
        filename="same.png",
        preview=True,
    )

    assert not (upload_dir / "same.png").exists()


def test_run_ocr_upload_pipeline_deletes_file_after_ocr_failure(tmp_path, monkeypatch):
    from app.services.ocr_engine import OcrEngine

    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("FUND_AI_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()

    def raise_ocr_error(self, image_path):
        raise RuntimeError("boom")

    monkeypatch.setattr(OcrEngine, "extract_text", raise_ocr_error)

    result = run_ocr_upload_pipeline(
        file_bytes=b"broken-image",
        filename="fund.png",
    )

    assert "OCR 识别失败" in result["error"]
    assert not (upload_dir / "fund.png").exists()
