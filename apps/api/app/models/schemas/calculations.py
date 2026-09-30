"""Calculation request, result, and shared-unit schemas.

Every physical quantity carries its unit in the field name (``_a``, ``_v``,
``_kw``, ``_mm2``, ``_m``, ``_c``) and uses ``Decimal`` rather than ``float``,
because these numbers end up on drawings.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from app.models.schemas.search import Citation


class ConductorMaterial(StrEnum):
    """Conductor metal."""

    COPPER = "copper"
    ALUMINIUM = "aluminium"


class InstallationMethod(StrEnum):
    """IEC 60364-5-52 reference installation method."""

    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C = "C"
    D1 = "D1"
    D2 = "D2"
    E = "E"
    F = "F"
    G = "G"


class DutyClass(StrEnum):
    """Drive duty rating."""

    NORMAL = "normal"
    HEAVY = "heavy"


class AppliedFactor(BaseModel):
    """A derating or correction factor, with the clause it came from."""

    name: str
    value: Decimal
    source: Citation


# --- Cable sizing ----------------------------------------------------------
class CableSizingRequest(BaseModel):
    """Inputs for a feeder cable sizing."""

    design_current_a: Decimal
    length_m: Decimal
    supply_voltage_v: Decimal
    installation_method: InstallationMethod
    ambient_temp_c: Decimal
    grouped_circuits: int = 1
    conductor_material: ConductorMaterial = ConductorMaterial.COPPER
    insulation_rating_c: int = 90
    # 0.8: the normal-service motor column of the voltage-drop table, which
    # tabulates 0.8 and 0.35 only and refuses anything else.
    power_factor: Decimal = Decimal("0.8")
    three_phase: bool = True


class CableSizingResult(BaseModel):
    """Output of the pure sizing function."""

    cross_section_mm2: Decimal
    derated_ampacity_a: Decimal
    applied_factors: list[AppliedFactor]


class CableSizingResponse(BaseModel):
    """Cable sizing as returned to the caller."""

    result: CableSizingResult
    voltage_drop_v: Decimal
    voltage_drop_percent: Decimal
    sources: list[Citation]


# --- VFD selection ---------------------------------------------------------
class VfdSelectionRequest(BaseModel):
    """Inputs for a drive selection."""

    motor_power_kw: Decimal
    supply_voltage_v: Decimal
    motor_efficiency: Decimal
    motor_power_factor: Decimal
    duty_class: DutyClass = DutyClass.NORMAL
    altitude_m: Decimal = Decimal(0)
    ambient_temp_c: Decimal = Decimal(40)


class VfdSelectionResult(BaseModel):
    """Output of the pure selection function."""

    frame_reference: str
    rated_output_current_a: Decimal
    applied_factors: list[AppliedFactor]


class VfdSelectionResponse(BaseModel):
    """Drive selection as returned to the caller."""

    result: VfdSelectionResult
    #: The motor current the drive was sized for.
    motor_current_a: Decimal
    sources: list[Citation]


# --- Panel BOM -------------------------------------------------------------
class LoadScheduleItem(BaseModel):
    """One load on a panel's schedule."""

    tag: str
    description: str
    power_kw: Decimal | None = None
    #: Nameplate current; a load with none gets no cable or drive.
    current_a: Decimal | None = None
    #: Heat this load's equipment gives off inside the enclosure.
    dissipation_w: Decimal | None = None
    #: Fed through a variable speed drive, which the BOM then selects.
    variable_speed: bool = False


class EnclosurePlacement(StrEnum):
    """How an enclosure stands, per IEC 60890's effective-area formulas."""

    SINGLE_FREE_STANDING = "single_free_standing"
    SINGLE_WALL = "single_wall"
    SUITE_END_FREE_STANDING = "suite_end_free_standing"
    SUITE_END_WALL = "suite_end_wall"
    SUITE_MIDDLE_FREE_STANDING = "suite_middle_free_standing"
    SUITE_MIDDLE_WALL = "suite_middle_wall"
    SUITE_MIDDLE_WALL_COVERED_ROOF = "suite_middle_wall_covered_roof"


class EnclosureConstraints(BaseModel):
    """Physical and vendor constraints on the enclosure."""

    width_mm: int
    height_mm: int
    depth_mm: int
    ingress_rating: str
    #: Required: the effective surface area, and so the cooling, depends on it.
    placement: EnclosurePlacement
    #: Required: the outgoing cables' capacity depends on it.
    cable_installation_method: InstallationMethod
    supply_voltage_v: Decimal = Decimal(400)
    preferred_vendors: list[str] = []
    ambient_temp_c: Decimal = Decimal(35)
    max_internal_temp_c: Decimal = Decimal(50)


class BomLineKind(StrEnum):
    """What a BOM line is, so a page can describe it in its own language."""

    DRIVE = "drive"
    CABLE = "cable"
    ENCLOSURE = "enclosure"
    COOLING = "cooling"


class BomNote(StrEnum):
    """What a BOM leaves out, as a key a page can translate."""

    NO_PROTECTIVE_DEVICES = "no_protective_devices"
    INCOMPLETE_DISSIPATION = "incomplete_dissipation"


class BomLine(BaseModel):
    """One line of a bill of materials."""

    part_reference: str
    #: English, for anything that shows the line without translating it.
    description: str
    quantity: int
    source: Citation
    kind: BomLineKind
    #: The values ``description`` is written from, for translation: ``tag``,
    #: ``load``, ``method``, ``grouped``, ``placement``, ``cooling_w``,
    #: ``rise_k`` and ``qw`` as each kind has them.
    details: dict[str, str] = {}


class PanelBomResult(BaseModel):
    """Output of the pure BOM function."""

    lines: list[BomLine]
    #: Total heat given off inside the enclosure.
    heat_load_w: Decimal
    #: Heat the enclosure surface cannot shed at the permitted temperature
    #: rise, which cooling must remove. Zero if the surface suffices.
    cooling_required_w: Decimal
    #: What the BOM deliberately leaves out, and why, in English.
    notes: list[str] = []
    #: The same, as keys a page translates.
    note_keys: list[BomNote] = []


class PanelBomRequest(BaseModel):
    """Inputs for BOM generation."""

    loads: list[LoadScheduleItem]
    constraints: EnclosureConstraints


class PanelBomResponse(BaseModel):
    """BOM as returned to the caller."""

    result: PanelBomResult
    sources: list[Citation]
