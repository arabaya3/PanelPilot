"""Tests for `app/design/integrity.py`."""

from __future__ import annotations

from decimal import Decimal

from app.ai.tools import cable_sizing
from app.design import integrity
from app.domain import design as design_domain
from app.models.schemas.calculations import ConductorMaterial, InstallationMethod
from app.models.schemas.design import (
    DesignProject,
    DeviceKind,
    FindingSeverity,
    InstallationConditions,
    ProjectDesignRequest,
)


def _designed() -> DesignProject:
    request = ProjectDesignRequest.model_validate(
        {
            "info": {"name": "Hall"},
            "boards": [
                {
                    "name": "DB1",
                    "loads": [
                        {"description": "Lights", "load": "lighting", "power_kw": "0.6"},
                        {"description": "Sockets", "load": "socket", "power_kw": "1.5"},
                        {
                            "description": "Pump",
                            "load": "motor",
                            "power_kw": "5.5",
                            "phases": 3,
                            "starter": "dol",
                        },
                    ],
                }
            ],
        }
    )
    designed, _ = design_domain.build_design(request)
    return designed


def _tampered(rating: str) -> DesignProject:
    project = _designed()
    board = project.boards[0]
    lights = next(c for c in board.circuits if c.description == "Lights")
    breaker = next(
        d
        for d in board.devices
        if d.id in lights.device_ids and d.kind is DeviceKind.CIRCUIT_BREAKER
    )
    breaker.rated_current_a = Decimal(rating)
    return project


def test_a_server_design_has_no_blocking_integrity_finding() -> None:
    found = integrity.integrity_findings(_designed())
    assert integrity.blocking(found) == []
    # The conditions the request states give the same verdict as the note's.
    given = {"DB1": InstallationConditions()}
    assert integrity.blocking(integrity.integrity_findings(_designed(), given)) == []


def test_a_tampered_200_a_breaker_on_its_cable_is_blocking() -> None:
    project = _tampered("200")
    (found,) = integrity.blocking(integrity.integrity_findings(project))
    assert found.code == "integrity_device_above_cable_capacity"
    assert found.severity is FindingSeverity.BLOCKING
    assert found.board_id == "DB1"
    assert found.circuit_id is not None
    assert found.device_id is not None
    assert found.values["rated"] == "200"


def test_without_conditions_the_largest_tabulated_capacity_bounds_the_check() -> None:
    project = _tampered("200")
    project.boards[0].notes = [n for n in project.boards[0].notes if n.code != "cable_conditions"]
    assert integrity.conditions_from_notes(project.boards[0]) is None
    (found,) = integrity.blocking(integrity.integrity_findings(project))
    assert found.values["basis"] == "largest tabulated"


def test_a_missing_device_reference_is_blocking() -> None:
    project = _designed()
    circuit = project.boards[0].circuits[0]
    # Bypass validation, as a stored or constructed model could.
    object.__setattr__(circuit, "device_ids", [*circuit.device_ids, "ghost"])
    codes = [f.code for f in integrity.blocking(integrity.integrity_findings(project))]
    assert "integrity_unknown_device" in codes


def test_notes_become_findings_on_their_circuit() -> None:
    found = integrity.note_findings(_designed())
    starter = next(f for f in found if f.code == "starter_table")
    assert starter.circuit_id is not None
    assert starter.severity is FindingSeverity.INFO
    assert any(f.code == "rules_unconfirmed" and f.requires_engineering_confirmation for f in found)


def test_capacity_matches_the_sizing_tables() -> None:
    capacity = cable_sizing.derated_capacity(
        cross_section_mm2=Decimal("1.5"),
        installation_method=InstallationMethod.B1,
        ambient_temp_c=Decimal(30),
        grouped_circuits=1,
        conductor_material=ConductorMaterial.COPPER,
        insulation_rating_c=70,
        three_phase=False,
    )
    assert capacity == Decimal("17.5")
    bound = cable_sizing.largest_tabulated_capacity(
        cross_section_mm2=Decimal("1.5"),
        conductor_material=ConductorMaterial.COPPER,
        insulation_rating_c=70,
    )
    assert bound is not None
    assert bound < 200
