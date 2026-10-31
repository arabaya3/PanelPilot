"""Tests of ``app.design.model.domain``: plain, frozen, Decimal-only values."""

from __future__ import annotations

import ast
import dataclasses
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.design.model import convert_v1, domain
from app.models.schemas.design import DesignNote, DesignProject


def test_domain_imports_no_framework() -> None:
    source = Path(domain.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported <= {"__future__", "collections.abc", "dataclasses", "decimal"}


def test_domain_holds_decimals_and_no_floats(hall_project: DesignProject) -> None:
    def walk(value: Any) -> None:
        assert not isinstance(value, float)
        if dataclasses.is_dataclass(value):
            for field in dataclasses.fields(value):
                walk(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                walk(item)

    model = convert_v1.from_v1(hall_project)
    walk(model)
    assert isinstance(model.boards[0].supply.voltage_v, Decimal)


def test_domain_is_frozen(hall_project: DesignProject) -> None:
    hall_project.boards[0].notes.append(DesignNote(code="c", params={"a": "b"}, text="t"))
    model = convert_v1.from_v1(hall_project)
    with pytest.raises(dataclasses.FrozenInstanceError):
        model.profile = "other"  # type: ignore[misc]
    with pytest.raises(TypeError):
        model.boards[0].notes[-1].params["x"] = "y"  # type: ignore[index]


def test_values_compare_by_content() -> None:
    a = domain.Designation(function="DB1", location=None, product="Q1")
    assert a == domain.Designation(function="DB1", location=None, product="Q1")
    assert a != dataclasses.replace(a, product="Q2")
