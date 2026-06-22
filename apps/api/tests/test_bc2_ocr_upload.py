"""BC2 verification tests: multipart/form-data upload parity for OCR endpoints.

Verifies that both /api/ocr and /api/transactions/ocr:
- Accept the same multipart/form-data structure that Web's FormData upload produces
  (a 'file' field with image bytes + optional 'preview' field)
- Return status 200
- Return a JSON response whose top-level shape matches the expected contract

The tests stub out OcrEngine.extract_text to avoid depending on PaddleOCR being
installed, just as the other OCR tests in test_api.py do.

Requirements: 6.7, 14.3
"""

import io

from fastapi.testclient import TestClient

from app.config import refresh_settings
from tests.conftest import auth_client_for_db

# ---------------------------------------------------------------------------
# A minimal 1×1 grey PNG (26 bytes) so we have a real in-memory image file.
# This is the smallest valid PNG that any image-aware code can recognise.
# ---------------------------------------------------------------------------
_TINY_PNG = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
    0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR length + type
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # width=1, height=1
    0x08, 0x00, 0x00, 0x00, 0x00, 0x3A, 0x7E, 0x9B,  # bitdepth=8, colortype=0 + crc
    0x55, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41,  # IDAT length + type
    0x54, 0x78, 0x9C, 0x62, 0x00, 0x00, 0x00, 0x02,  # IDAT compressed data
    0x00, 0x01, 0xE5, 0x27, 0xDE, 0xFC,              # IDAT crc
    0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44,  # IEND length + type
    0xAE, 0x42, 0x60, 0x82,                           # IEND crc
])


def _make_client(tmp_path, monkeypatch) -> TestClient:
    """Return an authenticated TestClient with a fresh DB and OCR stub."""
    from app.services.ocr_engine import OcrEngine

    monkeypatch.setenv("FUND_AI_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = auth_client_for_db(monkeypatch, tmp_path / "app.db")

    # Stub OcrEngine so we don't need PaddleOCR installed.
    # Returns empty text → pipeline returns empty holdings, which is fine;
    # we only check the response contract shape.
    def _stub_extract(self, image_path):
        return ""

    monkeypatch.setattr(OcrEngine, "extract_text", _stub_extract)
    return client


# ---------------------------------------------------------------------------
# /api/ocr  –  holdings OCR endpoint
# ---------------------------------------------------------------------------

class TestOcrEndpointBC2:
    """BC2: /api/ocr accepts multipart/form-data (file + preview) and returns correct shape."""

    def test_file_upload_preview_true_returns_200(self, tmp_path, monkeypatch):
        """POST multipart/form-data with file + preview=true returns 200."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/ocr",
            files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
            data={"preview": "true"},
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_file_upload_preview_true_returns_json(self, tmp_path, monkeypatch):
        """Response is valid JSON."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/ocr",
            files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
            data={"preview": "true"},
        )
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, dict), f"Expected dict, got {type(body)}"

    def test_file_upload_preview_true_has_holdings_key(self, tmp_path, monkeypatch):
        """Response contains 'holdings' key (list)."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/ocr",
            files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
            data={"preview": "true"},
        )
        body = response.json()
        assert "holdings" in body, f"Missing 'holdings' key in response: {list(body.keys())}"
        assert isinstance(body["holdings"], list)

    def test_file_upload_preview_true_has_ocr_source_key(self, tmp_path, monkeypatch):
        """Response contains 'ocr_source' key."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/ocr",
            files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
            data={"preview": "true"},
        )
        body = response.json()
        assert "ocr_source" in body, f"Missing 'ocr_source' key: {list(body.keys())}"

    def test_file_upload_preview_true_has_preview_key(self, tmp_path, monkeypatch):
        """Response echoes 'preview' field."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/ocr",
            files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
            data={"preview": "true"},
        )
        body = response.json()
        assert "preview" in body, f"Missing 'preview' key: {list(body.keys())}"
        assert body["preview"] is True

    def test_file_upload_preview_true_has_profile_sync_key(self, tmp_path, monkeypatch):
        """Response contains 'profile_sync' key (skipped in preview mode)."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/ocr",
            files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
            data={"preview": "true"},
        )
        body = response.json()
        assert "profile_sync" in body, f"Missing 'profile_sync': {list(body.keys())}"
        # In preview mode the sync should be marked as skipped
        assert body["profile_sync"].get("skipped") is True

    def test_full_response_contract(self, tmp_path, monkeypatch):
        """All expected top-level keys are present in a single assertion."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/ocr",
            files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
            data={"preview": "true"},
        )
        assert response.status_code == 200
        body = response.json()

        expected_keys = {
            "holdings",
            "ocr_source",
            "preview",
            "profile_sync",
            "raw_text",
            "fund_code_resolutions",
            "holding_warnings",
            "holding_diffs",
            "previous_holdings",
            "warning_count",
        }
        missing = expected_keys - set(body.keys())
        assert not missing, f"Missing keys in /api/ocr response: {missing}"

    def test_file_upload_without_preview_returns_200(self, tmp_path, monkeypatch):
        """POST multipart/form-data with file only (no preview) also returns 200."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/ocr",
            files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
        )
        assert response.status_code == 200
        body = response.json()
        assert "holdings" in body

    def test_file_field_name_matches_web_formdata(self, tmp_path, monkeypatch):
        """The field name 'file' (used by Web FormData) is accepted without error."""
        client = _make_client(tmp_path, monkeypatch)
        # Web sends: formData.append('file', blob, filename)
        response = client.post(
            "/api/ocr",
            files={"file": ("fund_screenshot.jpg", io.BytesIO(_TINY_PNG), "image/jpeg")},
            data={"preview": "true"},
        )
        assert response.status_code == 200
        # The endpoint should not fail with a validation error
        body = response.json()
        assert "error" not in body or body.get("holdings") is not None


