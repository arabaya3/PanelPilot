"""Find stored revisions that use one cable id and core on two boards (read-only).

Such a revision is valid under the board-qualified core rule
(docs/architecture/cable-core-identity.md) but is refused by readers older than
that rule. Operators run this before rolling back past that change, or before
running older readers against the database: if it finds any, rolling back would
make those revisions unreadable to the older code (refused, never misread).

It reads ``design_project_revisions`` only, changes nothing, and prints only
project ids, revision numbers and the cable/core it found, never content.

Usage::

    python -m app.design.cable_core_audit        # exit 0: none found; 1: found

Run with the same ``DATABASE_URL`` as the API.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import create_engine, text

#: Snapshot schemas that hold cable cores (a v1 snapshot has none).
_WITH_CORES = ("pp-design/2", "pp-design/3")


def shared_cores(snapshot: Any) -> list[tuple[str, int, list[str]]]:
    """The cable cores a stored snapshot uses on more than one board.

    Args:
        snapshot: A stored ``design_snapshot`` envelope.

    Returns:
        ``(cable_id, index, boards)`` for each cable id and core used on two
        or more boards, sorted; empty for a v1 snapshot or one with none.

    Raises:
        ValueError: If it cannot be read as a snapshot (never treated as "none found").
    """
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)  # a driver returning the JSON column as text
    if not isinstance(snapshot, Mapping):
        raise ValueError("not a snapshot envelope")
    if snapshot.get("schema") not in _WITH_CORES:
        return []
    model = snapshot.get("model")
    nets = model.get("nets") if isinstance(model, Mapping) else None
    connections = model.get("connections") if isinstance(model, Mapping) else None
    if not isinstance(nets, list) or not isinstance(connections, list):
        raise ValueError("a v2/v3 snapshot without nets and connections lists")
    board_of = {n.get("id"): n.get("board_id") for n in nets if isinstance(n, Mapping)}
    boards: dict[tuple[str, int], set[str]] = defaultdict(set)
    for connection in connections:
        core = connection.get("core") if isinstance(connection, Mapping) else None
        if not isinstance(core, Mapping):
            continue
        board = board_of.get(connection.get("net_id"))
        cable, index = core.get("cable_id"), core.get("index")
        if isinstance(board, str) and isinstance(cable, str) and isinstance(index, int):
            boards[(cable, index)].add(board)
    return sorted(
        (cable, index, sorted(names)) for (cable, index), names in boards.items() if len(names) > 1
    )


def audit(rows: Iterable[tuple[Any, int, Any]]) -> list[str]:
    """One line per affected revision core, and one per revision it could not read.

    ``<project> r<number>: <cable>#<core> on <boards>``, or
    ``<project> r<number>: UNREADABLE (<reason>)`` (counted as a finding, never as none).
    """
    found: list[str] = []
    for project_id, number, snapshot in rows:
        try:
            shared = shared_cores(snapshot)
        except (ValueError, TypeError, AttributeError) as exc:
            found.append(f"{project_id} r{number}: UNREADABLE ({type(exc).__name__}: {exc})")
            continue
        for cable, index, boards in shared:
            found.append(f"{project_id} r{number}: {cable}#{index} on {', '.join(boards)}")
    return found


def main() -> int:
    """Scan every stored revision; print what is found; 1 if any, else 0."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("cable_core_audit: DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT project_id, number, design_snapshot FROM design_project_revisions "
                "ORDER BY project_id, number"
            )
        )
        found = audit((r[0], r[1], r[2]) for r in rows)
    for line in found:
        print(line)
    print(
        f"cable_core_audit: {len(found)} finding(s) (one cable id and core on two boards, or a"
        " revision that could not be checked)"
        + ("; do not roll back past cable-core-identity until each is understood" if found else "")
    )
    return 1 if found else 0


if __name__ == "__main__":  # pragma: no cover - a command
    sys.exit(main())
