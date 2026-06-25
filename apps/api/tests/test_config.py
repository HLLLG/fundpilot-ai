from app.config import refresh_settings
from tests.conftest import PYTEST_PLACEHOLDER_DEEPSEEK_KEY, PYTEST_VALID_DEEPSEEK_KEY


def test_placeholder_deepseek_key_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", PYTEST_PLACEHOLDER_DEEPSEEK_KEY)
    refresh_settings()
    settings = refresh_settings()

    assert settings.deepseek_api_key is None
    assert settings.deepseek_configured is False


def test_realistic_deepseek_key_is_accepted(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", PYTEST_VALID_DEEPSEEK_KEY)
    settings = refresh_settings()

    assert settings.deepseek_api_key is not None
    assert settings.deepseek_configured is True


def test_deepseek_key_strips_quotes_and_whitespace(monkeypatch):
    monkeypatch.setenv(
        "FUND_AI_DEEPSEEK_API_KEY",
        f'  "{PYTEST_VALID_DEEPSEEK_KEY}"  ',
    )
    settings = refresh_settings()

    assert settings.deepseek_api_key == PYTEST_VALID_DEEPSEEK_KEY


def test_vlm_ocr_settings_defaults(monkeypatch):
    monkeypatch.delenv("FUND_AI_OCR_PROVIDER", raising=False)
    monkeypatch.delenv("FUND_AI_VLM_OCR_API_KEY", raising=False)
    monkeypatch.delenv("FUND_AI_VLM_OCR_MODEL", raising=False)

    s = refresh_settings()
    assert s.ocr_provider == "auto"
    assert s.vlm_ocr_model == "qwen-vl-ocr"
    assert s.vlm_ocr_base_url.startswith("https://dashscope.aliyuncs.com")
    assert s.vlm_ocr_timeout_seconds == 20
    # 注：不断言 vlm_ocr_api_key（会被本地 .env 真实 key 覆盖，与「默认值」语义无关）
    # qwen-vl-ocr 图像缩放 + 上传前压缩默认值
    assert s.vlm_ocr_min_pixels == 3072
    assert s.vlm_ocr_max_pixels == 8388608
    assert s.vlm_ocr_compress_enabled is True
    assert s.vlm_ocr_jpeg_quality == 85
    assert s.vlm_ocr_max_image_side == 2000
