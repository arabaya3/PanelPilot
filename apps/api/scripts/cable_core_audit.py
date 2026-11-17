"""Read-only: stored revisions using one cable id and core on two boards.

Operator tool, like ``legacy-approval-counts.sql`` (it spans every tenant, so it
is kept out of the application). Run before rolling back past
docs/architecture/cable-core-identity.md, or before running older readers
against the database::

    cd apps/api && DATABASE_URL=... python scripts/cable_core_audit.py
    # exit 0: none; 1: found, or a revision could not be checked; 2: no DATABASE_URL

It runs inside a READ ONLY transaction, so it cannot change data, and prints only
project ids, revision numbers and the cable/core found, never content
(the scan is :mod:`app.design.cable_core_audit`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/api, for `app`

from sqlalchemy import create_engine, text

from app.design.cable_core_audit import audit


def scan(database_url: str) -> list[str]:
    """Every finding over all stored revisions, in a read-only transaction."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            rows = conn.execute(
                text(
                    "SELECT project_id, number, design_snapshot FROM design_project_revisions "
                    "ORDER BY project_id, number"
                )
            )
            return audit((r[0], r[1], r[2]) for r in rows)
    finally:
        engine.dispose()


def main() -> int:
    """Print the findings; exit 1 if any, 0 if none, 2 without ``DATABASE_URL``."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("cable_core_audit: DATABASE_URL is not set", file=sys.stderr)
        return 2
    found = scan(url)
    for line in found:
        print(line)
    print(
        f"cable_core_audit: {len(found)} finding(s) (one cable id and core on two boards, or a"
        " revision that could not be checked)"
        + ("; do not roll back past cable-core-identity until each is understood" if found else "")
    )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
