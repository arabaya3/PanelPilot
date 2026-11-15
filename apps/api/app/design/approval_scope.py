"""What a review approval covers, and what it does not (M6d).

Printed on every review export and shown on the approval panel; the approver
ticks that they read it (``scope_confirmed``), and the approval records the
version of the statement confirmed (``approved_scope_version``). The
statement is printed on every review export, so changing it bumps the
renderer version, which stops every earlier approval counting.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

#: Bumped whenever the statement below changes what it says.
APPROVAL_SCOPE_VERSION = "2026.11.04.1"

COVERS = "A review approval is a review of this revision's study under PanelPilot's default rules."

#: What a review approval never covers, in the order printed.
NOT_COVERED: tuple[str, ...] = (
    "control circuits",
    "board-to-board wiring",
    "items listed as open or excluded from scope",
    "life-safety systems",
    "standards compliance",
    "electrical accuracy",
    "execution",
)

NOT_VALIDATION = "It is not engineer validation."


def statement_lines() -> list[str]:
    """The scope statement as printed lines (every review export)."""
    return [
        f"[SCOPE] {COVERS}",
        "[SCOPE] It does not cover: "
        + ", ".join(NOT_COVERED[:-1])
        + f" or {NOT_COVERED[-1]}. {NOT_VALIDATION}",
    ]


def confirmed_line(version: str | None) -> str:
    """The line an approved export prints: the approver confirmed this scope."""
    return f"[SCOPE] The approver confirmed this scope (scope statement {version or 'none'})."


def block(
    *,
    approved: bool,
    scope_version: str | None,
    excluded: Sequence[str],
    false_positives: Sequence[str],
) -> dict[str, Any]:
    """The JSON export's ``approval`` block: what the approval covers and does not."""
    return {
        "approved": approved,
        "scope_version": APPROVAL_SCOPE_VERSION,
        "scope_confirmed": approved and scope_version == APPROVAL_SCOPE_VERSION,
        "covers": COVERS,
        "does_not_cover": list(NOT_COVERED),
        "engineer_validated": False,
        "excluded_from_scope": list(excluded),
        "false_positives": list(false_positives),
    }
