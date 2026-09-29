"""Tests for `app/domain/schematics.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.

PD-007's acceptance criterion is a representative full panel design run
through the schema end to end, including how a refused or uncalculated
quantity is represented. That is the first test below; the rest pin the
edge cases the spec names — an unspecified or ambiguous topology is flagged,
never guessed.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import ValidationError
from app.domain import schematics
from app.models.schemas.schematic import (
    CalculatedValue,
    NotCalculatedValue,
    RefusedValue,
    SchematicRequest,
)

#: A small motor-control panel: an isolator feeding a distribution busbar,
#: two motor starters, a VFD branch, a control-supply MCB with a lamp, and
#: the terminal strip. Widths are the sourced PD-002 series.
_MCB = {"category": "mcb", "series": "ABB S200"}
_TB = {"category": "terminal-block", "series": "Wago TOPJOB S 2002-1201"}


def _panel(**overrides: Any) -> SchematicRequest:
    payload: dict[str, Any] = {
        "title": "Pump station MCC-1",
        "supply": "400 V 3~ 50 Hz",
        "usable_rail_mm": "465",
        "lines": [
            {
                "designator": "Q0",
                "kind": "isolator",
                "rating": "63 A",
                "group": "incoming",
                "mounting": "door",
            },
            {
                "designator": "W1",
                "kind": "busbar",
                "group": "incoming",
                "feeds_from": "Q0",
                "mounting": "field",
            },
            {
                "designator": "Q1",
                "kind": "circuit-breaker",
                "rating": "C16",
                "group": "pump 1",
                "feeds_from": "W1",
                "din": {**_MCB, "poles": 3},
            },
            {
                "designator": "K1",
                "kind": "contactor",
                "rating": "AC-3 9 A",
                "group": "pump 1",
                "feeds_from": "Q1",
            },
            {
                "designator": "F1",
                "kind": "overload-relay",
                "rating": "6-9 A",
                "group": "pump 1",
                "feeds_from": "K1",
            },
            {
                "designator": "M1",
                "kind": "motor",
                "rating": "4 kW",
                "group": "pump 1",
                "feeds_from": "F1",
                "mounting": "field",
            },
            {
                "designator": "Q2",
                "kind": "circuit-breaker",
                "rating": "C32",
                "group": "pump 2",
                "feeds_from": "W1",
                "din": {**_MCB, "poles": 3},
            },
            {
                "designator": "U2",
                "kind": "vfd",
                "rating": "7.5 kW",
                "group": "pump 2",
                "feeds_from": "Q2",
                "mounting": "field",
            },
            {
                "designator": "M2",
                "kind": "motor",
                "rating": "7.5 kW",
                "group": "pump 2",
                "feeds_from": "U2",
                "mounting": "field",
            },
            {
                "designator": "Q3",
                "kind": "circuit-breaker",
                "rating": "C6",
                "group": "control",
                "feeds_from": "W1",
                "din": _MCB,
            },
            {
                "designator": "H1",
                "kind": "indicator-lamp",
                "rating": "24 V",
                "group": "control",
                "feeds_from": "Q3",
                "mounting": "door",
            },
            {
                "designator": "X1",
                "kind": "terminal-block",
                "group": "terminals",
                "feeds_from": "F1",
                "din": _TB,
            },
        ],
    }
    payload.update(overrides)
    return SchematicRequest.model_validate(payload)


# --- the acceptance criterion ---------------------------------------------------


def test_a_representative_panel_runs_through_end_to_end() -> None:
    spec = schematics.build_schematic(_panel())

    assert spec.incomer == "Q0"
    assert [c.designator for c in spec.components] == [
        "Q0",
        "W1",
        "Q1",
        "K1",
        "F1",
        "M1",
        "Q2",
        "U2",
        "M2",
        "Q3",
        "H1",
        "X1",
    ]
    # One connection per fed device: the complete topology, nothing inferred.
    assert {(c.upstream, c.downstream) for c in spec.connections} == {
        ("Q0", "W1"),
        ("W1", "Q1"),
        ("Q1", "K1"),
        ("K1", "F1"),
        ("F1", "M1"),
        ("W1", "Q2"),
        ("Q2", "U2"),
        ("U2", "M2"),
        ("W1", "Q3"),
        ("Q3", "H1"),
        ("F1", "X1"),
    }
    assert [g.name for g in spec.groups] == ["incoming", "pump 1", "pump 2", "control", "terminals"]
    assert spec.unknown_kinds == []

    # Every uncalculated quantity is explicit, never merely absent.
    assert all(isinstance(c.conductor, NotCalculatedValue) for c in spec.connections)
    assert isinstance(spec.enclosure, NotCalculatedValue)
    assert spec.trunking == schematics.TRUNKING_NOT_CALCULATED
    assert spec.trunking.blocked_by == "PD-004"


def test_rail_rows_come_from_pd003_with_their_source() -> None:
    spec = schematics.build_schematic(_panel())
    rows = {g.name: g.rail_rows for g in spec.groups}

    # Three-pole S200: 3 x 17.5 = 52.5 mm, one row of 465 mm.
    assert rows["pump 2"] == CalculatedValue(
        display="1 row (52.5 mm of rail)", source=schematics.RAIL_ROWS_SOURCE
    )
    assert rows["terminals"] == CalculatedValue(
        display="1 row (5.2 mm of rail)", source=schematics.RAIL_ROWS_SOURCE
    )
    # The incoming group is door- and field-mounted only.
    assert isinstance(rows["incoming"], CalculatedValue)
    assert rows["incoming"].display.startswith("0 rows")


def test_a_rail_device_without_a_sourced_width_is_named_not_estimated() -> None:
    """K1 is a contactor: PD-002 has no sourced contactor width."""
    spec = schematics.build_schematic(_panel())
    rows = {g.name: g.rail_rows for g in spec.groups}

    assert isinstance(rows["pump 1"], NotCalculatedValue)
    assert "K1" in rows["pump 1"].reason
    assert "F1" in rows["pump 1"].reason
    assert rows["pump 1"].blocked_by == "PD-002"


def test_without_a_rail_length_rows_are_not_calculated() -> None:
    spec = schematics.build_schematic(_panel(usable_rail_mm=None))

    assert all(
        isinstance(g.rail_rows, NotCalculatedValue) or g.name == "incoming" for g in spec.groups
    )


def test_a_tool_refusal_is_carried_as_a_refusal() -> None:
    """A device wider than the rail: PD-003 refuses, and the schema says so."""
    spec = schematics.build_schematic(_panel(usable_rail_mm="40"))
    rows = {g.name: g.rail_rows for g in spec.groups}

    assert isinstance(rows["pump 2"], RefusedValue)
    assert "exceeds" in rows["pump 2"].reason


def test_an_unknown_kind_is_kept_and_reported() -> None:
    lines = _panel().model_dump()["lines"]
    lines[7]["kind"] = "soft-starter"

    spec = schematics.build_schematic(_panel(lines=lines))

    assert spec.unknown_kinds == ["soft-starter"]
    assert "U2" in [c.designator for c in spec.components]


# --- topology is flagged, never guessed -------------------------------------------


def _lines(*edges: tuple[str, str | None]) -> list[dict[str, Any]]:
    return [
        {"designator": d, "kind": "circuit-breaker", "feeds_from": up, "mounting": "door"}
        for d, up in edges
    ]


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ((("Q1", "Q0"), ("Q2", "Q1")), "no incomer"),
        ((("Q0", None), ("Q1", None)), "ambiguous incomer: Q0, Q1"),
        ((("Q0", None), ("Q1", "Q9")), "Q1 is fed from Q9, which is not in the schedule"),
        ((("Q0", None), ("Q0", None)), "Q0 appears more than once"),
        ((("Q0", None), ("Q1", "Q1")), "Q1 is fed from itself"),
        ((("Q0", None), ("Q1", "Q2"), ("Q2", "Q1")), "loop: Q1 -> Q2 -> Q1"),
    ],
    ids=["no-incomer", "two-incomers", "unknown-upstream", "duplicate", "self-fed", "loop"],
)
def test_an_unspecified_topology_is_refused(
    edges: tuple[tuple[str, str | None], ...], message: str
) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        schematics.build_schematic(_panel(lines=_lines(*edges)))


def test_every_topology_problem_is_listed_at_once() -> None:
    """Fix the schedule once, not one error per round trip."""
    with pytest.raises(ValidationError) as raised:
        schematics.build_schematic(_panel(lines=_lines(("Q0", None), ("Q1", None), ("Q2", "Q9"))))

    assert "ambiguous incomer" in str(raised.value)
    assert "Q9" in str(raised.value)


def test_a_loop_is_reported_once_whichever_member_is_walked_first() -> None:
    with pytest.raises(ValidationError) as raised:
        schematics.build_schematic(
            _panel(lines=_lines(("Q0", None), ("A", "B"), ("B", "C"), ("C", "A")))
        )

    assert str(raised.value).count("loop:") == 1


# --- the two lists this module mirrors stay in step ------------------------------


def test_known_kinds_match_the_symbol_library() -> None:
    """The renderer draws placeholders for anything not in its own list."""
    symbols = (
        Path(__file__).resolve().parents[5] / "apps/web/src/components/schematic/symbols.tsx"
    ).read_text(encoding="utf-8")
    block = symbols.split("export const SYMBOL_KINDS")[1].split("];")[0]

    assert set(re.findall(r"'([a-z-]+)'", block)) == schematics.KNOWN_SYMBOL_KINDS


def test_a_calculated_rail_figure_matches_hand_arithmetic() -> None:
    """Two 3-pole S200s and one 1-pole on a 100 mm rail: 122.5 mm -> 2 rows."""
    lines = _lines(("Q0", None))
    lines += [
        {
            "designator": "Q1",
            "kind": "circuit-breaker",
            "feeds_from": "Q0",
            "group": "g",
            "din": {**_MCB, "poles": 3},
        },
        {
            "designator": "Q2",
            "kind": "circuit-breaker",
            "feeds_from": "Q0",
            "group": "g",
            "din": {**_MCB, "poles": 3},
        },
        {
            "designator": "Q3",
            "kind": "circuit-breaker",
            "feeds_from": "Q0",
            "group": "g",
            "din": _MCB,
        },
    ]

    spec = schematics.build_schematic(_panel(lines=lines, usable_rail_mm=Decimal(100)))
    rows = {g.name: g.rail_rows for g in spec.groups}

    assert rows["g"] == CalculatedValue(
        display="2 rows (122.5 mm of rail)", source=schematics.RAIL_ROWS_SOURCE
    )
