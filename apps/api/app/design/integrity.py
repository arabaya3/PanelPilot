"""Findings for a designed project: its notes, classified, and integrity checks.

A project reaching an export may not be the one the design made: the export
of a draft takes whatever project the client sends. So before any export the
project is checked again here, with the same sourced sizing tables the design
used (``app.ai.tools.cable_sizing``), and what fails is a finding, not a
silent pass:

* every device and cable a circuit names exists on its board;
* the device protecting each circuit's cable against overload is not rated
  above the cable's current-carrying capacity, ``In <= Iz`` (ABB handbook
  Vol. 2 §2.3; Iz from §2.2.1 Tables 4, 5 and 8). For a coordinated motor
  starter the overload relay protects the cable and its breaker trips on
  short circuit only, so the relay's setting is what is checked.

Iz is read under the board's installation conditions where they are known:
from the request for a saved revision, or from the board's own
``cable_conditions`` note for a draft. Where they are not, the check uses the
most any tabulated installation gives the section, so a rating above it is
above the capacity however the cable is laid, and nothing that might hold is
blocked.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from app.ai.tools import cable_sizing
from app.core.errors import ValidationError
from app.design import findings_policy
from app.models.schemas.calculations import ConductorMaterial, InstallationMethod
from app.models.schemas.design import (
    Board,
    Cable,
    Circuit,
    DesignNote,
    DesignProject,
    Device,
    DeviceKind,
    FindingSeverity,
    InstallationConditions,
    MotorStarter,
    Phase,
    ValidationFinding,
)

_CAPACITY_SOURCE = (
    "ABB Electrical installation handbook Vol. 2, §2.3 (In <= Iz); Iz from §2.2.1 Tables 4, 5, 8"
)
_REFERENCE_SOURCE = "project model: every reference resolves"

#: Arithmetic slack when comparing a rating with a capacity computed from the
#: tables' factors: the design sizes by division, this checks by
#: multiplication, and the two may differ in the last of 28 digits.
_SLACK = Decimal("1e-9")

_PROTECTIVE = (DeviceKind.CIRCUIT_BREAKER, DeviceKind.FUSE)
_STARTERS = (MotorStarter.DIRECT_ON_LINE, MotorStarter.STAR_DELTA)


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _finding(
    code: str,
    severity: FindingSeverity,
    message: str,
    *,
    source: str,
    board: Board,
    circuit: Circuit | None = None,
    device: Device | None = None,
    values: Mapping[str, object] | None = None,
    confirm: bool = False,
) -> ValidationFinding:
    return ValidationFinding(
        code=code,
        severity=severity,
        board_id=board.id,
        circuit_id=circuit.id if circuit else None,
        device_id=device.id if device else None,
        values={name: str(value) for name, value in (values or {}).items()},
        message=message,
        requires_engineering_confirmation=confirm,
        source=source,
    )


def note_findings(project: DesignProject) -> list[ValidationFinding]:
    """Each board note as a finding, its severity from ``findings_policy``.

    A note about a load (its ``load`` param) is placed on the circuit of
    that description on its board, so the page can say where to fix it.

    Args:
        project: The designed project.

    Returns:
        One finding per note, in board and note order.
    """
    found: list[ValidationFinding] = []
    for board in project.boards:
        by_description = {circuit.description: circuit for circuit in board.circuits}
        for item in board.notes:
            found.append(_from_note(board, item, by_description))
    return found


def _from_note(
    board: Board, item: DesignNote, by_description: Mapping[str, Circuit]
) -> ValidationFinding:
    rule = findings_policy.classify(item.code)
    circuit = by_description.get(item.params.get("load", ""))
    return ValidationFinding(
        code=item.code,
        severity=rule.severity,
        board_id=board.id,
        circuit_id=circuit.id if circuit else None,
        values=dict(item.params),
        message=item.text,
        requires_engineering_confirmation=rule.requires_engineering_confirmation,
        source=rule.source,
    )


def conditions_from_notes(board: Board) -> InstallationConditions | None:
    """A board's installation conditions as its ``cable_conditions`` note states them.

    The conductor material is not in the note; each cable states its own.

    Args:
        board: A designed board.

    Returns:
        The conditions, or ``None`` where the note is missing or unreadable.
    """
    for item in board.notes:
        if item.code != "cable_conditions":
            continue
        try:
            return InstallationConditions(
                installation_method=InstallationMethod(item.params["method"]),
                ambient_temp_c=Decimal(item.params["ambient"]),
                grouped_circuits=int(item.params["grouped"]),
                insulation_rating_c=int(item.params["insulation"]),
            )
        except (KeyError, ValueError, ArithmeticError):
            return None
    return None


def _material(cable: Cable) -> ConductorMaterial | None:
    return {"cu": ConductorMaterial.COPPER, "al": ConductorMaterial.ALUMINIUM}.get(
        cable.material.strip().lower()
    )


def _insulation(cable: Cable) -> int | None:
    return {"pvc": 70, "xlpe": 90, "epr": 90}.get(cable.insulation.strip().lower())


def _capacity(
    cable: Cable, three_phase: bool, conditions: InstallationConditions | None
) -> tuple[Decimal, str] | None:
    """The cable's Iz (all its parallel runs) and how it was read; ``None`` if untabulated."""
    material = _material(cable)
    insulation = _insulation(cable)
    if material is None or insulation is None:
        return None
    if conditions is not None:
        try:
            each = cable_sizing.derated_capacity(
                cross_section_mm2=cable.cross_section_mm2,
                installation_method=conditions.installation_method,
                ambient_temp_c=conditions.ambient_temp_c,
                # Each parallel run counts as a circuit in its group (Table 5 note 4).
                grouped_circuits=conditions.grouped_circuits + cable.parallel - 1,
                conductor_material=material,
                insulation_rating_c=insulation,
                three_phase=three_phase,
            )
        except ValidationError:
            each = None
        if each is not None:
            return each * cable.parallel, f"method {conditions.installation_method.value}"
    bound = cable_sizing.largest_tabulated_capacity(
        cross_section_mm2=cable.cross_section_mm2,
        conductor_material=material,
        insulation_rating_c=insulation,
    )
    if bound is None:
        return None
    return bound * cable.parallel, "largest tabulated"


