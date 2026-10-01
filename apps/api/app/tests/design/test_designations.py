"""Tests for `app/design/designations.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

from app.design import designations, profile
from app.models.schemas.design import (
    Board,
    Cable,
    Circuit,
    DesignProject,
    Device,
    DeviceKind,
    LoadKind,
    Phase,
    ProjectInfo,
)


def _circuit(i: int, upstream: str) -> Circuit:
    return Circuit(
        id=f"c{i}",
        description=f"Circuit {i}",
        load=LoadKind.LIGHTING,
        power_kw=Decimal(1),
        design_current_a=Decimal(4),
        phase=Phase.L1,
        upstream_id=upstream,
        device_ids=[f"q{i}"],
        cable_id=f"w{i}",
    )


def _board() -> Board:
    # Devices listed out of drawing order on purpose: numbering follows the
    # drawing, not the list.
    return Board(
        id="b",
        name="DB1",
        location="HALL",
        incomer_ids=["main"],
        devices=[
            Device(id="q2", kind=DeviceKind.CIRCUIT_BREAKER),
            Device(id="rcd", kind=DeviceKind.RESIDUAL_CURRENT_DEVICE),
            Device(id="q1", kind=DeviceKind.CIRCUIT_BREAKER),
            Device(id="main", kind=DeviceKind.CIRCUIT_BREAKER),
            Device(id="spd", kind=DeviceKind.SURGE_PROTECTOR),
        ],
        cables=[
            Cable(id="w2", cores=3, cross_section_mm2=Decimal("1.5")),
            Cable(id="w1", cores=3, cross_section_mm2=Decimal("1.5")),
        ],
        circuits=[_circuit(1, "rcd"), _circuit(2, "rcd")],
    )


def test_devices_are_numbered_in_drawing_order() -> None:
    board = designations.designate_board(_board(), profile.default_profile())
    names = {d.id: str(d.designation) for d in board.devices}
    assert names == {
        "main": "=DB1+HALL-Q1",
        "rcd": "=DB1+HALL-F1",
        "q1": "=DB1+HALL-Q2",
        "q2": "=DB1+HALL-Q3",
        # Not on any circuit: numbered after everything drawn.
        "spd": "=DB1+HALL-F2",
    }
    assert {c.id: c.designation.product for c in board.cables if c.designation} == {
        "w1": "W1",
        "w2": "W2",
    }


def test_the_input_is_not_modified() -> None:
    board = _board()
    designations.designate_board(board, profile.default_profile())
    assert all(d.designation is None for d in board.devices)


def test_another_company_gets_its_own_letters_and_numbering() -> None:
    company = profile.load_profile(
        {"key": "acme", "letters": {"residual_current_device": "RCCB-P"}, "start_number": 0}
    )
    board = designations.designate_board(_board(), company)
    assert str(board.device("rcd").designation) == "=DB1+HALL-RCCB-P0"
    assert str(board.device("main").designation) == "=DB1+HALL-Q0"


def test_a_project_is_issued_under_the_profile() -> None:
    project = DesignProject(
        info=ProjectInfo(name="P"),
        boards=[_board(), _board().model_copy(update={"id": "b2", "name": "DB2"})],
    )
    issued = designations.designate_project(project, profile.load_profile({"key": "acme"}))
    assert issued.profile == "acme"
    # Each board numbers from one; the function aspect tells them apart.
    assert str(issued.boards[1].device("main").designation) == "=DB2+HALL-Q1"
    assert project.profile == "iec-default"
