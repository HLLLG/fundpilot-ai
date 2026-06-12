from app.config import PROJECT_ROOT, _resolve_project_root, refresh_settings
from tests.conftest import PYTEST_PLACEHOLDER_DEEPSEEK_KEY, PYTEST_VALID_DEEPSEEK_KEY


def test_project_root_resolves_in_monorepo():
    assert (PROJECT_ROOT / "apps" / "api").is_dir()
    assert (PROJECT_ROOT / "apps" / "web").is_dir()


def test_project_root_resolves_for_docker_layout(tmp_path, monkeypatch):
    docker_root = tmp_path / "app"
    fake_config = docker_root / "app" / "config.py"
    fake_config.parent.mkdir(parents=True)
    fake_config.touch()

    monkeypatch.delenv("FUND_AI_PROJECT_ROOT", raising=False)
    monkeypatch.setattr("app.config.__file__", str(fake_config))

    assert _resolve_project_root() == docker_root


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
