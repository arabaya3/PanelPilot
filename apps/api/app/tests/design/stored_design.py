"""The v1 design inside a stored snapshot, whichever schema it was written in.

Tests that alter a stored row edit the design through :func:`design_of`, so
they alter the same design whether the row is a bare ``pp-design/1`` snapshot
or a ``pp-design/2`` envelope (``model.design``), as ``DESIGN_SNAPSHOT_SCHEMA``
decided when it was saved. Nothing here re-hashes or repairs anything: an
edit made through it must still be caught by the hash, and for v2 also by the
reader's own invariants.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any


def is_v2(stored: dict[str, Any]) -> bool:
    """Whether a stored snapshot is a ``pp-design/2`` envelope."""
    return stored.get("schema") == "pp-design/2"


def design_of(stored: dict[str, Any]) -> dict[str, Any]:
    """The v1 design dict a stored snapshot holds, itself (edits change ``stored``).

    Args:
        stored: A bare v1 snapshot or a v2 envelope, as read from the row.

    Returns:
        ``stored`` for v1; ``stored["model"]["design"]`` for v2.
    """
    if is_v2(stored):
        design: dict[str, Any] = stored["model"]["design"]
        return design
    return stored


def edited(stored: dict[str, Any], change: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """A deep copy of ``stored`` with ``change`` applied to its v1 design.

    ``change`` may edit the design in place or return a new one; a returned
    dict replaces the design inside the copy.
    """
    out = copy.deepcopy(stored)
    design = design_of(out)
    result = change(design)
    if isinstance(result, dict) and result is not design:
        if is_v2(out):
            out["model"]["design"] = result
        else:
            out = result
    return out
