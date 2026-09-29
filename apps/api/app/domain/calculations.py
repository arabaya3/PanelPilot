"""Engineering calculation service.

Owns everything around a calculation — validating inputs, choosing the right
standard, recording an audit trail — and delegates the arithmetic itself to the
pure functions in ``app.ai.tools``. Keeping the two apart means a formula can be
unit-tested against its manufacturer guide without a database.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import CapabilityUnavailableError
from app.models.schemas.auth import CurrentUser
from app.models.schemas.calculations import (
    CableSizingRequest,
    CableSizingResponse,
    PanelBomRequest,
    PanelBomResponse,
    VfdSelectionRequest,
    VfdSelectionResponse,
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
            underlying tables.        CapabilityUnavailableError: Always, until the underlying tool is
            sourced and verified; see the module docstring.
    """
    # The signature is the contract the implementation will keep; nothing in
    # it is read while the capability refuses.
    del session, user, request
    raise CapabilityUnavailableError(
        "cable sizing is not available yet: conductor selection and derating are "
        "blocked on a manufacturer guide with enough published worked examples to "
        "verify them against (AI-005). Voltage drop alone is implemented."
    )


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
        ValidationError: If no catalogue frame covers the requested duty.        CapabilityUnavailableError: Always, until the underlying tool is
            sourced and verified; see the module docstring.
    """
    # The signature is the contract the implementation will keep; nothing in
    # it is read while the capability refuses.
    del session, user, request
    raise CapabilityUnavailableError(
        "VFD selection is not available yet: it is blocked on published worked "
        "drive-selection examples to verify against (AI-006)."
    )


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
        ValidationError: If the load schedule is internally inconsistent.        CapabilityUnavailableError: Always, until the underlying tool is
            sourced and verified; see the module docstring.
    """
    # The signature is the contract the implementation will keep; nothing in
    # it is read while the capability refuses.
    del session, user, request
    raise CapabilityUnavailableError(
        "panel BOM generation is not available yet: it depends on the cable, VFD "
        "and panel sizing tools, which are blocked on source documents (AI-007, BE-011)."
    )