def _protecting(circuit: Circuit, devices: Mapping[str, Device]) -> list[Device]:
    """The devices that protect the circuit's cable against overload."""
    own = [devices[ref] for ref in circuit.device_ids if ref in devices]
    if circuit.starter in _STARTERS:
        return [device for device in own if device.kind is DeviceKind.OVERLOAD_RELAY]
    return [device for device in own if device.kind in _PROTECTIVE]


def integrity_findings(
    project: DesignProject, conditions: Mapping[str, InstallationConditions] | None = None
) -> list[ValidationFinding]:
    """Re-check a project's references and its protection against its cables.

    Args:
        project: The project as it is to be exported.
        conditions: Each board's installation conditions by board id, where
            known (a saved revision's request); otherwise each board's
            ``cable_conditions`` note is read, and failing that the largest
            tabulated capacity bounds the check.

    Returns:
        The findings, in board and circuit order.
    """
    found: list[ValidationFinding] = []
    for board in project.boards:
        given = (conditions or {}).get(board.id)
        found.extend(_board_findings(board, given or conditions_from_notes(board)))
    return found


def _board_findings(
    board: Board, conditions: InstallationConditions | None
) -> list[ValidationFinding]:
    found: list[ValidationFinding] = []
    devices = {device.id: device for device in board.devices}
    cables = {cable.id: cable for cable in board.cables}
    for circuit in board.circuits:
        for ref in [*circuit.device_ids, circuit.upstream_id]:
            if ref is not None and ref not in devices:
                found.append(
                    _finding(
                        "integrity_unknown_device",
                        FindingSeverity.BLOCKING,
                        f"{circuit.description}: device {ref} is not on board {board.name}.",
                        source=_REFERENCE_SOURCE,
                        board=board,
                        circuit=circuit,
                        values={"device": ref},
                    )
                )
        if circuit.cable_id is None:
            continue
        cable = cables.get(circuit.cable_id)
        if cable is None:
            found.append(
                _finding(
                    "integrity_unknown_cable",
                    FindingSeverity.BLOCKING,
                    f"{circuit.description}: cable {circuit.cable_id} is not on board "
                    f"{board.name}.",
                    source=_REFERENCE_SOURCE,
                    board=board,
                    circuit=circuit,
                    values={"cable": circuit.cable_id},
                )
            )
            continue
        found.extend(_capacity_findings(board, circuit, cable, devices, conditions))
    return found


