from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

TEST_DATABASE_ENV = "PYBOT_TEST_DATABASE_URL"


def validate_test_database_url(database_url: str) -> str:
    """Validate that an external test URL cannot target a normal database."""
    try:
        url = make_url(database_url)
    except ArgumentError as err:
        raise ValueError(f"{TEST_DATABASE_ENV} must be a valid database URL.") from err
    if url.drivername != "postgresql+asyncpg":
        raise ValueError(f"{TEST_DATABASE_ENV} must use the postgresql+asyncpg driver.")
    trusted_hosts = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "test-postgres"}  # noqa: S104
    host = url.host or ""
    if host not in trusted_hosts:
        raise ValueError(f"{TEST_DATABASE_ENV} host '{host}' is not a trusted test host.")

    database_name = url.database or ""
    if database_name != "test" and not database_name.endswith("_test"):
        raise ValueError(f"{TEST_DATABASE_ENV} must target database 'test' or a name ending in '_test'.")
    return database_url
