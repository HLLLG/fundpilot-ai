import pytest

from app.auth.test_account_guard import (
    assert_register_allowed,
    is_blocked_test_account,
    is_production_api,
)


@pytest.mark.parametrize(
    "account",
    [
        "user-abc12345@example.com",
        "e2e-123@example.com",
        "uitest+123@example.com",
        "cloudbase-test-123@example.com",
        "debugtest0701@example.com",
    ],
)
def test_blocked_test_accounts(account: str) -> None:
    assert is_blocked_test_account(account)


def test_real_accounts_are_allowed(account: str = "2162803956@qq.com") -> None:
    assert not is_blocked_test_account(account)


def test_register_allowed_in_sqlite_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUND_AI_APP_ENV", raising=False)
    monkeypatch.delenv("FUND_AI_CLOUDBASE_ENV_ID", raising=False)
    monkeypatch.delenv("FUND_AI_ALLOW_TEST_ACCOUNTS", raising=False)
    from app.config import refresh_settings

    refresh_settings()
    assert not is_production_api()
    assert_register_allowed("user-abc12345@example.com")


def test_register_blocked_on_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUND_AI_APP_ENV", "production")
    from app.config import refresh_settings

    refresh_settings()
    assert is_production_api()
    with pytest.raises(ValueError, match="不可用于注册"):
        assert_register_allowed("user-abc12345@example.com")


def test_register_allowed_on_production_with_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUND_AI_APP_ENV", "production")
    monkeypatch.setenv("FUND_AI_ALLOW_TEST_ACCOUNTS", "true")
    from app.config import refresh_settings

    refresh_settings()
    assert not is_production_api()
    assert_register_allowed("user-abc12345@example.com")
