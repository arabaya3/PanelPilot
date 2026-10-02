"""Tests for `app/design/markup_suggestions.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.design import designations, markup_suggestions, motors, profile, project
from app.models.schemas.design import (
    DesignProject,
    DistributionBoardRequest,
    LoadInput,
    LoadKind,
    ProjectInfo,
)


def _project() -> DesignProject:
    company = profile.default_profile()
    boards = project.design_boards(
        [
            DistributionBoardRequest(
                name="MDB",
                loads=[
                    LoadInput(description="Server room", load=LoadKind.DATA, power_kw=Decimal(3)),
                    LoadInput(description="Pump", load=LoadKind.OTHER, power_kw=Decimal(4)),
                ],
            ),
            DistributionBoardRequest(
                name="DB1",
                fed_from="MDB",
                loads=[LoadInput(description="Sockets", load=LoadKind.SOCKET, power_kw=Decimal(2))],
            ),
        ],
        company,
    )
    designed = DesignProject(
        info=ProjectInfo(name="T"), boards=boards, parts=motors.parts_for(boards)
    )
    return designations.designate_project(designed, company)


def _label(designed: DesignProject, board: str, description: str) -> str:
    found = next(b for b in designed.boards if b.name == board)
    circuit = next(c for c in found.circuits if c.description == description)
    designation = found.device(circuit.device_ids[0]).designation
    assert designation is not None
    return f"-{designation.product}"


def test_find_circuit_by_any_of_its_labels() -> None:
    designed = _project()
    label = _label(designed, "MDB", "Pump")
    found = markup_suggestions.find_circuit(designed, "MDB", ["16 A", label])
    assert found is not None
    assert found.description == "Pump"
    by_name = markup_suggestions.find_circuit(designed, "MDB", ["Server room"])
    assert by_name is not None
    assert by_name.description == "Server room"
    assert markup_suggestions.find_circuit(designed, "MDB", ["-Z99"]) is None
    assert markup_suggestions.find_circuit(designed, "NOPE", [label]) is None


@pytest.mark.parametrize(
    ("text", "field", "value"),
    [
        ("Please delete this circuit", "remove", None),
        ("احذف هذه الدائرة", "remove", None),
        ("Pump is 5.5 kW per the mech schedule", "power_kw", "5.5"),
        # Arabic-Indic seven, the Arabic decimal separator, five.
        ("القدرة \u0667\u066b\u0665 kW", "power_kw", "7.5"),
        ("cable run is 45 m", "length_m", "45"),
        ("طول 60", "length_m", "60"),
        ("pf 0.85 please", "power_factor", "0.85"),
        ("make it 3 phase", "phases", "3"),
        ("use star-delta", "starter", "star_delta"),
        ("check with the client", None, None),
    ],
)
def test_suggest_reads_one_change(text: str, field: str | None, value: str | None) -> None:
    designed = _project()
    made = markup_suggestions.suggest(designed, "MDB", [_label(designed, "MDB", "Pump")], text)
    if field is None:
        assert made is None
        return
    assert made is not None
    assert (made.board, made.circuit, made.load_index) == ("MDB", "Pump", 1)
    assert (made.field, made.value) == (field, value)


def test_a_feeder_takes_only_a_length_for_its_sub_board() -> None:
    designed = _project()
    feeder = _label(designed, "MDB", "Feeder to DB1")
    made = markup_suggestions.suggest(designed, "MDB", [feeder], "feeder is 80 m long")
    assert made is not None
    assert (made.board, made.load_index, made.field, made.value) == (
        "DB1",
        None,
        "feeder_length_m",
        "80",
    )
    assert markup_suggestions.suggest(designed, "MDB", [feeder], "delete") is None


def test_a_comment_on_no_circuit_suggests_nothing() -> None:
    assert markup_suggestions.suggest(_project(), "MDB", [], "5 kW") is None
