"""Engine configuration: how long the database is allowed to hold a request."""

from __future__ import annotations

from app.core import db


def test_postgres_connections_are_bounded() -> None:
    """A hung connect or a runaway statement must not hold a request forever."""
    args = db._connect_args("postgresql+psycopg://u:p@h/d")
    assert args["connect_timeout"] == db.CONNECT_TIMEOUT_S
    assert f"statement_timeout={db.STATEMENT_TIMEOUT_MS}" in str(args["options"])


def test_other_drivers_get_no_postgres_options() -> None:
    """SQLite and friends reject libpq options outright."""
    assert db._connect_args("sqlite://") == {}


def test_waiting_for_the_pool_is_bounded_below_the_default() -> None:
    """The default wait is 30 s — an exhausted pool stalled every request that long."""
    assert db.POOL_TIMEOUT_S < 30
