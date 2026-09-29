"""Tests for `app/models/schemas/schematic.py`.

Mirrors the module 1:1 — if you add a function there, add its test here.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from app.ai.tools.din_module_width import ComponentCategory
from app.models.schemas.schematic import (
    CalculatedValue,
    CalcValue,
    DinCategory,
    NotCalculatedValue,
    RefusedValue,
    SchematicRequest,
)


def test_the_din_categories_match_pd002() -> None:
    """Restated rather than imported; this keeps the two from drifting."""
    assert set(get_args(DinCategory)) == {c.value for c in ComponentCategory}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": "calculated", "display": "2 rows", "source": "PD-003"}, CalculatedValue),
        ({"status": "not_calculated", "reason": "r", "blocked_by": "AI-005"}, NotCalculatedValue),
        ({"status": "refused", "reason": "out of range"}, RefusedValue),
    ],
)
def test_a_calculated_quantity_is_one_of_three_explicit_shapes(
    payload: dict[str, Any], expected: type
) -> None:
    assert isinstance(TypeAdapter(CalcValue).validate_python(payload), expected)


def test_a_quantity_cannot_be_merely_absent() -> None:
    """There is no fourth, silent state: an unknown status is rejected."""
    with pytest.raises(PydanticValidationError):
        TypeAdapter(CalcValue).validate_python({"status": "blank"})


def test_a_calculated_value_must_cite_its_source() -> None:
    with pytest.raises(PydanticValidationError):
        TypeAdapter(CalcValue).validate_python({"status": "calculated", "display": "4 mm²"})


@pytest.mark.parametrize("designator", ["", "Q 1", "X" * 17])
def test_a_designator_is_one_printable_token(designator: str) -> None:
    with pytest.raises(PydanticValidationError):
        SchematicRequest.model_validate(
            {"title": "t", "supply": "s", "lines": [{"designator": designator, "kind": "fuse"}]}
        )


def test_an_empty_schedule_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        SchematicRequest.model_validate({"title": "t", "supply": "s", "lines": []})


def test_a_non_positive_rail_length_is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        SchematicRequest.model_validate(
            {
                "title": "t",
                "supply": "s",
                "usable_rail_mm": "0",
                "lines": [{"designator": "Q0", "kind": "fuse"}],
            }
        )