def _capacity_findings(
    board: Board,
    circuit: Circuit,
    cable: Cable,
    devices: Mapping[str, Device],
    conditions: InstallationConditions | None,
) -> list[ValidationFinding]:
    if circuit.starter is MotorStarter.DRIVE:
        # The drive's own motor overload protects its output cable; its input
        # fuses protect the drive, and are not the cable's overload device.
        return [
            _finding(
                "integrity_drive_cable_unchecked",
                FindingSeverity.INFO,
                f"{circuit.description}: the cable after a drive is protected by the drive's "
                "motor overload setting, which is not checked here.",
                source="policy",
                board=board,
                circuit=circuit,
            )
        ]
    protecting = _protecting(circuit, devices)
    rated = [device for device in protecting if device.rated_current_a is not None]
    if not rated:
        return [
            _finding(
                "integrity_protection_unchecked",
                FindingSeverity.WARNING,
                f"{circuit.description}: no rated overload protective device is found for its "
                "cable, so the cable is not checked against it.",
                source="policy",
                board=board,
                circuit=circuit,
                confirm=True,
            )
        ]
    three_phase = circuit.phase is Phase.THREE_PHASE or circuit.starter is not None
    capacity = _capacity(cable, three_phase, conditions)
    if capacity is None:
        return [
            _finding(
                "integrity_cable_untabulated",
                FindingSeverity.WARNING,
                f"{circuit.description}: {_plain(cable.cross_section_mm2)} mm² "
                f"{cable.material} {cable.insulation} is not in the capacity tables, so the "
                "protection is not checked against it.",
                source=_CAPACITY_SOURCE,
                board=board,
                circuit=circuit,
                values={"section": _plain(cable.cross_section_mm2)},
                confirm=True,
            )
        ]
    iz, basis = capacity
    found: list[ValidationFinding] = []
    for device in rated:
        assert device.rated_current_a is not None
        if device.rated_current_a > iz + _SLACK:
            found.append(
                _finding(
                    "integrity_device_above_cable_capacity",
                    FindingSeverity.BLOCKING,
                    f"{circuit.description}: {_plain(device.rated_current_a)} A protection is "
                    f"above the {iz.quantize(Decimal('0.1'))} A its "
                    f"{cable.size} cable carries ({basis}); In <= Iz does not hold.",
                    source=_CAPACITY_SOURCE,
                    board=board,
                    circuit=circuit,
                    device=device,
                    values={
                        "rated": _plain(device.rated_current_a),
                        "capacity": str(iz.quantize(Decimal("0.1"))),
                        "cable": cable.size,
                        "basis": basis,
                    },
                )
            )
    return found


def project_findings(
    project: DesignProject, conditions: Mapping[str, InstallationConditions] | None = None
) -> list[ValidationFinding]:
    """Every finding for a project: its integrity checks, then its notes.

    Args:
        project: The designed project.
        conditions: As :func:`integrity_findings` takes.

    Returns:
        The findings.
    """
    return [*integrity_findings(project, conditions), *note_findings(project)]


def blocking(findings: list[ValidationFinding]) -> list[ValidationFinding]:
    """The blocking findings among some.

    Args:
        findings: Any findings.

    Returns:
        Those whose severity is blocking, in order.
    """
    return [found for found in findings if found.severity is FindingSeverity.BLOCKING]
