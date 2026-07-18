from app.config import Settings, refresh_settings
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


def test_tactical_prompt_tuning_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FUND_AI_TACTICAL_PROMPT_TUNING_ENABLED", raising=False)

    settings = Settings(_env_file=None)

    assert settings.tactical_prompt_tuning_enabled is False


def test_deepseek_resilience_defaults_are_bounded(monkeypatch):
    for name in (
        "FUND_AI_DEEPSEEK_MAX_TOKENS",
        "FUND_AI_DEEPSEEK_MAX_TOKENS_REPORT",
        "FUND_AI_DEEPSEEK_CONNECTION_RETRIES",
        "FUND_AI_DEEPSEEK_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.deepseek_max_tokens == 32_768
    assert settings.deepseek_max_tokens_report == 32_768
    assert settings.deepseek_connection_retries == 2
    assert settings.deepseek_timeout_seconds == 300


def test_holdings_cache_defaults_are_safe_for_mysql_multiworker(monkeypatch):
    monkeypatch.delenv("FUND_AI_HOLDINGS_MEMORY_CACHE_ENABLED", raising=False)

    mysql = Settings(
        _env_file=None,
        database_url="mysql://user:pass@db:3306/fundpilot",
    )
    sqlite = Settings(_env_file=None, database_url=None)

    assert mysql.resolved_holdings_memory_cache_enabled is False
    assert sqlite.resolved_holdings_memory_cache_enabled is True
    assert mysql.portfolio_mutation_lock_timeout_seconds == 30


def test_runtime_role_and_background_worker_health_defaults(monkeypatch):
    monkeypatch.delenv("FUND_AI_RUNTIME_ROLE", raising=False)

    settings = Settings(_env_file=None)

    assert settings.runtime_role == "all"
    assert settings.background_worker_lock_timeout_seconds == 5
    assert settings.background_worker_retry_seconds == 5
    assert settings.background_worker_heartbeat_interval_seconds == 10
    assert settings.background_worker_heartbeat_stale_seconds == 45
