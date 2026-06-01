from app.config import Settings, refresh_settings


def test_placeholder_deepseek_key_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "sk-your-deepseek-key")
    refresh_settings()
    settings = refresh_settings()

    assert settings.deepseek_api_key is None
    assert settings.deepseek_configured is False


def test_realistic_deepseek_key_is_accepted(monkeypatch):
    monkeypatch.setenv(
        "FUND_AI_DEEPSEEK_API_KEY",
        "sk-1234567890abcdef1234567890abcdef",
    )
    settings = refresh_settings()

    assert settings.deepseek_api_key is not None
    assert settings.deepseek_configured is True


def test_deepseek_key_strips_quotes_and_whitespace(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", '  "sk-1234567890abcdef1234567890abcdef"  ')
    settings = refresh_settings()

    assert settings.deepseek_api_key == "sk-1234567890abcdef1234567890abcdef"
