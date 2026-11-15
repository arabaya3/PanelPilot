"""Tests for `app/design/integrity.py`."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.ai.tools import cable_sizing
from app.design import integrity
from app.domain import design as design_domain
from app.models.schemas.calculations import ConductorMaterial, InstallationMethod
from app.models.schemas.design import (
    Cable,
    Circuit,
    DesignFinding,
    DesignProject,
    Device,
    DeviceKind,
    FindingSeverity,
    InstallationConditions,
    LoadInput,
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


# --- independent review: what the re-check left out ------------------------


def _circuit(project: DesignProject, description: str) -> Circuit:
    return next(c for c in project.boards[0].circuits if c.description == description)


def _codes(project: DesignProject) -> dict[str, DesignFinding]:
    return {f.code: f for f in integrity.integrity_findings(project)}


def _breaker_of(project: DesignProject, description: str) -> Device:
    circuit = _circuit(project, description)
    return next(
        d
        for d in project.boards[0].devices
        if d.id in circuit.device_ids and d.kind is DeviceKind.CIRCUIT_BREAKER
    )


def test_a_load_circuit_without_a_cable_is_blocking() -> None:
    project = _designed()
    lights = _circuit(project, "Lights")
    lights.cable_id = None
    found = _codes(project)["integrity_cable_missing"]
    assert found.severity is FindingSeverity.BLOCKING
    assert found.circuit_id == lights.id


def test_a_spare_way_without_a_cable_is_not_a_finding() -> None:
    project = _designed()
    lights = _circuit(project, "Lights")
    lights.cable_id = None
    lights.power_kw = Decimal(0)
    assert "integrity_cable_missing" not in _codes(project)


def test_a_starter_on_a_circuit_that_is_not_a_motor_is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="starter"):
        Circuit.model_validate(
            {
                "id": "c1",
                "description": "Lights",
                "load": "lighting",
                "power_kw": "0.6",
                "design_current_a": "3",
                "phase": "L1",
                "starter": "drive",
            }
        )
    with pytest.raises(PydanticValidationError, match="starter"):
        LoadInput.model_validate(
            {
                "description": "Lift",
                "load": "lift",
                "power_kw": "5.5",
                "phases": 3,
                "starter": "dol",
            }
        )


def test_a_drive_circuit_cable_needs_an_engineer() -> None:
    request = ProjectDesignRequest.model_validate(
        {
            "info": {"name": "Plant"},
            "boards": [
                {
                    "name": "MCC",
                    "loads": [
                        {
                            "description": "Fan",
                            "load": "motor",
                            "power_kw": "7.5",
                            "phases": 3,
                            "starter": "drive",
                        }
                    ],
                }
            ],
        }
    )
    designed, _ = design_domain.build_design(request)
    found = _codes(designed)["integrity_drive_cable_unchecked"]
    assert found.severity is FindingSeverity.WARNING
    assert found.requires_engineering_confirmation


def test_an_unrated_breaker_is_blocking() -> None:
    project = _designed()
    breaker = _breaker_of(project, "Lights")
    breaker.rated_current_a = None
    found = _codes(project)
    assert found["integrity_device_unrated"].severity is FindingSeverity.BLOCKING
    assert found["integrity_device_unrated"].device_id == breaker.id
    assert found["integrity_protection_missing"].severity is FindingSeverity.BLOCKING


def test_a_circuit_whose_protection_was_removed_is_blocking() -> None:
    project = _designed()
    _circuit(project, "Sockets").device_ids = []
    found = _codes(project)["integrity_protection_missing"]
    assert found.severity is FindingSeverity.BLOCKING


def test_a_direct_on_line_motor_without_its_overload_relay_is_blocking() -> None:
    project = _designed()
    pump = _circuit(project, "Pump")
    relays = {d.id for d in project.boards[0].devices if d.kind is DeviceKind.OVERLOAD_RELAY}
    assert relays & set(pump.device_ids)
    pump.device_ids = [ref for ref in pump.device_ids if ref not in relays]
    found = _codes(project)["integrity_protection_missing"]
    assert found.severity is FindingSeverity.BLOCKING
    assert found.circuit_id == pump.id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rated_current_a", "1e999999"),
        ("rated_current_a", "1e200000000"),
        ("rated_current_a", "0"),
        ("rated_current_a", "10001"),
        ("rated_current_a", "NaN"),
        ("rated_current_a", "Infinity"),
        ("rated_current_a", "16.0000000001"),
        ("breaking_capacity_ka", "201"),
        ("breaking_capacity_ka", "1e999999"),
        ("residual_current_ma", "3001"),
        ("residual_current_ma", "-30"),
    ],
)
def test_a_device_rating_out_of_bounds_is_refused(field: str, value: str) -> None:
    with pytest.raises(PydanticValidationError):
        Device.model_validate({"id": "d", "kind": "circuit_breaker", field: value})


@pytest.mark.parametrize("value", ["1e999999", "1e200000000", "0", "1001", "-2.5", "1.0000001"])
def test_a_cable_section_out_of_bounds_is_refused(value: str) -> None:
    with pytest.raises(PydanticValidationError):
        Cable.model_validate({"id": "w", "cores": 3, "cross_section_mm2": value})


def test_the_bounds_hold_every_value_the_design_tables_give() -> None:
    from app.ai.tools.cable_sizing import _AMPACITY
    from app.design import mccb

    for tables in _AMPACITY.values():
        for table in tables.values():
            for section in table:
                Cable.model_validate({"id": "w", "cores": 4, "cross_section_mm2": section})
    for frame in mccb._FRAMES:
        for rated in frame.ratings_a:
            for _, icu in frame.icu_ka:
                Device.model_validate(
                    {
                        "id": "d",
                        "kind": "circuit_breaker",
                        "rated_current_a": rated,
                        "breaking_capacity_ka": icu,
                    }
                )
    # And every device and cable of a designed project re-validates.
    DesignProject.model_validate(_designed().model_dump(mode="json"))


def test_a_rating_too_large_to_normalise_is_not_a_500() -> None:
    # Set without validation, as a constructed model could be.
    project = _designed()
    breaker = _breaker_of(project, "Lights")
    object.__setattr__(breaker, "rated_current_a", Decimal("1e999999999"))
    found = integrity.integrity_findings(project)
    assert all(len(f.message) < 2000 for f in found)
    assert any(f.severity is FindingSeverity.BLOCKING for f in found)


def test_a_draft_says_its_findings_were_not_rechecked_even_with_its_notes_stripped() -> None:
    project = _tampered("16")
    for board in project.boards:
        board.notes = []
    found = integrity.draft_findings(project)
    (warning,) = [f for f in found if f.code == "draft_findings_not_rechecked"]
    assert warning.severity is FindingSeverity.WARNING
    assert warning.requires_engineering_confirmation
    mark = design_domain.draft_mark(project)
    assert "draft_findings_not_rechecked" in {f.code for f in mark.findings}
    # A saved revision's findings, re-designed on the server, carry no such warning.
    assert "draft_findings_not_rechecked" not in {
        f.code for f in integrity.project_findings(_designed())
    }


def test_a_draft_estimate_carries_the_not_rechecked_warning() -> None:
    from app.models.schemas.auth import CurrentUser
    from app.models.schemas.design import PricingSettings, QuotationRequest

    project = _designed()
    for board in project.boards:
        board.notes = []
    estimate = design_domain.price_design(
        user=CurrentUser(id="u", email="e@x.com", tenant_id="t", roles=frozenset()),
        request=QuotationRequest(project=project, pricing=PricingSettings()),
    )
    assert "draft_findings_not_rechecked" in {f.code for f in estimate.findings}


def test_a_pass_against_the_largest_tabulated_capacity_says_so() -> None:
    project = _designed()
    project.boards[0].notes = [n for n in project.boards[0].notes if n.code != "cable_conditions"]
    found = [
        f for f in integrity.integrity_findings(project) if f.code == "integrity_conditions_unknown"
    ]
    assert found
    assert all(f.severity is FindingSeverity.INFO for f in found)
    # Under known conditions, nothing of the kind.
    given = {"DB1": InstallationConditions()}
    assert "integrity_conditions_unknown" not in {
        f.code for f in integrity.integrity_findings(_designed(), given)
    }


def test_conditions_keyed_by_anything_but_a_board_id_are_a_bug() -> None:
    with pytest.raises(ValueError, match="DB-unknown"):
        integrity.integrity_findings(_designed(), {"DB-unknown": InstallationConditions()})


def test_a_request_board_name_is_its_designed_board_id() -> None:
    # design_projects keys conditions by the request's board name; integrity
    # reads them by board id. The design makes the two the same.
    request = ProjectDesignRequest.model_validate(
        {
            "info": {"name": "Hall"},
            "boards": [
                {
                    "name": "DB-Main",
                    "loads": [{"description": "L", "load": "lighting", "power_kw": "1"}],
                }
            ],
        }
    )
    designed, _ = design_domain.build_design(request)
    assert [b.id for b in designed.boards] == [b.name for b in request.boards]
