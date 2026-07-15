from __future__ import annotations

import pytest

from app import mysql_admin_bootstrap


class _Connection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_admin_bootstrap_uses_deployment_credentials_without_building_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "root-secret:/?#[]@")
    monkeypatch.setenv("MYSQL_DATABASE", "fundpilot")
    monkeypatch.setenv("FUND_AI_MYSQL_ADMIN_HOST", "mysql-admin")
    monkeypatch.setenv("FUND_AI_MYSQL_ADMIN_PORT", "3307")
    monkeypatch.setenv("FUND_AI_MYSQL_ADMIN_USER", "schema-owner")
    connection = _Connection()
    observed: dict[str, object] = {}

    def connect(**kwargs):
        observed.update(kwargs)
        return connection

    bootstrapped: list[object] = []
    monkeypatch.setattr(mysql_admin_bootstrap.pymysql, "connect", connect)
    monkeypatch.setattr(
        mysql_admin_bootstrap,
        "ensure_mysql_schema",
        lambda value: bootstrapped.append(value),
    )

    mysql_admin_bootstrap.run_admin_bootstrap()

    assert observed == {
        "host": "mysql-admin",
        "port": 3307,
        "user": "schema-owner",
        "password": "root-secret:/?#[]@",
        "database": "fundpilot",
        "charset": "utf8mb4",
        "connect_timeout": 20,
        "read_timeout": 180,
        "write_timeout": 180,
        "autocommit": False,
    }
    assert bootstrapped == [connection]
    assert connection.closed is True


def test_admin_bootstrap_closes_connection_when_schema_contract_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "secret")
    monkeypatch.setenv("MYSQL_DATABASE", "fundpilot")
    connection = _Connection()
    monkeypatch.setattr(
        mysql_admin_bootstrap.pymysql,
        "connect",
        lambda **_kwargs: connection,
    )

    def fail(_connection) -> None:
        raise RuntimeError("schema conflict")

    monkeypatch.setattr(mysql_admin_bootstrap, "ensure_mysql_schema", fail)

    with pytest.raises(RuntimeError, match="schema conflict"):
        mysql_admin_bootstrap.run_admin_bootstrap()

    assert connection.closed is True


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"MYSQL_DATABASE": "fundpilot"}, "MYSQL_ROOT_PASSWORD"),
        ({"MYSQL_ROOT_PASSWORD": "secret"}, "MYSQL_DATABASE"),
    ],
)
def test_admin_bootstrap_requires_admin_environment(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    message: str,
) -> None:
    monkeypatch.delenv("MYSQL_ROOT_PASSWORD", raising=False)
    monkeypatch.delenv("MYSQL_DATABASE", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        mysql_admin_bootstrap.pymysql,
        "connect",
        lambda **_kwargs: pytest.fail("must fail before connecting"),
    )

    with pytest.raises(RuntimeError, match=message):
        mysql_admin_bootstrap.run_admin_bootstrap()


@pytest.mark.parametrize("value", ["not-a-number", "0", "65536"])
def test_admin_bootstrap_rejects_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("MYSQL_ROOT_PASSWORD", "secret")
    monkeypatch.setenv("MYSQL_DATABASE", "fundpilot")
    monkeypatch.setenv("FUND_AI_MYSQL_ADMIN_PORT", value)

    with pytest.raises(RuntimeError, match="FUND_AI_MYSQL_ADMIN_PORT"):
        mysql_admin_bootstrap.run_admin_bootstrap()
