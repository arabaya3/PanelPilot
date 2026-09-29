"""Engineering calculation service.

Owns everything around a calculation — validating inputs, choosing the right
standard, recording an audit trail — and delegates the arithmetic itself to the
pure functions in ``app.ai.tools``. Keeping the two apart means a formula can be
unit-tested against its manufacturer guide without a database.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NotImplementedYetError
from app.models.schemas.auth import CurrentUser
from app.models.schemas.calculations import (
    CableSizingRequest,
    CableSizingResponse,
    PanelBomRequest,
    PanelBomResponse,
    VfdSelectionRequest,
    VfdSelectionResponse,
)

# Why every calculation answers 501 today. The formulas and tables come from
# named manufacturer engineering guides that are not in this repository, and
# a table written from general knowledge would be confident and uncitable —
# the failure cite-or-refuse exists to prevent. See the README, "Blocked on
# source documents that are not in this repository".
_BLOCKED_ON_SOURCES = (
    "{tool} is not available yet: it is blocked on the manufacturer engineering "
    "guides its tables must be cited from"
)


def size_cable(
    *,
    session: Session,
    user: CurrentUser,
    request: CableSizingRequest,
) -> CableSizingResponse:
    """Size a feeder cable for the requested load and installation method.

    Args:
        session: Open database session, used to persist the calculation record.
        user: The authenticated caller.
        request: Load current, length, voltage, installation method, and
            ambient conditions.

    Returns:
        The selected conductor size with derating factors, voltage drop, and
        the standard clause each step came from.

    Raises:
        ValidationError: If the inputs fall outside the supported ranges of the
            underlying tables.
        NotImplementedYetError: Always, until the source guide is supplied.
    """
    del session, user, request  # Unused until the tool exists; the signature is the contract.
    raise NotImplementedYetError(_BLOCKED_ON_SOURCES.format(tool="Cable sizing"))


def select_vfd(
    *,
    session: Session,
    user: CurrentUser,
    request: VfdSelectionRequest,
) -> VfdSelectionResponse:
    """Select a variable frequency drive frame for a motor and duty profile.

    Args:
        session: Open database session, used to persist the calculation record.
        user: The authenticated caller.
        request: Motor rating, supply voltage, duty class, and altitude.

    Returns:
        The recommended drive rating with applied derates and cited sources.

    Raises:
        ValidationError: If no catalogue frame covers the requested duty.
        NotImplementedYetError: Always, until the source guide is supplied.
    """
    del session, user, request  # Unused until the tool exists; the signature is the contract.
    raise NotImplementedYetError(_BLOCKED_ON_SOURCES.format(tool="VFD selection"))


def build_panel_bom(
    *,
    session: Session,
    user: CurrentUser,
    request: PanelBomRequest,
) -> PanelBomResponse:
    """Produce a bill of materials for a control panel from its load schedule.

    Args:
        session: Open database session, used to persist the generated BOM.
        user: The authenticated caller.
        request: Load schedule, enclosure constraints, and preferred vendors.

    Returns:
        The itemised BOM with quantities, part references, and heat load.

    Raises:
        ValidationError: If the load schedule is internally inconsistent.
        NotImplementedYetError: Always, until the calc tools it consumes exist.
    """
    del session, user, request  # Unused until the tool exists; the signature is the contract.
    raise NotImplementedYetError(_BLOCKED_ON_SOURCES.format(tool="Panel BOM generation"))
