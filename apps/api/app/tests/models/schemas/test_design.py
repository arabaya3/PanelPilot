"""Tests for `app/models/schemas/design.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.design.profile import default_profile
from app.models.schemas.design import (
    Board,
    Cable,
    Circuit,
    CompanyProfile,
    Designation,
    DesignProject,
    Device,
    DeviceKind,
    LoadKind,
    Part,
    Phase,
    ProjectInfo,
)


def _board(**overrides: object) -> Board:
    data: dict[str, object] = {
        "id": "b1",
        "name": "DBG-HALL",
        "incomer_ids": ["main"],
        "devices": [
            Device(id="main", kind=DeviceKind.CIRCUIT_BREAKER, poles=3, part_key="mcb"),
            Device(id="rcd", kind=DeviceKind.RESIDUAL_CURRENT_DEVICE, poles=4),
            Device(id="q1", kind=DeviceKind.CIRCUIT_BREAKER, poles=1),
        ],
        "cables": [Cable(id="c1", cores=3, cross_section_mm2=Decimal("1.5"))],
        "circuits": [
            Circuit(
                id="l1",
                description="Lighting",
                load=LoadKind.LIGHTING,
                power_kw=Decimal("0.5"),
                design_current_a=Decimal("2.2"),
                phase=Phase.L1,
                upstream_id="rcd",
                device_ids=["q1"],
                cable_id="c1",
            )
        ],
    }
    data.update(overrides)
    return Board.model_validate(data)


def _project(board: Board | None = None) -> DesignProject:
    return DesignProject(
        info=ProjectInfo(name="Pocket"),
        boards=[board or _board()],
        parts=[
            Part(key="mcb", manufacturer="ETEK", type_number="EKM6-63X-3C63", description="MCB")
        ],
    )


def test_designation_prints_every_aspect_given() -> None:
    assert str(Designation(function="DB1", location="HALL", product="Q1")) == "=DB1+HALL-Q1"
    assert str(Designation(product="X1")) == "-X1"


def test_a_board_round_trips_through_json() -> None:
    project = _project()
    again = DesignProject.model_validate_json(project.model_dump_json())
    assert again == project


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"incomer_ids": ["nope"]}, "incomer nope is not a device"),
        (
            {
                "devices": [
                    Device(id="main", kind=DeviceKind.CIRCUIT_BREAKER),
                    Device(id="main", kind=DeviceKind.FUSE),
                ]
            },
            "device ids are not unique",
        ),
    ],
)
def test_a_board_refuses_a_reference_to_nothing(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _board(**overrides)


def test_a_circuit_must_name_real_devices_and_cable() -> None:
    circuit = _board().circuits[0].model_copy(update={"cable_id": "c9"})
    with pytest.raises(ValidationError, match="c9 is not a cable"):
        _board(circuits=[circuit])
    circuit = _board().circuits[0].model_copy(update={"upstream_id": "ghost"})
    with pytest.raises(ValidationError, match="ghost is not a device"):
        _board(circuits=[circuit])


def test_board_lookups() -> None:
    board = _board()
    assert board.device("rcd").kind is DeviceKind.RESIDUAL_CURRENT_DEVICE
    assert board.cable("c1").cores == 3
    with pytest.raises(KeyError):
        board.device("nope")
    with pytest.raises(KeyError):
        board.cable("nope")


def test_a_project_refuses_an_unknown_part() -> None:
    board = _board()
    board.devices[1].part_key = "missing"
    with pytest.raises(ValidationError, match="part missing is not in the project"):
        _project(Board.model_validate(board.model_dump()))


def test_project_part_lookup() -> None:
    assert _project().part("mcb").manufacturer == "ETEK"
    with pytest.raises(KeyError):
        _project().part("nope")


def test_a_profile_needs_a_letter_for_every_kind() -> None:
    data = default_profile().model_dump()
    del data["letters"][DeviceKind.FUSE]
    with pytest.raises(ValidationError, match="no letter for"):
        CompanyProfile.model_validate(data)
