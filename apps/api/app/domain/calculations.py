"""Engineering calculation service.

Owns everything around a calculation — validating inputs, choosing the right
standard, recording an audit trail — and delegates the arithmetic itself to the
pure functions in ``app.ai.tools``. Keeping the two apart means a formula can be
unit-tested against its manufacturer guide without a database.
"""

from __future__ import annotations

from decimal import Decimal

import structlog
from sqlalchemy.orm import Session

from app.ai.tools import cable_sizing, panel_bom, vfd_selection
from app.core.errors import ValidationError
from app.models.schemas.auth import CurrentUser
from app.models.schemas.calculations import (
    CableSizingRequest,
    CableSizingResponse,
    DriveRangeSummary,
    PanelBomRequest,
    PanelBomResponse,
    SupplyBand,
    VfdSelectionRequest,
    VfdSelectionResponse,
)
from app.models.schemas.search import Citation

logger = structlog.get_logger(__name__)


def _require_positive(name: str, value: Decimal) -> None:
    """Refuse anything but a finite number above zero."""
    if not value.is_finite() or value <= 0:
        raise ValidationError(f"{name} must be a positive number, got {value}")


def size_cable(
    *,
    session: Session,
    user: CurrentUser,
    request: CableSizingRequest,
) -> CableSizingResponse:
    """Size a feeder cable for the requested load and installation method.

    Args:
        session: Open database session. Unused: the result is not persisted.
        user: The authenticated caller, for the log line.
        request: Load current, length, voltage, installation method, and
            ambient conditions.

    Returns:
        The selected conductor size with derating factors, voltage drop, and
        the table each step came from.

    Raises:
        ValidationError: If the inputs fall outside the supported ranges of the
            underlying tables.
    """
    del session
    _require_positive("supply_voltage_v", request.supply_voltage_v)
    result = cable_sizing.size_conductor(
        design_current_a=request.design_current_a,
        installation_method=request.installation_method,
        ambient_temp_c=request.ambient_temp_c,
        grouped_circuits=request.grouped_circuits,
        conductor_material=request.conductor_material,
        insulation_rating_c=request.insulation_rating_c,
        three_phase=request.three_phase,
    )
    drop = cable_sizing.voltage_drop(
        current_a=request.design_current_a,
        length_m=request.length_m,
        cross_section_mm2=result.cross_section_mm2,
        conductor_material=request.conductor_material,
        power_factor=request.power_factor,
        three_phase=request.three_phase,
        installation_method=request.installation_method,
    )
    logger.info(
        "calculation.cable_sized",
        tenant_id=user.tenant_id,
        cross_section_mm2=str(result.cross_section_mm2),
    )
    return CableSizingResponse(
        result=result,
        voltage_drop_v=drop,
        voltage_drop_percent=drop / request.supply_voltage_v * 100,
        sources=[
            cable_sizing.ampacity_citation(request.installation_method),
            *(factor.source for factor in result.applied_factors),
            cable_sizing.voltage_drop_citation(request.conductor_material, request.power_factor),
        ],
    )


def select_vfd(
    *,
    session: Session,
    user: CurrentUser,
    request: VfdSelectionRequest,
) -> VfdSelectionResponse:
    """Select a variable frequency drive for a motor and duty profile.

    Args:
        session: Open database session. Unused: the result is not persisted.
        user: The authenticated caller, for the log line.
        request: Motor rating, supply voltage, duty class, and site.

    Returns:
        The recommended drive with its derated current, the factors applied,
        and every source.

    Raises:
        ValidationError: If an input is outside the tables, or no catalogue
            drive covers the duty.
    """
    del session
    required = vfd_selection.required_drive_current_a(
        motor_power_kw=request.motor_power_kw,
        supply_voltage_v=request.supply_voltage_v,
        motor_efficiency=request.motor_efficiency,
        motor_power_factor=request.motor_power_factor,
        duty_class=request.duty_class,
    )
    result = vfd_selection.select_frame(
        required_current_a=required,
        supply_voltage_v=request.supply_voltage_v,
        duty_class=request.duty_class,
        altitude_m=request.altitude_m,
        ambient_temp_c=request.ambient_temp_c,
        drive_range=request.drive_range,
    )
    logger.info("calculation.vfd_selected", tenant_id=user.tenant_id, drive=result.frame_reference)
    return VfdSelectionResponse(
        result=result,
        motor_current_a=required,
        sources=[
            vfd_selection.motor_current_citation(),
            vfd_selection.ratings_citation(request.supply_voltage_v, request.drive_range),
            *(factor.source for factor in result.applied_factors),
        ],
    )


def drive_ranges() -> list[DriveRangeSummary]:
    """List the drive series VFD selection can choose from.

    Returns:
        Each range with its supply bands and the manual it is read from,
        the default (ABB ACS880-01) first.
    """
    return [
        DriveRangeSummary(
            key=summary.key,
            manufacturer=summary.manufacturer,
            series=summary.series,
            bands=[SupplyBand(low_v=low, high_v=high) for low, high in summary.bands],
            heavy_duty=summary.heavy_duty,
            source=summary.source,
        )
        for summary in vfd_selection.available_ranges()
    ]


def build_panel_bom(
    *,
    session: Session,
    user: CurrentUser,
    request: PanelBomRequest,
) -> PanelBomResponse:
    """Produce a bill of materials for a control panel from its load schedule.

    Args:
        session: Open database session. Unused: the result is not persisted.
        user: The authenticated caller, for the log line.
        request: Load schedule and enclosure constraints.

    Returns:
        The sourced BOM lines, heat load and required cooling, with every
        document they came from.

    Raises:
        ValidationError: If the load schedule is inconsistent or a load falls
            outside the tables.
    """
    del session
    result = panel_bom.build_bom(loads=request.loads, constraints=request.constraints)
    logger.info("calculation.bom_built", tenant_id=user.tenant_id, lines=len(result.lines))
    sources: list[Citation] = []
    for line in result.lines:
        if line.source not in sources:
            sources.append(line.source)
    return PanelBomResponse(result=result, sources=sources)
