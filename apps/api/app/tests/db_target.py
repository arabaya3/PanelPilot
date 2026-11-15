"""Which database a destructive test may touch.

Database-backed tests create and drop tables, and the migration tests drop and
create whole databases. A wrong target is not hypothetical: on 2026-10-28 a
helper command chose a Postgres container by filter and order, reached another
project's server, and was stopped only because that server had no
``panelpilot`` role. The rules below are therefore strict and checked before
anything is created or dropped:

* the target is named explicitly in ``TEST_DATABASE_URL``; nothing is guessed,
  filtered or picked by position, and there is no fallback to ``DATABASE_URL``;
* the database name must be a disposable test name, ``panelpilot_phase1_*``,
  ``panelpilot_ci_*`` or ``panelpilot_test_*``;
* the dev database ``panelpilot`` and the server's own databases are refused
  by name, whatever else is configured, CI included.
"""

from __future__ import annotations

import re

from sqlalchemy.engine import URL, make_url

#: A disposable test database. Lower case only, as Postgres folds unquoted names.
DISPOSABLE = re.compile(r"^panelpilot_(?:phase1|ci|test)_[a-z0-9_]{1,40}$")

#: Refused by name even if a pattern above were widened by mistake.
REFUSED = frozenset({"panelpilot", "postgres", "template0", "template1"})

#: Longest name Postgres keeps without truncating it.
_MAX_NAME = 63


class UnsafeDatabaseTargetError(RuntimeError):
    """The configured database is not one a destructive test may use."""


def check_name(name: str | None) -> str:
    """Return ``name`` if it is a disposable test database, else raise.

    Args:
        name: A database name, the main one or one derived from it.

    Returns:
        The same name.

    Raises:
        UnsafeDatabaseTargetError: It is missing, reserved, too long or not a
            disposable test name.
    """
    if not name:
        raise UnsafeDatabaseTargetError("no database is named; name the disposable test database")
    if name in REFUSED:
        raise UnsafeDatabaseTargetError(
            f"refusing the database {name!r}: it is not a test database"
        )
    if len(name) > _MAX_NAME or not DISPOSABLE.fullmatch(name):
        raise UnsafeDatabaseTargetError(
            f"refusing the database {name!r}: only panelpilot_phase1_*, panelpilot_ci_* "
            "or panelpilot_test_* may be used by destructive tests"
        )
    return name


def require_disposable(url: str | None) -> URL:
    """Parse ``url`` and accept it only if it names a disposable test database.

    Args:
        url: The value of ``TEST_DATABASE_URL``, exactly as configured.

    Returns:
        The parsed URL.

    Raises:
        UnsafeDatabaseTargetError: The URL is missing, not Postgres, or names any
            other database.
    """
    if not url:
        raise UnsafeDatabaseTargetError(
            "TEST_DATABASE_URL is not set; name the disposable database"
        )
    try:
        parsed = make_url(url)
    except Exception as exc:
        raise UnsafeDatabaseTargetError("TEST_DATABASE_URL is not a database URL") from exc
    if not parsed.drivername.startswith("postgresql"):
        raise UnsafeDatabaseTargetError("TEST_DATABASE_URL must be a Postgres URL")
    check_name(parsed.database)
    return parsed


def scratch_url(url: str | None, suffix: str) -> URL:
    """The URL of a scratch database derived from the disposable one.

    Both the configured database and the derived name are checked, so a
    migration test can never drop a database it was not meant to.

    Args:
        url: The value of ``TEST_DATABASE_URL``.
        suffix: Appended after an underscore; lower-case letters, digits and
            underscores.

    Returns:
        The scratch database's URL.
    """
    base = require_disposable(url)
    return base.set(database=check_name(f"{base.database}_{suffix}"))