# ---------------------------------------------------------------------------
# /api/transactions/ocr  –  transaction OCR endpoint
# ---------------------------------------------------------------------------

class TestTransactionsOcrEndpointBC2:
    """BC2: /api/transactions/ocr accepts multipart/form-data (file) and returns correct shape."""

    def test_file_upload_returns_200(self, tmp_path, monkeypatch):
        """POST multipart/form-data with file returns 200."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/transactions/ocr",
            files={"file": ("tx_screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_file_upload_returns_json(self, tmp_path, monkeypatch):
        """Response is valid JSON."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/transactions/ocr",
            files={"file": ("tx_screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
        )
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, dict)

    def test_file_upload_has_transactions_key(self, tmp_path, monkeypatch):
        """Response contains 'transactions' key (list)."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/transactions/ocr",
            files={"file": ("tx_screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
        )
        body = response.json()
        assert "transactions" in body, f"Missing 'transactions' key: {list(body.keys())}"
        assert isinstance(body["transactions"], list)

    def test_file_upload_has_ocr_source_key(self, tmp_path, monkeypatch):
        """Response contains 'ocr_source' key."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/transactions/ocr",
            files={"file": ("tx_screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
        )
        body = response.json()
        assert "ocr_source" in body, f"Missing 'ocr_source' key: {list(body.keys())}"

    def test_full_response_contract(self, tmp_path, monkeypatch):
        """Both expected top-level keys are present."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/transactions/ocr",
            files={"file": ("tx_screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
        )
        assert response.status_code == 200
        body = response.json()
        expected_keys = {"transactions", "ocr_source"}
        missing = expected_keys - set(body.keys())
        assert not missing, f"Missing keys in /api/transactions/ocr response: {missing}"

    def test_empty_ocr_text_returns_empty_transactions(self, tmp_path, monkeypatch):
        """When OCR returns empty text, transactions list is empty (graceful degradation)."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/transactions/ocr",
            files={"file": ("tx.png", io.BytesIO(_TINY_PNG), "image/png")},
        )
        body = response.json()
        # OcrEngine stub returns "" → no transactions parsed
        assert body["transactions"] == []

    def test_file_field_name_matches_web_formdata(self, tmp_path, monkeypatch):
        """The field name 'file' (Web FormData) is accepted without validation error."""
        client = _make_client(tmp_path, monkeypatch)
        response = client.post(
            "/api/transactions/ocr",
            files={"file": ("buy_sell.jpg", io.BytesIO(_TINY_PNG), "image/jpeg")},
        )
        assert response.status_code == 200
        body = response.json()
        assert "transactions" in body


# ---------------------------------------------------------------------------
# Cross-endpoint parity: both endpoints share the same upload contract
# ---------------------------------------------------------------------------

def test_both_ocr_endpoints_accept_same_multipart_shape(tmp_path, monkeypatch):
    """Both /api/ocr and /api/transactions/ocr accept identical multipart/form-data structure."""
    client = _make_client(tmp_path, monkeypatch)

    holdings_resp = client.post(
        "/api/ocr",
        files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
        data={"preview": "true"},
    )
    transactions_resp = client.post(
        "/api/transactions/ocr",
        files={"file": ("screenshot.png", io.BytesIO(_TINY_PNG), "image/png")},
    )

    assert holdings_resp.status_code == 200, f"/api/ocr failed: {holdings_resp.text}"
    assert transactions_resp.status_code == 200, f"/api/transactions/ocr failed: {transactions_resp.text}"

    holdings_body = holdings_resp.json()
    transactions_body = transactions_resp.json()

    assert isinstance(holdings_body.get("holdings"), list)
    assert isinstance(transactions_body.get("transactions"), list)
    # Both carry an ocr_source string
    assert isinstance(holdings_body.get("ocr_source"), str)
    assert isinstance(transactions_body.get("ocr_source"), str)
