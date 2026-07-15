"""Run the production MySQL schema bootstrap with deployment-only credentials.

The API deliberately connects with a least-privilege database account. MySQL
servers with binary logging enabled can require an administrative account to
create the immutable ledger triggers, so releases run this module once before
the runtime container is replaced. The root password is read from the
container environment and is never rendered into a URL or log output.
"""

from __future__ import annotations

import os

import pymysql

from app.mysql_bootstrap import ensure_mysql_schema


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is required for the deployment schema bootstrap")
    return value


def _mysql_admin_port() -> int:
    raw = os.getenv("FUND_AI_MYSQL_ADMIN_PORT", "3306").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("FUND_AI_MYSQL_ADMIN_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("FUND_AI_MYSQL_ADMIN_PORT must be between 1 and 65535")
    return port


def run_admin_bootstrap() -> None:
    """Create and verify the schema without exposing admin credentials to logs."""

    connection = pymysql.connect(
        host=os.getenv("FUND_AI_MYSQL_ADMIN_HOST", "mysql").strip() or "mysql",
        port=_mysql_admin_port(),
        user=os.getenv("FUND_AI_MYSQL_ADMIN_USER", "root").strip() or "root",
        password=_required_environment("MYSQL_ROOT_PASSWORD"),
        database=_required_environment("MYSQL_DATABASE"),
        charset="utf8mb4",
        connect_timeout=20,
        read_timeout=180,
        write_timeout=180,
        autocommit=False,
    )
    try:
        ensure_mysql_schema(connection)
    finally:
        connection.close()


def main() -> None:
    print("Running deployment MySQL schema bootstrap")
    run_admin_bootstrap()
    print("Deployment MySQL schema bootstrap succeeded")


if __name__ == "__main__":
    main()
